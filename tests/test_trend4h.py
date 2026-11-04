"""Tendance 4H paper-trading engine."""
import pytest

from scanner import persist, trend4h
from scanner.config import Config
from scanner.market_data import Candle, MarketDataError

H4 = trend4h.H4_MS
T0 = 1_699_920_000_000  # multiple of 8h, so funding settlements fall on bar boundaries
FLAT = [(100, 101, 99, 100, 100)] * 30
BREAKOUT = (100, 105.5, 100, 105, 300)
LIVE = (105, 105.2, 104.8, 105, 10)


def bars(rows):
    return [Candle(T0 + i * H4, o, h, l, c, v, T0 + (i + 1) * H4 - 1, v * c, 1)
            for i, (o, h, l, c, v) in enumerate(rows)]


def after_close(n_closed):
    """Wall clock just after the n-th candle closed (the next one is in progress)."""
    return T0 + n_closed * H4 + 1


@pytest.fixture()
def params():
    return {
        "enabled": True, "symbols": ["AAAUSDT"], "trendSymbol": "AAAUSDT",
        "channelBars": 5, "trailBars": 3, "atrPeriod": 3, "stopAtr": 2.0, "trendEma": 5,
        "volumeLookback": 3, "volumeMultiple": 2.0, "maxFundingRate": 0.0003,
        "riskPct": 1.0, "maxPositions": 5, "maxLeverage": 5.0, "startCapital": 500.0,
        "takerFeePct": 0.05, "slippagePct": 0.025, "klineLimit": 1000,
    }


def opened(params, funding=None, breakout=BREAKOUT):
    rows = FLAT + [breakout, LIVE]
    return trend4h.step(None, params, {"AAAUSDT": bars(rows)}, funding or {}, after_close(31)), rows


def test_breakout_with_filters_opens_at_next_open(params):
    st, _ = opened(params)
    assert st["equity"] == 500.0 and st["trades"] == []
    (pos,) = st["positions"]
    atr = (2 * 2 + 5.5) / 3  # Wilder ATR(3) on the breakout candle
    assert pos["side"] == "LONG" and pos["entryPrice"] == 105
    assert pos["stop"] == pytest.approx(105 - 2 * atr)
    assert pos["stake"] == pytest.approx(5.0)  # 1 % of 500
    assert st["evaluations"][-1]["taken"] is True


def test_first_run_does_not_backfill_history(params):
    rows = FLAT + [BREAKOUT] + FLAT[:3] + [LIVE]
    st = trend4h.step(None, params, {"AAAUSDT": bars(rows)}, {}, after_close(34))
    assert st["positions"] == [] and st["evaluations"] == []


def test_watch_reports_channel_trend_and_planned_size(params):
    st = trend4h.step(None, params, {"AAAUSDT": bars(FLAT + [LIVE])}, {"AAAUSDT": 0.0001}, after_close(30))
    w = st["watch"]["AAAUSDT"]
    assert (w["channelHigh"], w["channelLow"], w["price"]) == (101, 99, 105)
    assert w["barClose"] == T0 + 30 * H4 and w["fundingRate"] == 0.0001
    assert w["stopDistance"] == pytest.approx(4.0)  # 2 x ATR(3) of 2
    assert w["plannedStake"] == pytest.approx(5.0)
    assert w["plannedNotional"] == pytest.approx(5.0 / 4.0 * 105)
    assert w["plannedLeverage"] == pytest.approx(5.0 / 4.0 * 105 / 500)
    assert len(w["candles"]) == 31 and w["candles"][-1] == [T0 + 30 * H4, 105, 105.2, 104.8, 105]


@pytest.mark.parametrize("change, funding, reason", [
    ({"breakout": (100, 105.5, 100, 105, 150)}, None, "volume"),
    ({}, {"AAAUSDT": 0.001}, "financement"),
])
def test_filters_reject_breakouts(params, change, funding, reason):
    st, _ = opened(params, funding, **change)
    assert st["positions"] == []
    assert any(reason in r for r in st["evaluations"][-1]["reasons"])


def test_breakout_against_trend_symbol_is_rejected(params):
    params["trendSymbol"] = "BBBUSDT"
    down = [(200 - i, 201 - i, 199 - i, 200 - i, 100) for i in range(32)]
    st = trend4h.step(None, params, {"AAAUSDT": bars(FLAT + [BREAKOUT, LIVE]), "BBBUSDT": bars(down)},
                      {}, after_close(31))
    assert st["positions"] == []
    assert any("contre la tendance" in r for r in st["evaluations"][-1]["reasons"])


def test_stop_loses_one_r_plus_costs(params):
    st, rows = opened(params)
    rows = rows[:-1] + [(105, 105.2, 98, 99, 100), LIVE]
    st = trend4h.step(st, params, {"AAAUSDT": bars(rows)}, {}, after_close(32))
    (trade,) = st["trades"]
    assert trade["exitReason"] == "STOP" and trade["rGross"] == pytest.approx(-1.0)
    costs = 0.0015 * 105 / (2 * (2 * 2 + 5.5) / 3)
    assert trade["rNet"] == pytest.approx(-1.0 - costs, abs=1e-4)
    assert st["equity"] == pytest.approx(500 - 5 * (1 + costs), abs=1e-3)
    assert st["positions"] == []


def test_trailing_exit_on_close_below_recent_lows(params):
    st, rows = opened(params)
    path = [(105, 107, 104.5, 106.5, 100), (106.5, 109, 106, 108.5, 100), (108.5, 111, 108, 110.5, 100),
            (110.5, 113, 110, 112.5, 100), (112.5, 112.6, 105.5, 105.8, 100)]
    rows = rows[:-1] + path + [LIVE]
    st = trend4h.step(st, params, {"AAAUSDT": bars(rows)}, {}, after_close(36))
    (trade,) = st["trades"]
    assert trade["exitReason"] == "TRAIL" and trade["exitPrice"] == 105.8
    assert trade["rGross"] > 0


def test_rerun_is_idempotent(params):
    st, rows = opened(params)
    again = trend4h.step(st, params, {"AAAUSDT": bars(rows)}, {}, after_close(31))
    assert again["positions"] == st["positions"] and again["evaluations"] == st["evaluations"]


def test_funding_is_charged_to_longs(params):
    st, rows = opened(params, {"AAAUSDT": 0.0001})
    rows = rows[:-1] + [(105, 106, 104, 105.5, 100), LIVE]
    st = trend4h.step(st, params, {"AAAUSDT": bars(rows)}, {"AAAUSDT": 0.0001}, after_close(32))
    (pos,) = st["positions"]
    assert pos["fundingPaid"] == pytest.approx(0.0001 * pos["qty"] * 105)


def test_max_positions_is_enforced(params):
    params.update(symbols=["AAAUSDT", "CCCUSDT"], maxPositions=1)
    feed = bars(FLAT + [BREAKOUT, LIVE])
    st = trend4h.step(None, params, {"AAAUSDT": feed, "CCCUSDT": feed}, {}, after_close(31))
    assert len(st["positions"]) == 1
    assert "nombre maximal de positions atteint" in st["evaluations"][-1]["reasons"]


def test_config_validation_bounds_risk():
    cfg = Config({"trend4h": {"enabled": True, "riskPct": 10}})
    assert any("riskPct" in e for e in cfg.validate())
    assert Config.load().validate() == []


class FakeClient:
    def __init__(self, feed, fail=()):
        self.feed, self.fail = feed, set(fail)

    def klines(self, symbol, interval, limit):
        if symbol in self.fail:
            raise MarketDataError("down")
        return self.feed[symbol]

    def premium_index(self):
        return [{"symbol": "AAAUSDT", "lastFundingRate": "0.0001"}]


def test_run_persists_and_keeps_state_when_trend_feed_fails(params, tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "front")
    cfg = Config({"trend4h": params})
    feed = {"AAAUSDT": bars(FLAT + [BREAKOUT, LIVE])}
    first = trend4h.run(cfg, FakeClient(feed), after_close(31), log=lambda *_: None)
    assert len(first["positions"]) == 1 and first["lastRun"]["status"] == "ok"
    assert (tmp_path / "front" / trend4h.FILE).read_bytes() == (tmp_path / "data" / trend4h.FILE).read_bytes()
    second = trend4h.run(cfg, FakeClient(feed, fail=["AAAUSDT"]), after_close(32), log=lambda *_: None)
    assert second["positions"] == first["positions"]
    assert second["lastRun"]["status"] == "degraded"
