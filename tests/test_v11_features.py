"""v1.1 feature tests: sessions, risk metrics, Wilson calibration, Monte Carlo,
derivatives context, 1D bias, premium/discount, screener snapshot."""
import copy

import pytest

from scanner.config import Config
from scanner.sessions import session_at, default_kill_zones
from scanner.performance import (compute_performance, wilson_interval,
                                 _max_drawdown_r, _monte_carlo)
from scanner.signals import generate_signals, try_setup

from conftest import make_signal, long_analysis, T0, MS_15M


@pytest.fixture()
def cfg():
    return Config.load()


class TestSessions:
    def test_kill_zone_mapping(self):
        from datetime import datetime, timezone
        kz = default_kill_zones()
        def utc(h, m=0):
            return int(datetime(2026, 8, 30, h, m, tzinfo=timezone.utc).timestamp() * 1000)
        assert session_at(utc(1, 30), kz) == "ASIA"
        assert session_at(utc(8), kz) == "LONDON"
        assert session_at(utc(13), kz) == "NEW_YORK"
        assert session_at(utc(18), kz) == "OFF"

    def test_exact_hours(self):
        from datetime import datetime, timezone
        kz = default_kill_zones()
        def utc(h): return int(datetime(2026, 8, 30, h, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert session_at(utc(2), kz) == "ASIA"
        assert session_at(utc(9), kz) == "LONDON"
        assert session_at(utc(13), kz) == "NEW_YORK"
        assert session_at(utc(17), kz) == "OFF"
        assert session_at(utc(6), kz) == "OFF"   # boundary: end exclusive
        assert session_at(utc(7), kz) == "LONDON"

    def test_wrap_around_window(self):
        kz = [{"name": "SYDNEY", "startUtcHour": 22, "endUtcHour": 4}]
        from datetime import datetime, timezone
        def utc(h): return int(datetime(2026, 8, 30, h, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert session_at(utc(23), kz) == "SYDNEY"
        assert session_at(utc(3), kz) == "SYDNEY"
        assert session_at(utc(12), kz) == "OFF"


class TestRiskMetrics:
    def test_wilson_interval_hand_computed(self):
        lo, hi = wilson_interval(7, 10)  # p=0.7, n=10
        assert lo == pytest.approx(39.7, abs=0.3)
        assert hi == pytest.approx(89.2, abs=0.3)

    def test_wilson_empty_and_ordering(self):
        assert wilson_interval(0, 0) == (None, None)
        lo, hi = wilson_interval(50, 100)
        assert lo == pytest.approx(40.2, abs=0.6) and hi == pytest.approx(59.8, abs=0.6)

    def test_max_drawdown_r(self):
        assert _max_drawdown_r([1.0, 1.0, -2.5, 1.0]) == pytest.approx(2.5)
        assert _max_drawdown_r([1.0, 2.0, 3.0]) == 0.0
        assert _max_drawdown_r([-1.0, -1.0]) == pytest.approx(2.0)

    def test_monte_carlo_deterministic(self):
        rs = [2.8, -1.0, 3.1, -1.0, 2.6, -1.0, -1.0, 2.9]
        a = _monte_carlo(rs)
        b = _monte_carlo(rs)
        assert a == b                      # fixed seed -> identical
        assert a["terminalRp5"] <= a["terminalRp50"] <= a["terminalRp95"]
        assert a["maxDDp50"] <= a["maxDDp95"]

    def test_monte_carlo_needs_sample(self):
        assert _monte_carlo([1.0, -1.0]) is None

    def test_aggregate_has_new_fields(self, cfg):
        book = [
            make_signal(id="S1", status="WIN", rMultiple=3.0, triggeredAt=T0,
                        closedAt=T0 + 6 * MS_15M, entryPrice=110.0),
            make_signal(id="S2", status="LOSS", rMultiple=-1.0, triggeredAt=T0,
                        closedAt=T0 + 2 * MS_15M, entryPrice=111.5),
        ]
        perf = compute_performance(book, cfg, T0 + 99 * MS_15M)
        assert perf["expectancyR"] == pytest.approx(1.0)
        assert perf["maxDrawdownR"] is not None
        assert perf["slippage"]["fills"] == 2
        assert perf["slippage"]["avgPct"] > 0
        assert "scoreCalibration" in perf and "A+" in perf["scoreCalibration"]
        cal = perf["scoreCalibration"]["A+"]
        assert cal["resolved"] == 2 and cal["wins"] == 1
        assert cal["wilson95"]["lower"] <= 50.0 <= cal["wilson95"]["upper"]


class TestSignalContext:
    def test_payload_contains_context_fields(self, cfg):
        analysis, _, now = long_analysis(cfg)
        ctx = {"fundingRatePct": 0.0123, "openInterest": 4567.0, "session": "LONDON"}
        payload, reasons = try_setup("long", analysis, cfg, ctx)
        assert payload is not None, reasons
        assert payload["session"] == "LONDON"
        assert payload["fundingRatePct"] == pytest.approx(0.0123)
        assert payload["openInterest"] == pytest.approx(4567.0)
        assert payload["rangeLabel"] in ("DISCOUNT", "PREMIUM")
        assert 0.0 <= payload["rangePosition"] <= 1.0
        assert payload["fundingExtreme"] is False

    def test_generate_signals_stores_context(self, cfg):
        analysis, _, now = long_analysis(cfg)
        derivs = {analysis["symbol"]: {"fundingRatePct": 0.0123, "openInterest": 4567.0}}
        signals, _ = generate_signals([analysis], cfg, [], now, "test", derivatives=derivs)
        assert signals
        sig = signals[0]
        assert sig["fundingRatePct"] == pytest.approx(0.0123)
        assert sig["openInterest"] == pytest.approx(4567.0)
        assert isinstance(sig["session"], str)
        assert sig["htf1dBias"] == "unknown"          # conftest frames have no 1d
        assert sig["rangeLabel"] in ("DISCOUNT", "PREMIUM", None)

    def test_funding_hard_filter_off_by_default(self, cfg):
        analysis, _, now = long_analysis(cfg)
        ctx = {"fundingRatePct": 0.5, "openInterest": None, "session": "OFF"}  # extreme long
        payload, _ = try_setup("long", analysis, cfg, ctx)
        assert payload is not None                    # context only, no rejection
        assert payload["fundingExtreme"] is True      # flagged for display

    def test_funding_hard_filter_when_enabled(self, cfg):
        analysis, _, now = long_analysis(cfg)
        cfg._data["derivatives"]["hardFilter"] = True
        try:
            ctx = {"fundingRatePct": 0.5, "openInterest": None, "session": "OFF"}
            payload, reasons = try_setup("long", analysis, cfg, ctx)
            assert payload is None
            assert reasons[0]["code"] == "funding_extreme"
            # opposite direction still allowed
            short_ctx = {"fundingRatePct": 0.5, "openInterest": None, "session": "OFF"}
            spayload, _sreasons = try_setup("short", analysis, cfg, short_ctx)
            # SHORT is rejected by HTF rules on this market anyway; funding must not be the reason
            if spayload is None:
                assert "funding" not in _reasons_join(_sreasons)
        finally:
            cfg._data["derivatives"]["hardFilter"] = False

    def test_bias_1d_gate_off_by_default(self, cfg):
        analysis, _, now = long_analysis(cfg)
        analysis["bias1d"] = {"bias": "strong_bearish", "strength": 2}
        payload, reasons = try_setup("long", analysis, cfg)
        assert payload is not None or "1D" not in _reasons_join(reasons)

    def test_bias_1d_gate_when_enabled(self, cfg):
        analysis, _, now = long_analysis(cfg)
        analysis["bias1d"] = {"bias": "strong_bearish", "strength": 2}
        cfg._data["bias1d"]["requireAlignment"] = True
        try:
            payload, reasons = try_setup("long", analysis, cfg)
            assert payload is None and "1D bias" in _reasons_join(reasons)
        finally:
            cfg._data["bias1d"]["requireAlignment"] = False


def _reasons_join(reasons):
    """Rejections are structured records ({code, detail}); join their details."""
    return " ".join(r["detail"] if isinstance(r, dict) else str(r) for r in reasons)


class TestBias1dAnalysis:
    def test_unknown_when_1d_absent(self, cfg):
        analysis, _, _ = long_analysis(cfg)          # conftest frames: no 1d key
        assert analysis["bias1d"]["bias"] == "unknown"

    def test_computed_when_1d_present(self, cfg):
        analysis, frames, now = long_analysis(cfg)
        rows, p = [], 100.0
        # steady daily uptrend, asymmetric wicks -> 1D bullish stack
        for i in range(230):
            c = p + 1.0 if i % 5 < 4 else p - 0.3
            rows.append((p, max(p, c) + 0.5, min(p, c) - 0.15, c, 1000.0))
            p = c
        frames["1d"] = _daily_candles(rows)
        from scanner.analysis import analyze_symbol
        a2 = analyze_symbol("TESTUSDT", frames, cfg, now)
        assert a2["bias1d"]["bias"] in ("bullish", "strong_bullish")


def _daily_candles(rows):
    from conftest import mk
    return [mk(i, o, h, l, c, v, ms=86_400_000) for i, (o, h, l, c, v) in enumerate(rows)]
