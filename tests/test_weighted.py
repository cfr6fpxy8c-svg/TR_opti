"""Weighted proposal mode: gates become points, one live BTC proposal at a time."""
import pytest

from scanner.config import Config
from scanner.risk import build_setup
from scanner.scoring import quality_tier
from scanner.signals import generate_signals, try_setup
from scanner.validation import validate_signal

from conftest import long_analysis, short_analysis, make_signal


@pytest.fixture()
def wcfg():
    cfg = Config.load()
    cfg._data["signalModel"]["mode"] = "weighted"
    cfg._data["weighted"]["standby"] = []
    return cfg


@pytest.fixture()
def scfg():
    return Config.load()


def test_tiers_extend_below_80_only_in_weighted_mode(wcfg, scfg):
    assert quality_tier(92, wcfg) == "A+"
    assert quality_tier(75, wcfg) == "B"
    assert quality_tier(55, wcfg) == "C"
    assert quality_tier(45, wcfg) is None
    assert quality_tier(75, scfg) is None


def test_standby_metric_scores_zero_and_scale_stays_100(wcfg):
    from scanner.scoring import score_setup
    parts = {"bias4h": {"bias": "strong_bullish"}, "bias1h": {"bias": "strong_bullish"},
             "structureEventAgeBars": 2, "additionalBos": True, "displacement": True,
             "sweep": True, "fvg": True, "orderBlock": True, "rsi": 60, "adx": 30,
             "plusDi": 30, "minusDi": 10, "relVolume": 2.5, "atrPercent": 0.5,
             "bias1d": "strong_bullish", "session": "LONDON"}
    full = score_setup("long", parts, 3.5, wcfg)
    assert full["score"] == pytest.approx(100.0)
    wcfg._data["weighted"]["standby"] = ["sweep", "session"]
    reduced = score_setup("long", parts, 3.5, wcfg)
    assert reduced["components"]["liquiditySmc"] == full["components"]["liquiditySmc"] - 10
    assert reduced["score"] == pytest.approx(100.0)
    parts["fvg"] = False
    assert score_setup("long", parts, 3.5, wcfg)["score"] < 100.0


def test_fixed_rr_target_when_no_structure(wcfg, scfg):
    args = ("long", 100.0, 1.0, 100.5, 99.5, 98.5, [])
    setup, _ = build_setup(*args, wcfg)
    assert setup is not None and setup.targetKind == "fixed_rr"
    assert setup.rr == pytest.approx(wcfg.get("weighted.fallbackRr"))
    assert build_setup(*args, scfg)[0] is None


def test_strict_reversal_still_proposed_with_context_component(wcfg):
    analysis, _, _ = long_analysis(wcfg)
    payload, reasons = try_setup("long", analysis, wcfg)
    assert payload is not None, reasons
    assert "context" in payload["components"]
    assert 0 <= payload["score"] <= 100


def test_missing_sweep_costs_points_instead_of_rejecting(wcfg, scfg):
    analysis, _, _ = long_analysis(wcfg)
    full, _ = try_setup("long", analysis, wcfg)
    analysis["frames"]["15m"]["sweeps"] = []
    partial, reasons = try_setup("long", analysis, wcfg)
    assert partial is not None, reasons
    assert partial["liquiditySweep"] is None
    assert partial["score"] < full["score"]
    strict_payload, strict_reasons = try_setup("long", analysis, scfg)
    assert strict_payload is None and strict_reasons[0]["code"] == "no_liquidity_sweep"


def test_only_the_stronger_direction_is_proposed(wcfg):
    analysis, _, now = long_analysis(wcfg)
    signals, rejects = generate_signals([analysis], wcfg, [], now, "test")
    assert len(signals) == 1 and signals[0]["direction"] == "LONG"
    assert not validate_signal(signals[0], wcfg)
    codes = {r["code"] for r in rejects if r["direction"] == "short"}
    assert codes and codes <= {"weaker_direction", "risk_model_rejected",
                               "score_below_threshold", "volatility_out_of_band"}


def test_one_live_proposal_per_symbol(wcfg):
    analysis, _, now = short_analysis(wcfg)
    live = make_signal(symbol="TESTUSDT", direction="LONG", status="WAITING_TRIGGER",
                       generatedAt=now - 10 * 3_600_000)
    signals, rejects = generate_signals([analysis], wcfg, [live], now, "test")
    assert signals == []
    assert {r["code"] for r in rejects} == {"duplicate_active"}


def test_validation_accepts_c_tier_2r_only_in_weighted_mode(wcfg, scfg):
    sig = make_signal(score=55.0, quality="C", triggerPrice=100.0, stopLoss=99.0,
                      takeProfit=102.0, riskReward=2.0)
    assert not validate_signal(sig, wcfg)
    assert validate_signal(sig, scfg)


def test_replay_history_merge_keeps_only_resolved_pre_live_proposals(tmp_path, wcfg, monkeypatch):
    import json
    from scanner import persist
    from scanner.replay import merge_into_history
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
    persist.seed_empty_files()
    live = make_signal(id="LIVE", generatedAt=5_000, dataSource="live")
    persist.write_data_file("signals.json", {"generatedAt": 5_000, "signals": [live]})
    replayed = [make_signal(id="R1", generatedAt=1_000, status="EXPIRED", dataSource="replay"),
                make_signal(id="R2", generatedAt=2_000, status="WAITING_TRIGGER", dataSource="replay"),
                make_signal(id="R3", generatedAt=6_000, status="EXPIRED", dataSource="replay")]
    assert merge_into_history(replayed, wcfg) == 1
    assert merge_into_history(replayed[:1], wcfg) == 1
    ids = [s["id"] for s in json.loads((tmp_path / "data" / "signals.json").read_text())["signals"]]
    assert ids == ["R1", "LIVE"]
    assert (tmp_path / "frontend" / "data" / "performance.json").exists()
