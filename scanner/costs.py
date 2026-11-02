"""Transaction-cost model: taker fees and perpetual funding, expressed in R.

Why this module exists
----------------------
A signal that clears RR >= 2.5 *gross* is not a signal that earns 2.5R. The
entry is a stop order (it crosses the spread -> taker) and the exit is a
stop-market or a limit fill, so every round trip pays fees, and a perpetual
held across a UTC 00:00/08:00/16:00 boundary pays or receives funding.

The engine's R unit is the PLANNED risk, ``|triggerPrice - stopLoss|`` -- the
risk the position size was based on. Costs are converted into that same unit
so gross R and net R are directly comparable, and so the drag is auditable per
signal instead of being invisible inside an aggregate.

    fee_fraction      = entryFeePct + exitFeePct                (fraction of notional)
    funding_fraction  = n_settlements * fundingRate             (long pays positive)
    costR             = (fee_fraction + funding_fraction) * entryPrice / plannedRisk

Nothing is subtracted silently: ``rMultiple`` stays GROSS, and ``costR`` /
``rMultipleNet`` are stored alongside it so a reader can see both.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Binance USDⓈ-M perpetuals settle funding every 8h at 00:00 / 08:00 / 16:00 UTC,
# which is an epoch-aligned interval -- so settlements are found by integer division.
FUNDING_INTERVAL_MS = 8 * 3_600_000


@dataclass(frozen=True)
class CostModel:
    """Immutable fee/funding assumptions. Build once per scan via ``from_cfg``."""

    enabled: bool = True
    entryFeePct: float = 0.05      # percent of notional, one side (taker, VIP0)
    exitFeePct: float = 0.05       # percent of notional, one side (taker, VIP0)
    useFunding: bool = True
    fundingIntervalMs: int = FUNDING_INTERVAL_MS

    @classmethod
    def from_cfg(cls, cfg) -> "CostModel":
        c = cfg.get("costs", {}) or {}
        return cls(
            enabled=bool(c.get("enabled", True)),
            entryFeePct=float(c.get("entryFeePct", 0.05)),
            exitFeePct=float(c.get("exitFeePct", 0.05)),
            useFunding=bool(c.get("useFunding", True)),
            fundingIntervalMs=int(c.get("fundingIntervalMs", FUNDING_INTERVAL_MS)),
        )

    @property
    def round_trip_fee_pct(self) -> float:
        return self.entryFeePct + self.exitFeePct

    def funding_settlements(self, start_ms: int, end_ms: int) -> int:
        """Number of funding settlements in the half-open interval (start, end]."""
        if end_ms is None or start_ms is None or end_ms <= start_ms:
            return 0
        interval = self.fundingIntervalMs or FUNDING_INTERVAL_MS
        first = (start_ms // interval + 1) * interval
        if first > end_ms:
            return 0
        return (end_ms - first) // interval + 1

    @staticmethod
    def planned_risk(sig: dict) -> Optional[float]:
        """|trigger - stop| -- the risk the position size was based on."""
        trigger, sl = sig.get("triggerPrice"), sig.get("stopLoss")
        if trigger is None or sl is None:
            return None
        risk = abs(trigger - sl)
        return risk if risk > 0 else None

    def cost_in_r(self, sig: dict) -> Optional[float]:
        """Round-trip cost expressed in units of planned risk (R).

        Returns ``0.0`` when costing is disabled, ``None`` when it cannot be
        computed (missing levels), otherwise a signed value: fees are always a
        cost, while funding can be income for a short in positive funding.
        """
        if not self.enabled:
            return 0.0
        risk = self.planned_risk(sig)
        entry = sig.get("entryPrice") or sig.get("triggerPrice")
        if risk is None or entry is None or entry <= 0:
            return None

        fee_fraction = self.round_trip_fee_pct / 100.0
        funding_fraction = 0.0
        if self.useFunding:
            rate = sig.get("fundingRatePct")
            start, end = sig.get("triggeredAt"), sig.get("closedAt")
            # `is not None`, not truthiness: 0 is a legitimate epoch timestamp.
            if rate is not None and start is not None and end is not None:
                n = self.funding_settlements(start, end)
                # positive funding: longs pay, shorts receive
                sign = 1.0 if sig.get("direction") == "LONG" else -1.0
                funding_fraction = sign * n * (float(rate) / 100.0)
        return round((fee_fraction + funding_fraction) * entry / risk, 6)


def apply_costs(sig: dict, costs: Optional[CostModel]) -> dict:
    """Attach ``costR`` and ``rMultipleNet`` to a closed/cancelled signal.

    Leaves the signal untouched when no cost model is supplied, so callers that
    do not opt in (older tests, replay without costing) keep gross-only output.
    """
    if costs is None:
        return sig
    cost_r = costs.cost_in_r(sig)
    sig["costR"] = cost_r
    gross = sig.get("rMultiple")
    if cost_r is not None and gross is not None:
        sig["rMultipleNet"] = round(gross - cost_r, 4)
    else:
        sig["rMultipleNet"] = gross
    return sig
