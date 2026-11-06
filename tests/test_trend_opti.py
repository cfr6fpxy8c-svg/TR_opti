"""Third paper portfolio (4H + 1H optimise) -- independent of trend4h / trend_combo."""
import pytest

from scanner import persist, trend4h, trend_combo, trend_opti
from scanner.config import Config
from test_trend_combo import FakeClient, NOW, h1, h4

SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]


def make_cfg(max_same=3):
    return Config({
        "trend4h": {"enabled": True, "symbols": SYMS, "trendSymbol": "AAAUSDT", "channelBars": 5,
                    "trailBars": 3, "atrPeriod": 3, "stopAtr": 2.0, "trendEma": 200, "volumeLookback": 3,
                    "volumeMultiple": 2.0, "maxFundingRate": 0.0003, "riskPct": 1.0, "maxPositions": 5,
                    "maxLeverage": 5.0, "startCapital": 500.0, "takerFeePct": 0.05, "slippagePct": 0.025,
                    "klineLimit": 1000},
        "trendOpti": {"enabled": True, "startCapital": 500.0, "risk4hPct": 2.0, "risk1hPct": 1.0,
                      "maxSameSide": max_same, "trendEma": 5, "h1ChannelBars": 5, "h1TrailBars": 3,
                      "h1StopAtr": 2.0, "h1KlineLimit": 1000},
    })


def test_at_most_n_positions_in_the_same_direction_with_doubled_risk():
    st = trend_opti.combine(None, make_cfg(3), {s: h4() for s in SYMS}, {}, {}, NOW)
    pos = st["book4h"]["positions"]
    assert len(pos) == 3 and all(p["side"] == "LONG" for p in pos)
    assert all(p["stake"] == pytest.approx(10.0) for p in pos)
    rejected = [e for e in st["book4h"]["evaluations"] if not e["taken"]]
    assert [e["symbol"] for e in rejected] == ["DDDUSDT"]
    assert rejected[0]["reasons"] == [trend_opti.side_cap_reason(3)]
    assert st["params"]["trendEma"] == 5 and st["book4h"]["params"]["trendEma"] == 5


def test_1h_reinforcement_counts_towards_the_cap():
    cfg = make_cfg(3)
    st = trend_opti.combine(None, cfg, {"AAAUSDT": h4()}, {"AAAUSDT": h1()}, {}, NOW)
    assert len(st["book1h"]["positions"]) == 1 and st["book1h"]["positions"][0]["stake"] == pytest.approx(5.0)
    full = trend_opti.combine(None, make_cfg(1), {"AAAUSDT": h4()}, {"AAAUSDT": h1()}, {}, NOW)
    assert full["book1h"]["positions"] == []
    assert full["book1h"]["evaluations"][-1]["reasons"] == [trend_opti.side_cap_reason(1)]


def test_1h_still_needs_a_4h_position():
    st = trend_opti.combine(None, make_cfg(), {"AAAUSDT": h4(breakout=False)}, {"AAAUSDT": h1()}, {}, NOW)
    assert st["book1h"]["positions"] == []
    assert trend_combo.GATE_REASON in st["book1h"]["evaluations"][-1]["reasons"]


def test_cap_counts_both_books_and_only_the_same_direction():
    gate = trend_opti.cap_gate({"positions": [{"side": "LONG"}], "pending": [{"side": "LONG"}]}, 3)
    own = {"positions": [{"side": "LONG"}, {"side": "SHORT"}], "pending": []}
    assert gate(own, "X", "LONG", 0) == [trend_opti.side_cap_reason(3)]
    assert gate(own, "X", "SHORT", 0) == []


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "front")
    return tmp_path


def test_run_writes_only_its_own_file(stores):
    cfg = make_cfg()
    st = trend_opti.run(cfg, FakeClient(), NOW, log=lambda *_: None)
    assert st["lastRun"]["status"] == "ok"
    assert (stores / "data" / trend_opti.FILE).exists()
    assert not (stores / "data" / trend4h.FILE).exists() and not (stores / "data" / trend_combo.FILE).exists()
    assert (stores / "front" / trend_opti.FILE).read_bytes() == (stores / "data" / trend_opti.FILE).read_bytes()


def test_opti_config_is_validated():
    errors = Config({"trend4h": {"symbols": ["BTCUSDT"], "klineLimit": 300},
                     "trendOpti": {"enabled": True, "risk4hPct": 9, "maxSameSide": 0, "trendEma": 300}}).validate()
    assert any("risk4hPct" in e for e in errors) and any("maxSameSide" in e for e in errors)
    assert any("trendEma" in e for e in errors)
    assert not [e for e in Config.load().validate() if "trendOpti" in e]
