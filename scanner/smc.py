"""SMC / institutional price-action proxies.

These are *price-action proxies*. The system does not pretend to identify
actual institutional orders; every concept below is a deterministic,
documented candle-pattern definition.

Fair Value Gap (3-candle imbalance model)
    bullish FVG at candle i (evaluated on the completed triple i-2, i-1, i):
        low[i]  > high[i-2]  -> gap zone [high[i-2], low[i]]
    bearish FVG:
        high[i] < low[i-2]   -> gap zone [high[i], low[i-2]]
    The gap must be >= fvgMinGapAtrMultiple * ATR[i] (filters micro-gaps).
    Midpoint = "consequent encroachment" (ICT). The FVG is known only after
    candle i closes -> no lookahead.

Order Block (deterministic definition)
    bullish OB: the most recent *bearish* candle (close < open) within
        obLookback bars immediately before a bullish displacement candle
        whose move also broke structure (a BOS_up event at or within
        obValidationBars after the displacement). Zone = that candle's
        [low, high].
    bearish OB: mirror image before a bearish displacement + BOS_down.
    The OB is usable from the close of the displacement candle. If several
    displacement candles share the same origin, the most recent candle wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .structure import Displacement, StructureEvent


@dataclass
class FVG:
    index: int          # third candle of the triple (gap becomes known at its close)
    direction: str      # 'bullish' | 'bearish'
    bottom: float
    top: float
    size: float
    midpoint: float
    # Index of the first later candle that traded back into the gap (price
    # returned to fill the imbalance), or None while the gap is untouched.
    # A filled gap is spent liquidity: it must not score like a fresh one.
    mitigatedIndex: Optional[int] = None
    # Index of the first later candle that traded fully through the gap.
    invalidatedIndex: Optional[int] = None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def is_fresh(self, at_index: Optional[int] = None) -> bool:
        """True when the gap has not been touched (as of ``at_index``)."""
        for marker in (self.mitigatedIndex, self.invalidatedIndex):
            if marker is not None and (at_index is None or marker <= at_index):
                return False
        return True


def find_fvgs(opens: list[float], highs: list[float], lows: list[float],
              closes: list[float], atr_series: list[Optional[float]],
              min_gap_atr: float = 0.10) -> list[FVG]:
    out: list[FVG] = []
    for i in range(2, len(closes)):
        atr_v = atr_series[i]
        if atr_v is None or atr_v <= 0:
            continue
        # bullish imbalance: candle-3 low leaves a gap above candle-1 high
        if lows[i] > highs[i - 2]:
            gap = lows[i] - highs[i - 2]
            if gap >= min_gap_atr * atr_v:
                out.append(FVG(i, "bullish", highs[i - 2], lows[i], gap, (highs[i - 2] + lows[i]) / 2.0))
        # bearish imbalance: candle-3 high leaves a gap below candle-1 low
        if highs[i] < lows[i - 2]:
            gap = lows[i - 2] - highs[i]
            if gap >= min_gap_atr * atr_v:
                out.append(FVG(i, "bearish", highs[i], lows[i - 2], gap, (highs[i] + lows[i - 2]) / 2.0))
    _mark_fvg_mitigation(out, highs, lows)
    return out


def _mark_fvg_mitigation(gaps: list[FVG], highs: list[float], lows: list[float]) -> None:
    """Record when each gap was first traded back into, and when fully filled.

    Causal by construction: only candles AFTER the gap's own index are
    inspected, and the recorded marker is the index of the candle that did it,
    so a consumer evaluating bar N can ask "was this mitigated by N?" without
    seeing anything later.
    """
    n = len(highs)
    for gap in gaps:
        for j in range(gap.index + 1, n):
            touched = not (lows[j] > gap.top or highs[j] < gap.bottom)
            if touched and gap.mitigatedIndex is None:
                gap.mitigatedIndex = j
            # fully traded through: the whole imbalance has been rebalanced
            if gap.direction == "bullish" and lows[j] <= gap.bottom:
                gap.invalidatedIndex = j
                break
            if gap.direction == "bearish" and highs[j] >= gap.top:
                gap.invalidatedIndex = j
                break


@dataclass
class OrderBlock:
    index: int          # the origin candle
    direction: str      # 'bullish' | 'bearish' (direction it supports)
    bottom: float
    top: float
    displacementIndex: int
    bosIndex: int
    # First candle after the displacement that traded back into the zone
    # (tested), and the first that closed beyond it (broken). An order block
    # price has already revisited or invalidated is not the same evidence as
    # an untested one.
    mitigatedIndex: Optional[int] = None
    invalidatedIndex: Optional[int] = None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def is_fresh(self, at_index: Optional[int] = None) -> bool:
        """True when the zone is untested and unbroken (as of ``at_index``)."""
        for marker in (self.mitigatedIndex, self.invalidatedIndex):
            if marker is not None and (at_index is None or marker <= at_index):
                return False
        return True


def find_order_blocks(opens: list[float], highs: list[float], lows: list[float],
                      closes: list[float],
                      displacements: list[Displacement],
                      structure_events: list[StructureEvent],
                      lookback: int = 10,
                      bos_within_bars: int = 3) -> list[OrderBlock]:
    out: list[OrderBlock] = []
    bos_up_idx = [e.index for e in structure_events if e.type in ("BOS_up", "CHoCH_up")]
    bos_down_idx = [e.index for e in structure_events if e.type in ("BOS_down", "CHoCH_down")]

    for d in displacements:
        # the displacement must be validated by a structure break at/after it
        validated = any(d.index <= b <= d.index + bos_within_bars for b in (bos_up_idx if d.direction == "bullish" else bos_down_idx))
        if not validated:
            continue
        want_bearish = d.direction == "bullish"
        for j in range(d.index - 1, max(-1, d.index - 1 - lookback), -1):
            is_red = closes[j] < opens[j]
            if is_red == want_bearish and closes[j] != opens[j]:
                out.append(OrderBlock(j, d.direction, lows[j], highs[j], d.index, min(
                    (b for b in (bos_up_idx if want_bearish else bos_down_idx) if b >= d.index), default=d.index)))
                break
    # dedupe by origin index, newest first
    seen: set[int] = set()
    unique: list[OrderBlock] = []
    for ob in sorted(out, key=lambda o: -o.index):
        if ob.index not in seen:
            seen.add(ob.index)
            unique.append(ob)
    _mark_ob_mitigation(unique, highs, lows, closes)
    return unique


def _mark_ob_mitigation(blocks: list[OrderBlock], highs: list[float],
                        lows: list[float], closes: list[float]) -> None:
    """Record when each order block was first retested and when it broke.

    Only candles after the *displacement* are considered: the origin candle and
    the displacement itself necessarily overlap the zone, and counting those
    would mark every block mitigated at birth.
    """
    n = len(highs)
    for ob in blocks:
        for j in range(ob.displacementIndex + 1, n):
            touched = not (lows[j] > ob.top or highs[j] < ob.bottom)
            if touched and ob.mitigatedIndex is None:
                ob.mitigatedIndex = j
            # closing beyond the zone against its direction breaks it
            if ob.direction == "bullish" and closes[j] < ob.bottom:
                ob.invalidatedIndex = j
                break
            if ob.direction == "bearish" and closes[j] > ob.top:
                ob.invalidatedIndex = j
                break
