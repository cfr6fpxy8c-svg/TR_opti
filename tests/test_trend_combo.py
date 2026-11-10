"""Second paper portfolio (4H + renfort 1H) and its AI analyst -- independent of trend4h / ai_analyst."""
import json

import pytest

from scanner import ai_combo, persist, trend_combo, trend4h
from scanner.config import Config
from scanner.market_data import Candle

H1, H4 = trend_combo.H1_MS, trend_combo.H4_MS
T0 = 1_699_920_000_000
NOW = T0 + 31 * H4 + 1
FLAT = (100, 101, 99, 100, 100)


def bar(open_ms, step, row):
    o, h, low, c, v = row
    return Candle(open_ms, o, h, low, c, v, open_ms + step - 1, v * c, 1)


def h4(breakout=True):
    rows = [FLAT] * 30 + [(100, 105.5, 100, 105, 300) if breakout else FLAT, (105, 105.2, 104.8, 105, 10)]
    return [bar(T0 + i * H4, H4, r) for i, r in enumerate(rows)]


def h1(breakout=True):
    first = T0 + 31 * H4 - 40 * H1
    rows = [FLAT] * 39 + [(100, 103, 100, 102.5, 300) if breakout else FLAT, (102.5, 102.6, 102.4, 102.5, 10)]
    return [bar(first + i * H1, H1, r) for i, r in enumerate(rows)]


@pytest.fixture()
def cfg():
    return Config({
        "trend4h": {"enabled": True, "symbols": ["AAAUSDT"], "trendSymbol": "AAAUSDT", "channelBars": 5,
                    "trailBars": 3, "atrPeriod": 3, "stopAtr": 2.0, "trendEma": 5, "volumeLookback": 3,
                    "volumeMultiple": 2.0, "maxFundingRate": 0.0003, "riskPct": 1.0, "maxPositions": 5,
                    "maxLeverage": 5.0, "startCapital": 500.0, "takerFeePct": 0.05, "slippagePct": 0.025,
                    "klineLimit": 1000},
        "trendCombo": {"enabled": True, "aiEnabled": True, "startCapital": 500.0, "risk4hPct": 1.0, "risk1hPct": 0.5,
                       "h1ChannelBars": 5, "h1TrailBars": 3, "h1StopAtr": 2.0, "h1KlineLimit": 1000},
        "aiAnalyst": {"enabled": True},
    })


def test_1h_reinforcement_only_during_a_4h_position_on_shared_capital(cfg):
    st = trend_combo.combine(None, cfg, {"AAAUSDT": h4()}, {"AAAUSDT": h1()}, {}, NOW)
    (p4,) = st["book4h"]["positions"]
    (p1,) = st["book1h"]["positions"]
    assert p4["side"] == p1["side"] == "LONG"
    assert p4["stake"] == pytest.approx(5.0) and p1["stake"] == pytest.approx(2.5)
    assert st["book1h"]["evaluations"][-1]["taken"] is True
    assert st["stats"]["openPositions"] == 2 and st["stats"]["equityMarked"] == pytest.approx(500, abs=1)


def test_1h_breakout_without_4h_position_is_rejected(cfg):
    st = trend_combo.combine(None, cfg, {"AAAUSDT": h4(breakout=False)}, {"AAAUSDT": h1()}, {}, NOW)
    assert st["book4h"]["positions"] == [] and st["book1h"]["positions"] == []
    assert trend_combo.GATE_REASON in st["book1h"]["evaluations"][-1]["reasons"]


def test_shared_capital_sizes_on_both_books_realized_pnl(cfg):
    state = trend_combo.combine(None, cfg, {"AAAUSDT": h4()}, {"AAAUSDT": h1()}, {}, NOW)
    state["book4h"]["trades"].append({"symbol": "ZZZUSDT", "side": "LONG", "entryTime": 0, "exitTime": 1,
                                      "pnl": 100.0, "rNet": 2.0})
    st = trend_combo.combine(state, cfg, {"AAAUSDT": h4()}, {"AAAUSDT": h1()}, {}, NOW)
    assert st["equity"] == pytest.approx(600.0) and st["book1h"]["equity"] == pytest.approx(600.0)


def test_leverage_cap_counts_both_books(cfg):
    p4, p1 = trend_combo.book_params(cfg)
    book1h = trend_combo._new_book(p1, NOW, 500.0)
    book1h["positions"].append({"symbol": "ZZZUSDT", "side": "LONG", "entryTime": 0, "stake": 2.5, "notional": 2490.0,
                                "unrealizedR": 0.0, "fundingPaid": 0.0})
    state = {"version": trend_combo.STATE_VERSION, "startedAt": NOW, "startCapital": 500.0, "book4h": None,
             "book1h": book1h, "equityCurve": []}
    st = trend_combo.combine(state, cfg, {"AAAUSDT": h4()}, {"AAAUSDT": h1(breakout=False)}, {}, NOW)
    assert st["book4h"]["positions"] == []
    assert st["book4h"]["evaluations"][-1]["reasons"] == ["levier maximal depasse"]


class FakeClient:
    def klines(self, symbol, interval, limit):
        return h4() if interval == "4h" else h1()

    def premium_index(self):
        return [{"symbol": "AAAUSDT", "lastFundingRate": "0.0001"}]


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "front")
    return tmp_path


def test_run_writes_its_own_file_and_never_the_4h_one(cfg, stores):
    st = trend_combo.run(cfg, FakeClient(), NOW, log=lambda *_: None)
    assert st["lastRun"]["status"] == "ok"
    assert (stores / "data" / trend_combo.FILE).exists()
    assert not (stores / "data" / trend4h.FILE).exists()
    assert (stores / "front" / trend_combo.FILE).read_bytes() == (stores / "data" / trend_combo.FILE).read_bytes()


VIEW = {"macro": "x", "assets": {"AAAUSDT": {
    "direction24h": "UP", "probUp24h": 60, "reason": "r", "risks": "k",
    "breakout4h": {"decision": "GO", "probTarget": 40, "reason": "a"},
    "breakout1h": {"decision": "NO_GO", "probTarget": 30, "reason": "b"},
    "trade": {"action": "NONE", "conviction": 10, "reason": "rien"}}}}


def test_ai_combo_judges_1h_and_4h_breakouts_once_per_close(cfg, stores):
    trend_combo.run(cfg, FakeClient(), NOW, log=lambda *_: None)
    calls = []

    def post(url, token, body, timeout):
        calls.append(body)
        text = "```json\n" + json.dumps(VIEW) + "\n```"
        return {"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"totalTokenCount": 9}}

    env = {"GCP_PROJECT": "opti-trade-513710", "GCP_ACCESS_TOKEN": "t"}
    first = ai_combo.run(cfg, NOW + 60_000, log=lambda *_: None, post=post, env=env)
    again = ai_combo.run(cfg, NOW + 120_000, log=lambda *_: None, post=post, env=env)
    assert len(calls) == 1 and again["lastRun"]["status"] == "up-to-date"
    facts = calls[0]["contents"][0]["parts"][0]["text"]
    assert "breakout1h" in facts and "breakout4h" in facts and "taken" not in facts
    v = first["analyses"][0]["assets"]["AAAUSDT"]
    assert v["breakout4h"]["decision"] == "GO" and v["breakout1h"]["decision"] == "NO_GO"
    assert not (stores / "data" / "ai-analyst.json").exists()


def test_ai_combo_waits_between_4h_closes_without_breakout(cfg):
    combo = trend_combo.combine(None, cfg, {"AAAUSDT": h4(breakout=False)}, {"AAAUSDT": h1(breakout=False)}, {}, NOW)
    assert ai_combo.wants_analysis(combo, T0 + 31 * H4)  # a 4H close always gets an analysis
    assert not ai_combo.wants_analysis(combo, T0 + 31 * H4 - H1)


def test_combo_config_is_validated():
    errors = Config({"trend4h": {"symbols": ["BTCUSDT"]},
                     "trendCombo": {"enabled": True, "risk1hPct": 9, "h1KlineLimit": 50}}).validate()
    assert any("risk1hPct" in e for e in errors) and any("h1KlineLimit" in e for e in errors)
