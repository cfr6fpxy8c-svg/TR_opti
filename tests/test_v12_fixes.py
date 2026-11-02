"""Regressions for the v1.2 audit findings.

Every test here corresponds to a defect that was reproduced on the v1.1 code
before the fix, so each one fails on the old engine and passes on the new one.
"""
import json

import pytest

from conftest import FakeClient, build_frames, long_analysis, make_signal, T0, MS_15M

from scanner import persist
from scanner.analysis import htf_bias, min_adx_for
from scanner.config import Config
from scanner.main import ScanLog, scan_once
import scanner.main as main_module
from scanner.signals import REJECT_STAGES, generate_signals, try_setup
from scanner.smc import find_fvgs, find_order_blocks
from scanner.structure import find_displacements, detect_structure_events, \
    find_swing_highs, find_swing_lows


@pytest.fixture()
def cfg():
    return Config.load()


@pytest.fixture()
def client():
    return FakeClient({"TESTUSDT": build_frames("long")})


class TestDryRunJsonOutput:
    """`--dry-run --json-output` raised NameError on the success path.

    scan_once() only assigned the module-level _STATUS inside the *write*
    block, which a dry run returns before reaching.
    """

    def test_status_is_published_even_on_dry_run(self, tmp_path, cfg, client, monkeypatch):
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()

        code = scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=True)
        assert code == 0
        # must be serialisable -- this is exactly what --json-output does
        payload = json.loads(json.dumps(main_module._STATUS, default=str))
        assert payload["lastScan"]["symbolsScanned"] == 1

    def test_dry_run_writes_nothing(self, tmp_path, cfg, client, monkeypatch):
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()
        before = (tmp_path / "data" / "signals.json").read_text()

        scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=True)
        assert (tmp_path / "data" / "signals.json").read_text() == before


class TestRejectTelemetry:
    """Rejection reasons embedded live floats, so the histogram was unusable."""

    def test_rejections_carry_a_stable_code(self, cfg):
        analysis, _, _ = long_analysis(cfg)
        analysis["relVolume"] = 0.4
        payload, reasons = try_setup("long", analysis, cfg)
        assert payload is None
        assert reasons[0]["code"] == "volume_thin"
        assert reasons[0]["code"] in REJECT_STAGES
        # the human-readable detail is still available
        assert "relVol" in reasons[0]["detail"]

    def test_codes_are_a_closed_set(self, cfg):
        """Reject codes must not vary with market values (bounded cardinality)."""
        codes = set()
        for rel_vol in (0.1, 0.44, 0.83, 1.19):
            analysis, _, _ = long_analysis(cfg)
            analysis["relVolume"] = rel_vol
            _, reasons = try_setup("long", analysis, cfg)
            codes.add(reasons[0]["code"])
        assert codes == {"volume_thin"}, "one gate must yield exactly one code"

    def test_scan_status_exposes_an_ordered_funnel(self, tmp_path, cfg, client, monkeypatch):
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()

        scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=False)
        status = json.loads((tmp_path / "data" / "system-status.json").read_text())
        funnel = status["lastScan"]["rejectFunnel"]
        stages = [row["stage"] for row in funnel]
        assert all(s in REJECT_STAGES for s in stages)
        # the funnel must be in evaluation order, so it reads as a real funnel
        assert stages == sorted(stages, key=REJECT_STAGES.index)

    def test_reject_keys_are_codes_not_formatted_text(self, tmp_path, cfg, client,
                                                      monkeypatch):
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()
        scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=False)
        status = json.loads((tmp_path / "data" / "system-status.json").read_text())
        for key in status["lastScan"]["rejects"]:
            assert key in REJECT_STAGES
            assert not any(ch.isdigit() for ch in key), "no live values in aggregation keys"


class TestPerTimeframeAdx:
    """A single 15M ADX threshold was used to classify 4H regime and 1D bias."""

    def test_thresholds_differ_by_timeframe(self, cfg):
        assert min_adx_for(cfg, "4h") != min_adx_for(cfg, "15m") or \
            cfg.get("signalModel.minAdxByTimeframe.4h") is not None

    def test_falls_back_to_minadx15m_for_unknown_timeframe(self, cfg):
        assert min_adx_for(cfg, "3m") == float(cfg.get("signalModel.minAdx15m"))

    def test_missing_override_falls_back(self):
        cfg = Config.load()
        cfg._data["signalModel"]["minAdxByTimeframe"] = {}
        assert min_adx_for(cfg, "4h") == float(cfg.get("signalModel.minAdx15m"))


class TestHtfBiasDemotion:
    """`if bias == "neutral": bias = "neutral"` demoted nothing (a no-op)."""

    def _stack(self):
        """A plain (not "strong") bullish stack.

        strong_bullish requires close > EMA200 AND EMA50 > EMA200 AND
        EMA20 > EMA50. Keeping EMA50 below EMA200 yields the weaker
        "bullish" classification, which is the one a flat tape should demote.
        """
        # close 100 > ema200 99, ema20 102 > ema50 98, but ema50 < ema200
        return [100.0], [102.0], [98.0], [99.0]

    def test_weak_trend_demotes_directional_bias_to_neutral(self):
        closes, f, m, s = self._stack()
        out = htf_bias(closes, f, m, s, adx_value=5.0, min_adx=20)
        assert out["bias"] == "neutral" and out["strength"] == 0

    def test_strong_trend_keeps_its_bias(self):
        closes, f, m, s = self._stack()
        out = htf_bias(closes, f, m, s, adx_value=35.0, min_adx=20)
        assert out["bias"].endswith("bullish")

    def test_absent_adx_never_demotes(self):
        closes, f, m, s = self._stack()
        out = htf_bias(closes, f, m, s, adx_value=None, min_adx=20)
        assert out["bias"].endswith("bullish")


class TestZoneMitigation:
    """FVG/OB had no mitigation state: a gap filled long ago scored as fresh."""

    def _series(self, rows):
        return ([r[0] for r in rows], [r[1] for r in rows],
                [r[2] for r in rows], [r[3] for r in rows])

    def test_untouched_gap_is_fresh(self):
        # rising gap: candle 2 low (105) above candle 0 high (101); price leaves
        rows = [(100, 101, 99, 100), (102, 108, 101, 107),
                (106, 112, 105, 111), (111, 116, 110, 115), (115, 120, 114, 119)]
        o, h, l, c = self._series(rows)
        gaps = find_fvgs(o, h, l, c, [1.0] * len(c), min_gap_atr=0.1)
        bull = [g for g in gaps if g.direction == "bullish"]
        assert bull and bull[0].is_fresh()
        assert bull[0].mitigatedIndex is None

    def test_gap_traded_back_into_is_marked_mitigated(self):
        rows = [(100, 101, 99, 100), (102, 108, 101, 107),
                (106, 112, 105, 111), (111, 112, 104, 106)]  # wick back into gap
        o, h, l, c = self._series(rows)
        gaps = find_fvgs(o, h, l, c, [1.0] * len(c), min_gap_atr=0.1)
        bull = [g for g in gaps if g.direction == "bullish"]
        assert bull and bull[0].mitigatedIndex == 3
        assert not bull[0].is_fresh()

    def test_freshness_is_evaluated_causally(self):
        rows = [(100, 101, 99, 100), (102, 108, 101, 107),
                (106, 112, 105, 111), (111, 112, 104, 106)]
        o, h, l, c = self._series(rows)
        bull = [g for g in find_fvgs(o, h, l, c, [1.0] * len(c), 0.1)
                if g.direction == "bullish"][0]
        # as of bar 2 the fill (bar 3) has not happened yet
        assert bull.is_fresh(at_index=2) is True
        assert bull.is_fresh(at_index=3) is False

    def test_mitigated_zone_scores_less_than_a_fresh_one(self, cfg):
        from scanner.scoring import score_setup
        base = {"bias4h": {"bias": "strong_bullish"}, "bias1h": {"bias": "strong_bullish"},
                "structureEventAgeBars": 1, "displacement": True, "sweep": True,
                "fvg": True, "orderBlock": True, "rsi": 60, "adx": 30,
                "plusDi": 30, "minusDi": 10, "relVolume": 1.5, "atrPercent": 0.5}
        fresh = score_setup("long", {**base, "fvgFresh": True, "orderBlockFresh": True}, 3.0, cfg)
        stale = score_setup("long", {**base, "fvgFresh": False, "orderBlockFresh": False}, 3.0, cfg)
        assert stale["components"]["liquiditySmc"] < fresh["components"]["liquiditySmc"]
        assert stale["score"] < fresh["score"]

    def test_order_block_break_is_recorded(self):
        rows = [(100, 101, 99, 100)] * 3 + [
            (100, 101, 99, 98),      # 3: bearish origin candle
            (98, 112, 97, 111),      # 4: bullish displacement
            (111, 112, 90, 92),      # 5: closes back below the zone -> broken
        ]
        o, h, l, c = self._series(rows)
        atr = [1.0] * len(c)
        disp = find_displacements(o, c, atr, 1.5)
        events = detect_structure_events(c, find_swing_highs(h, 2), find_swing_lows(l, 2), 40)
        blocks = find_order_blocks(o, h, l, c, disp, events, lookback=10)
        for ob in blocks:
            if ob.direction == "bullish":
                assert ob.invalidatedIndex is not None


class TestBuilderIsolation:
    """The offline/preview builders wrote into the real checkout during tests."""

    def test_builders_honour_injected_paths(self, tmp_path, cfg, client, monkeypatch):
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()
        fe = tmp_path / "frontend"
        fe.mkdir(exist_ok=True)
        (fe / "index.html").write_text(
            '<html><head><link rel="stylesheet" href="styles.css"></head>'
            '<body><script src="app.js"></script></body></html>')
        (fe / "styles.css").write_text("body{color:red}")
        (fe / "app.js").write_text("console.log(1);")

        scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=False)

        assert (fe / "dashboard-offline.html").exists()
        assert (fe / "preview.html").exists()
        # the embedded snapshot must come from the scratch tree, not the repo
        assert "console.log(1);" in (fe / "preview.html").read_text()


class TestCrossExchangeFailover:
    """The "failover chain" was three Binance hostnames -- one source, three
    spellings. A 451 from Binance takes all three down together."""

    def _client(self, responses, endpoints=None):
        """MarketDataClient whose transport is a recorded-response stub."""
        from scanner.market_data import MarketDataClient, MarketDataError

        eps = endpoints or [
            {"name": "binance-futures", "market": "futures",
             "base": "https://fapi.example/fapi/v1"},
            {"name": "bybit-linear", "market": "futures", "dialect": "bybit",
             "root": "https://bybit.example", "base": "https://bybit.example"},
        ]
        client = MarketDataClient(endpoints=eps, timeout=1, max_retries=0, backoff=0)
        calls = []

        def fake_fetch(url):
            calls.append(url)
            for fragment, payload in responses.items():
                if fragment in url:
                    if isinstance(payload, Exception):
                        raise payload
                    return payload
            raise MarketDataError(f"no stub for {url}")

        client._fetch = fake_fetch
        client.calls = calls
        return client

    def test_config_ships_a_non_binance_endpoint(self, cfg):
        names = [e.get("name") for e in cfg.get("dataSource.failoverEndpoints")]
        dialects = {e.get("dialect", "binance") for e in cfg.get("dataSource.failoverEndpoints")}
        assert "bybit-linear" in names
        assert dialects != {"binance"}, "chain must contain a non-Binance venue"

    def test_klines_fail_over_to_bybit_and_are_translated(self):
        from scanner.market_data import MarketDataError
        bybit_kline = {"retCode": 0, "result": {"list": [
            # newest first, as Bybit returns them
            ["1700000900000", "11", "13", "10", "12", "5", "60"],
            ["1700000000000", "1", "3", "0.5", "2", "10", "20"],
        ]}}
        client = self._client({
            "fapi.example": MarketDataError("HTTP 451 geo-restricted"),
            "bybit.example": bybit_kline,
        })
        candles = client.klines("BTCUSDT", "15m", 200)
        assert len(candles) == 2
        # translated into Binance shape AND sorted oldest-first
        assert candles[0].openTime == 1700000000000
        assert candles[0].open == 1.0 and candles[0].high == 3.0
        # closeTime is derived from the interval (Bybit does not send one)
        assert candles[0].closeTime == 1700000000000 + 900_000 - 1

    def test_exchange_info_translation_keeps_only_tradable_perps(self):
        from scanner.market_data import MarketDataError
        payload = {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "status": "Trading", "baseCoin": "BTC",
             "quoteCoin": "USDT", "contractType": "LinearPerpetual",
             "launchTime": "1600000000000"},
            {"symbol": "OLDUSDT", "status": "Closed", "baseCoin": "OLD",
             "quoteCoin": "USDT", "contractType": "LinearPerpetual"},
            {"symbol": "BTC-01JAN", "status": "Trading", "baseCoin": "BTC",
             "quoteCoin": "USDT", "contractType": "LinearFutures"},
        ]}}
        client = self._client({"fapi.example": MarketDataError("451"),
                               "bybit.example": payload})
        info = client.exchange_info()
        symbols = [s["symbol"] for s in info["symbols"]]
        assert symbols == ["BTCUSDT"]           # delisted + dated futures dropped
        assert info["symbols"][0]["contractType"] == "PERPETUAL"
        assert info["symbols"][0]["status"] == "TRADING"

    def test_ticker_translation_exposes_quote_volume(self):
        from scanner.market_data import MarketDataError
        payload = {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "turnover24h": "12345.6", "volume24h": "7",
             "lastPrice": "60000", "fundingRate": "0.0001"}]}}
        client = self._client({"fapi.example": MarketDataError("451"),
                               "bybit.example": payload})
        rows = client.ticker_24h()
        assert rows[0]["symbol"] == "BTCUSDT"
        assert float(rows[0]["quoteVolume"]) == 12345.6
        # funding comes back on the Binance field name the engine reads
        funding = client.premium_index()
        assert float(funding[0]["lastFundingRate"]) == 0.0001

    def test_binance_path_is_untouched_when_it_works(self):
        rows = [[1700000000000, "1", "2", "0.5", "1.5", "10",
                 1700000899999, "15", 3]]
        client = self._client({"fapi.example": rows})
        candles = client.klines("BTCUSDT", "15m", 200)
        assert len(candles) == 1
        assert all("bybit" not in u for u in client.calls), "must not touch Bybit"

    def test_error_names_every_venue_tried(self):
        from scanner.market_data import MarketDataError
        client = self._client({
            "fapi.example": MarketDataError("HTTP 451 geo-restricted"),
            "bybit.example": MarketDataError("connection reset"),
        })
        with pytest.raises(MarketDataError) as excinfo:
            client.klines("BTCUSDT", "15m", 200)
        message = str(excinfo.value)
        assert "binance" in message and "bybit" in message

    def test_unknown_symbol_does_not_fan_out_to_other_venues(self):
        """A deterministic 4xx means the symbol is bad, not the venue."""
        from scanner.market_data import SymbolUnavailableError
        client = self._client({
            "fapi.example": SymbolUnavailableError("HTTP 400: invalid symbol"),
            "bybit.example": {"retCode": 0, "result": {"list": []}},
        })
        with pytest.raises(SymbolUnavailableError):
            client.klines("NOPEUSDT", "15m", 200)
        assert all("bybit" not in u for u in client.calls)


class TestDiagnoseTool:
    """`python -m scanner.diagnose` -- offline answer to "why zero signals?"."""

    ROWS = [
        # survives every indicator gate (long)
        {"symbol": "AAAUSDT", "bias4h": "strong_bullish", "bias1h": "bullish",
         "regime": "TRENDING_UP", "rsi": 60.0, "adx": 25.0, "atrPercent": 0.5,
         "relVolume": 1.5},
        # dies at HTF (4H and 1H oppose each other for both directions)
        {"symbol": "BBBUSDT", "bias4h": "strong_bearish", "bias1h": "strong_bullish",
         "regime": "TRENDING_DOWN", "rsi": 50.0, "adx": 25.0, "atrPercent": 0.5,
         "relVolume": 1.5},
        # aligned but thin volume -> dies at the last gate
        {"symbol": "CCCUSDT", "bias4h": "strong_bullish", "bias1h": "bullish",
         "regime": "TRENDING_UP", "rsi": 60.0, "adx": 25.0, "atrPercent": 0.5,
         "relVolume": 0.4},
    ]

    def test_funnel_is_ordered_and_conserves_population(self, cfg):
        from scanner.diagnose import GATES, funnel
        result = funnel(self.ROWS, cfg)
        assert result["evaluations"] == len(self.ROWS) * 2
        rejected = sum(result["totals"].values())
        assert rejected + len(result["survivors"]) == result["evaluations"]
        assert list(result["totals"]) == [code for code, _ in GATES]

    def test_identifies_the_gate_that_kills_each_symbol(self, cfg):
        from scanner.diagnose import funnel
        result = funnel(self.ROWS, cfg)
        assert "AAAUSDT" in result["byDirection"]["long"]["survivors"]
        assert result["totals"]["relvol"] >= 1        # CCCUSDT
        assert result["totals"]["htf"] >= 1           # BBBUSDT

    def test_relaxing_a_threshold_increases_survivors(self, cfg):
        from scanner.diagnose import funnel, _set_dotted
        from scanner.config import Config
        import copy
        base = len(funnel(self.ROWS, cfg)["survivors"])
        loose = Config(copy.deepcopy(cfg._data))
        _set_dotted(loose, "signalModel.minRelVolume", 0.1)
        assert len(funnel(self.ROWS, loose)["survivors"]) > base

    def test_runs_against_the_committed_snapshot(self, tmp_path):
        """End-to-end through the CLI, using the repo's real snapshot."""
        from scanner.config import repo_root
        from scanner.diagnose import main as diagnose_main
        snapshot = repo_root() / "data" / "universe-snapshot.json"
        if not snapshot.exists():
            pytest.skip("no committed snapshot")
        assert diagnose_main(["--snapshot", str(snapshot)]) == 0

    def test_missing_snapshot_exits_nonzero(self, tmp_path):
        from scanner.diagnose import main as diagnose_main
        assert diagnose_main(["--snapshot", str(tmp_path / "nope.json")]) == 1


class TestDocsConsistency:
    """CI guard: the README must not describe things that do not exist."""

    def test_repo_documentation_is_consistent(self):
        from scanner.check_docs import check
        assert check() == []

    def test_workflows_referenced_by_readme_exist(self):
        """Installed at .github/workflows/, or staged at ops/github-workflows/.

        The agent that opens PRs on this repo cannot write to .github/ without
        the `workflows` OAuth scope, so the files ship staged and are installed
        with a one-line copy. Either location satisfies the claim.
        """
        from scanner.config import repo_root
        root = repo_root()
        for name in ("scanner.yml", "tests.yml"):
            assert ((root / ".github" / "workflows" / name).exists()
                    or (root / "ops" / "github-workflows" / name).exists()), \
                f"{name} missing from both the installed and staged locations"

    def test_scheduler_declares_the_documented_cadence(self):
        from scanner.config import repo_root
        root = repo_root()
        for base in (root / ".github" / "workflows", root / "ops" / "github-workflows"):
            path = base / "scanner.yml"
            if path.exists():
                wf = path.read_text(encoding="utf-8")
                break
        else:
            pytest.fail("scanner.yml not found in either location")
        assert "*/15 * * * *" in wf
        assert "workflow_dispatch" in wf, "needed to recover from skipped cron fires"


class TestValidatorAndMetricHonesty:
    """Fields must mean what their names say."""

    def test_validator_survives_an_untriggered_signal(self, cfg):
        """entryPrice is null until a signal triggers.

        It is deliberately absent from REQUIRED_FIELDS, but validate_signal
        read it with sig["entryPrice"] -- so it raised KeyError on a payload it
        had just declared structurally complete.
        """
        from scanner.validation import REQUIRED_FIELDS, validate_signal
        assert "entryPrice" not in REQUIRED_FIELDS
        sig = make_signal()
        sig.pop("entryPrice", None)
        assert validate_signal(sig, cfg) == []      # must not raise KeyError

    def test_untriggered_signal_is_validated_against_its_trigger(self, cfg):
        from scanner.validation import validate_signal
        sig = make_signal()
        assert sig["entryPrice"] is None
        assert validate_signal(sig, cfg) == []

    def test_monte_carlo_net_is_null_when_costs_were_not_modelled(self, cfg):
        """A *Net field populated from the gross series understates real cost."""
        from scanner.performance import compute_performance
        resolved = []
        for i, r in enumerate((1.5, -1.0, 2.0, -1.0, 1.2, 1.8, -1.0, 2.2)):
            resolved.append(make_signal(
                id=f"SIG-mc{i:04d}",
                status="TP_HIT" if r > 0 else "SL_HIT",
                outcome="WIN" if r > 0 else "LOSS",
                generatedAt=T0 - (20 - i) * MS_15M,
                closedAt=T0 - (19 - i) * MS_15M,
                rMultiple=r,          # gross only: no costR, no rMultipleNet
            ))
        perf = compute_performance(resolved, cfg, T0)
        assert not perf.get("costsModelled")
        if perf.get("monteCarlo"):
            assert perf["monteCarlo"]["basis"] == "gross"
        assert perf.get("monteCarloNet") is None, \
            "monteCarloNet must stay null when there is no net series"


class TestFunnelIsRenderable:
    """The dashboard label map must cover every code the engine can emit.

    A stage the engine emits but the frontend cannot name would render as a
    raw identifier like 'no_fvg_or_order_block' to the user.
    """

    def test_every_reject_stage_has_a_dashboard_label(self):
        import re
        from scanner.config import repo_root
        from scanner.signals import REJECT_STAGES

        app_js = (repo_root() / "frontend" / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const FUNNEL_LABELS = \{(.*?)\n\};", app_js, re.S)
        assert block, "FUNNEL_LABELS map missing from app.js"
        labelled = set(re.findall(r"(\w+):\s*\"", block.group(1)))
        missing = [s for s in REJECT_STAGES if s not in labelled]
        assert not missing, f"stages with no dashboard label: {missing}"

    def test_no_stale_labels_for_codes_the_engine_dropped(self):
        import re
        from scanner.config import repo_root
        from scanner.signals import REJECT_STAGES

        app_js = (repo_root() / "frontend" / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const FUNNEL_LABELS = \{(.*?)\n\};", app_js, re.S)
        labelled = set(re.findall(r"(\w+):\s*\"", block.group(1)))
        orphans = labelled - set(REJECT_STAGES)
        assert not orphans, f"labels for codes the engine no longer emits: {orphans}"
