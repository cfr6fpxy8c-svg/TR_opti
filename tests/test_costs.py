"""P0 fixes: transaction-cost model, symmetric R multiples, cost gate, and the
immutability-guard aliasing regression.

These tests exist because the previous suite passed while the production path
was broken: ``test_outcomes.py`` exercised ``check_immutability`` with two
distinct dicts, but ``main.py`` handed it the SAME objects twice, so the
no-repaint guard silently returned [] forever. TestImmutabilityGuardAliasing
below locks that specific failure mode down through the real scan path.
"""
import json

import pytest

from scanner import main as main_module
from scanner import persist
from scanner.config import Config
from scanner.costs import CostModel, apply_costs
from scanner.main import ScanLog, scan_once
from scanner.outcomes import r_multiple
from scanner.performance import compute_performance
from scanner.risk import build_setup
from scanner.validation import check_immutability, restore_immutable

from conftest import FakeClient, build_frames, make_signal, T0, MS_15M

H8 = 28_800_000  # 8h funding interval


@pytest.fixture()
def cfg():
    return Config.load()


@pytest.fixture()
def client():
    return FakeClient({"TESTUSDT": build_frames("long")})


# --------------------------------------------------------------------- costs
class TestCostModel:
    def test_fees_only_expressed_in_r(self):
        # risk 5.0 on a 100.0 entry = 5% of notional; 0.10% round trip -> 0.02R
        cm = CostModel()
        cost = cm.cost_in_r({"direction": "LONG", "triggerPrice": 100.0,
                             "stopLoss": 95.0, "entryPrice": 100.0})
        assert cost == pytest.approx(0.02, abs=1e-9)

    def test_tight_risk_budget_makes_cost_enormous(self):
        # 0.15% risk budget: 0.10% of notional is two thirds of an entire R.
        # This is the case the old engine happily published as an A+ setup.
        cm = CostModel()
        cost = cm.cost_in_r({"direction": "LONG", "triggerPrice": 100.0,
                             "stopLoss": 99.85, "entryPrice": 100.0})
        assert cost == pytest.approx(0.001 * 100.0 / 0.15, abs=1e-6)
        assert cost > 0.5

    def test_funding_settlements_are_epoch_aligned(self):
        cm = CostModel()
        assert cm.funding_settlements(0, H8 - 1) == 0        # before first boundary
        assert cm.funding_settlements(0, H8) == 1            # exactly on it
        assert cm.funding_settlements(0, 2 * H8) == 2
        assert cm.funding_settlements(H8 + 1, H8 + 2) == 0   # no boundary crossed
        assert cm.funding_settlements(5, 0) == 0             # end before start

    def test_long_pays_positive_funding_short_receives_it(self):
        cm = CostModel(useFunding=True)
        base = {"triggerPrice": 100.0, "stopLoss": 95.0, "entryPrice": 100.0,
                "fundingRatePct": 0.01, "triggeredAt": 0, "closedAt": 2 * H8}
        long_cost = cm.cost_in_r({**base, "direction": "LONG"})
        short_cost = cm.cost_in_r({**base, "direction": "SHORT"})
        assert long_cost == pytest.approx((0.001 + 2 * 0.0001) * 100.0 / 5.0, abs=1e-9)
        assert short_cost == pytest.approx((0.001 - 2 * 0.0001) * 100.0 / 5.0, abs=1e-9)
        assert short_cost < long_cost  # short collects positive funding

    def test_disabled_model_costs_nothing(self):
        cm = CostModel(enabled=False)
        assert cm.cost_in_r({"direction": "LONG", "triggerPrice": 100.0,
                             "stopLoss": 95.0, "entryPrice": 100.0}) == 0.0

    def test_unusable_levels_return_none_not_zero(self):
        cm = CostModel()
        assert cm.cost_in_r({"direction": "LONG", "triggerPrice": 100.0,
                             "stopLoss": 100.0}) is None      # zero risk
        assert cm.cost_in_r({"direction": "LONG"}) is None    # no levels at all

    def test_from_cfg_reads_strategy_json(self, cfg):
        cm = CostModel.from_cfg(cfg)
        assert cm.enabled is True
        assert cm.round_trip_fee_pct == pytest.approx(0.10)
        assert cm.fundingIntervalMs == H8

    def test_apply_costs_attaches_net_without_touching_gross(self):
        sig = {"rMultiple": 2.5}
        apply_costs(sig, CostModel())  # no levels -> costR None
        assert sig["costR"] is None and sig["rMultipleNet"] == 2.5

        sig = {"direction": "LONG", "triggerPrice": 100.0, "stopLoss": 95.0,
               "entryPrice": 100.0, "rMultiple": 2.5}
        apply_costs(sig, CostModel())
        assert sig["costR"] == pytest.approx(0.02, abs=1e-9)
        assert sig["rMultipleNet"] == pytest.approx(2.48, abs=1e-6)
        assert sig["rMultiple"] == 2.5  # gross left intact, never overwritten

    def test_no_cost_model_leaves_signal_untouched(self):
        sig = {"rMultiple": 2.5}
        apply_costs(sig, None)
        assert "costR" not in sig and "rMultipleNet" not in sig


# --------------------------------------------------- P0-3: symmetric R multiples
class TestSymmetricRMultiple:
    def test_clean_fill_reproduces_the_classic_values(self):
        sig = make_signal()  # LONG trigger 110, SL 105, TP 122.5 (RR 2.5)
        sig["entryPrice"] = 110.0
        assert r_multiple(sig, 122.5) == pytest.approx(2.5)
        assert r_multiple(sig, 105.0) == pytest.approx(-1.0)

    def test_bad_gap_fill_is_worse_than_minus_one_r(self):
        """The bug this locks down: losses were hard-coded at -1.0 regardless of
        where the fill actually happened, so worse slippage produced prettier
        statistics."""
        sig = make_signal()
        sig["entryPrice"] = 112.0  # filled 2.0 above the 110.0 trigger
        assert r_multiple(sig, 105.0) == pytest.approx(-1.4)  # (112-105)/5
        assert r_multiple(sig, 122.5) == pytest.approx(2.1)   # (122.5-112)/5

    def test_short_is_the_exact_mirror(self):
        sig = make_signal(direction="SHORT")  # trigger 90, SL 95, TP 77.5
        sig["entryPrice"] = 90.0
        assert r_multiple(sig, 77.5) == pytest.approx(2.5)
        assert r_multiple(sig, 95.0) == pytest.approx(-1.0)
        sig["entryPrice"] = 92.0  # filled 2.0 worse for a short
        assert r_multiple(sig, 95.0) == pytest.approx(-0.6)
        assert r_multiple(sig, 77.5) == pytest.approx(2.9)

    def test_zero_risk_returns_none(self):
        sig = make_signal(stopLoss=110.0)  # trigger == stop
        assert r_multiple(sig, 105.0) is None


# ------------------------------------------------------- P0-4: cost gate
class TestCostGate:
    def test_economic_setup_is_accepted(self, cfg):
        setup, reason = build_setup(
            "long", 100.0, 2.0, 100.0, 99.0, 98.0,
            [(110.0, 5, "swing_high")], cfg)
        assert setup is not None, reason
        assert setup.rr >= cfg.get("risk.minRr")

    def test_uneconomic_setup_is_rejected_not_just_scored_down(self, cfg):
        # 0.35 price of risk on a 100 price = 0.35% -> 0.10% fee is 0.286R,
        # above the 0.25R cap. Confluence quality is irrelevant here.
        setup, reason = build_setup(
            "long", 100.0, 0.5, 100.0, 99.5, 99.95,
            [(112.5, 5, "swing_high")], cfg)
        assert setup is None
        assert "transaction cost too high" in reason

    def test_cap_is_configurable(self):
        relaxed = Config({"costs": {"maxCostR": 5.0}})
        setup, reason = build_setup(
            "long", 100.0, 0.5, 100.0, 99.5, 99.95,
            [(112.5, 5, "swing_high")], relaxed)
        assert setup is not None, reason

    def test_disabling_costs_disables_the_gate(self):
        off = Config({"costs": {"enabled": False}})
        setup, _ = build_setup("long", 100.0, 0.5, 100.0, 99.5, 99.95,
                               [(112.5, 5, "swing_high")], off)
        assert setup is not None


# ------------------------------------------- net metrics in the performance book
class TestNetPerformance:
    def _book(self):
        common = dict(triggeredAt=T0, closedAt=T0 + 3 * MS_15M, generatedAt=T0)
        return [
            make_signal(id="SIG-1", status="WIN", rMultiple=2.5,
                        costR=0.02, rMultipleNet=2.48, **common),
            make_signal(id="SIG-2", status="LOSS", rMultiple=-1.0,
                        costR=0.02, rMultipleNet=-1.02, **common),
            # gross winner, net loser: costs exceed the entire profit
            make_signal(id="SIG-3", status="WIN", rMultiple=0.01,
                        costR=0.05, rMultipleNet=-0.04, **common),
        ]

    def test_gross_and_net_are_both_reported(self, cfg):
        perf = compute_performance(self._book(), cfg, T0 + 100 * MS_15M)
        assert perf["costsModelled"] is True
        assert perf["winRate"] == pytest.approx(66.7, abs=0.1)
        assert perf["winRateNet"] == pytest.approx(33.3, abs=0.1)
        assert perf["expectancyR"] == pytest.approx(0.503, abs=0.01)
        assert perf["expectancyNetR"] == pytest.approx(0.473, abs=0.01)
        assert perf["avgCostR"] == pytest.approx(0.03, abs=1e-6)
        assert perf["totalCostR"] == pytest.approx(0.09, abs=1e-6)
        assert perf["profitFactorNet"] < perf["profitFactor"]

    def test_book_without_cost_fields_falls_back_to_gross(self, cfg):
        """Signals closed before costing existed must not read as zero-cost."""
        book = [make_signal(id="SIG-1", status="WIN", rMultiple=2.5,
                            triggeredAt=T0, closedAt=T0 + MS_15M),
                make_signal(id="SIG-2", status="LOSS", rMultiple=-1.0,
                            triggeredAt=T0, closedAt=T0 + MS_15M)]
        perf = compute_performance(book, cfg, T0 + 100 * MS_15M)
        assert perf["costsModelled"] is False
        assert perf["winRateNet"] is None
        assert perf["expectancyNetR"] is None
        assert perf["avgCostR"] is None

    def test_disclaimer_directs_reader_to_net(self, cfg):
        perf = compute_performance(self._book(), cfg, T0)
        assert "not a probability" in perf["disclaimer"]
        assert "EXCLUDE fees" in perf["disclaimer"]


# --------------------------------- P0-1: immutability guard aliasing regression
class TestImmutabilityGuardAliasing:
    def test_aliased_lists_are_reported_instead_of_silently_passing(self):
        """Passing the same list twice used to return [] -- a guard that could
        never fire. It must now fail loudly."""
        sig = make_signal()
        errors = check_immutability([sig], [sig])
        assert errors and "SAME OBJECT" in errors[0]

    def test_distinct_lists_with_repaint_are_still_caught(self):
        old = [make_signal()]
        new = [dict(old[0], triggerPrice=999.0)]
        errors = check_immutability(old, new)
        assert any("triggerPrice" in e for e in errors)

    def test_restore_immutable_keeps_lifecycle_progress(self):
        old = make_signal()
        new = dict(old, triggerPrice=999.0, status="WIN", outcome="tp_hit",
                   rMultiple=2.5, closedAt=T0 + MS_15M)
        restore_immutable(old, new)
        assert new["triggerPrice"] == 110.0   # repainted field repaired
        assert new["status"] == "WIN"         # legitimate progress preserved
        assert new["rMultiple"] == 2.5

    def test_scan_passes_distinct_objects_to_the_guard(self, tmp_path, cfg, client,
                                                       monkeypatch):
        """End-to-end regression through the REAL scan_once() path.

        The unit tests always passed because they built a fresh dict; this is
        the assertion that was missing -- it inspects the identity relationship
        between the two lists main.py actually hands to check_immutability.
        """
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()
        (tmp_path / "data" / "signals.json").write_text(json.dumps(
            {"generatedAt": 1, "signals": [make_signal(status="WAITING_TRIGGER")]}))

        seen: dict = {}
        real_check = main_module.check_immutability

        def spy(old_signals, new_signals):
            old_ids = {id(s) for s in old_signals}
            seen["pairs"] = len(new_signals)
            seen["aliased"] = any(id(s) in old_ids for s in new_signals)
            return real_check(old_signals, new_signals)

        monkeypatch.setattr(main_module, "check_immutability", spy)
        code = scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"],
                         dry_run=False)
        assert code == 0
        assert seen.get("pairs"), "guard was never reached with any signals"
        assert seen["aliased"] is False, (
            "check_immutability compared aliased objects -> it can never report "
            "a violation")

    def test_scan_records_net_cost_fields_on_closed_signals(self, tmp_path, cfg,
                                                            client, monkeypatch):
        """The scan must thread the cost model through the lifecycle engine."""
        monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
        monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "frontend" / "data")
        persist.seed_empty_files()
        seeded = make_signal(status="TRIGGERED", entryPrice=110.0,
                             triggeredAt=T0 + MS_15M)
        (tmp_path / "data" / "signals.json").write_text(json.dumps(
            {"generatedAt": 1, "signals": [seeded]}))

        scan_once(cfg, ScanLog(), client=client, symbols=["TESTUSDT"], dry_run=False)
        signals = json.loads((tmp_path / "data" / "signals.json").read_text())["signals"]
        closed = [s for s in signals if s.get("rMultiple") is not None]
        assert closed, "no signal resolved, so costing was never exercised"
        for s in closed:
            assert "costR" in s and "rMultipleNet" in s
            assert s["costR"] is not None
            assert s["rMultipleNet"] == pytest.approx(s["rMultiple"] - s["costR"], abs=1e-6)
            assert s["rMultipleNet"] < s["rMultiple"]  # costs always reduce

        perf = json.loads((tmp_path / "data" / "performance.json").read_text())
        assert perf["costsModelled"] is True
        assert perf["avgCostR"] > 0


# ------------------------------------------------- P0-5: block bootstrap paths
from scanner.performance import _monte_carlo  # noqa: E402


class TestBlockBootstrap:
    # correlated book: three winners then three losers, repeating
    CLUSTERED = [2.0, 2.0, 2.0, -1.0, -1.0, -1.0] * 4  # n = 24

    def test_block_one_is_the_independent_bootstrap(self):
        mc = _monte_carlo(self.CLUSTERED, block=1)
        assert mc["blockLength"] == 1 and mc["sampling"] == "moving-block"

    def test_auto_block_length_scales_with_sample(self):
        assert _monte_carlo(self.CLUSTERED)["blockLength"] == 3       # round(24**1/3)
        assert _monte_carlo([1.0] * 5)["blockLength"] >= 1            # never zero
        assert _monte_carlo([1.0] * 1000)["blockLength"] <= 500       # capped at n//2

    def test_blocks_preserve_loss_clustering_that_a_shuffle_destroys(self):
        """The defect this fixes: resampling single trades spreads correlated
        losses apart, so maxDDp95 -- the number a reader acts on -- is too low."""
        indep = _monte_carlo(self.CLUSTERED, block=1)
        blocks = _monte_carlo(self.CLUSTERED)
        assert blocks["maxDDp95"] > indep["maxDDp95"]
        assert blocks["maxDDp50"] >= indep["maxDDp50"]

    def test_still_deterministic_and_ordered(self):
        a, b = _monte_carlo(self.CLUSTERED), _monte_carlo(self.CLUSTERED)
        assert a == b
        assert a["terminalRp5"] <= a["terminalRp50"] <= a["terminalRp95"]
        assert a["maxDDp50"] <= a["maxDDp95"]

    def test_explicit_block_override_is_respected(self, cfg):
        assert _monte_carlo(self.CLUSTERED, block=6)["blockLength"] == 6

    def test_paths_use_net_basis_when_costs_were_modelled(self, cfg):
        common = dict(triggeredAt=T0, closedAt=T0 + 3 * MS_15M, generatedAt=T0)
        book = [make_signal(id=f"SIG-{i}", status="WIN" if i % 2 else "LOSS",
                            rMultiple=2.5 if i % 2 else -1.0, costR=0.02,
                            rMultipleNet=2.48 if i % 2 else -1.02, **common)
                for i in range(12)]
        perf = compute_performance(book, cfg, T0 + 99 * MS_15M)
        assert perf["costsModelled"] is True
        assert perf["monteCarlo"]["basis"] == "net"
        assert perf["monteCarlo"]["sampling"] == "moving-block"

    def test_paths_fall_back_to_gross_without_costs(self, cfg):
        common = dict(triggeredAt=T0, closedAt=T0 + 3 * MS_15M, generatedAt=T0)
        book = [make_signal(id=f"SIG-{i}", status="WIN" if i % 2 else "LOSS",
                            rMultiple=2.5 if i % 2 else -1.0, **common)
                for i in range(12)]
        perf = compute_performance(book, cfg, T0 + 99 * MS_15M)
        assert perf["costsModelled"] is False
        assert perf["monteCarlo"]["basis"] == "gross"

    def test_net_paths_show_a_worse_tail_than_gross(self, cfg):
        """Costs plus clustering must compound, not cancel out."""
        common = dict(triggeredAt=T0, closedAt=T0 + 3 * MS_15M, generatedAt=T0)
        book = [make_signal(id=f"SIG-{i}", status="WIN" if i % 2 else "LOSS",
                            rMultiple=2.5 if i % 2 else -1.0, costR=0.05,
                            rMultipleNet=2.45 if i % 2 else -1.05, **common)
                for i in range(12)]
        perf = compute_performance(book, cfg, T0 + 99 * MS_15M)
        gross = _monte_carlo([2.5 if i % 2 else -1.0 for i in range(12)])
        assert perf["monteCarlo"]["maxDDp95"] >= gross["maxDDp95"]
