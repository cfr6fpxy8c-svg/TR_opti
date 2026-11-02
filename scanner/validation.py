"""Data-integrity validation (spec §31, §33).

No invalid signal may reach the dashboard, and historical signals must never
be repainted: every immutable field of an existing signal must be identical
after an update pass.
"""
from __future__ import annotations

import math

from .signals import ACTIVE_STATUSES, WIN, LOSS, TRIGGERED

IMMUTABLE_FIELDS = (
    "id", "symbol", "direction", "timeframe", "generatedAt", "signalCandleCloseTime",
    "triggerPrice", "entryZone", "stopLoss", "takeProfit", "riskReward",
    "marketRegime", "htf4hBias", "htf1hBias", "expiryAt", "tradeMaxDurationMs",
)
REQUIRED_FIELDS = (
    "id", "symbol", "direction", "timeframe", "quality", "score", "generatedAt",
    "triggerPrice", "entryZone", "currentPrice", "stopLoss", "takeProfit",
    "riskReward", "marketRegime", "htf4hBias", "htf1hBias", "structure",
    "liquiditySweep", "bos", "choch", "displacement", "fvg", "orderBlock",
    "rsi", "adx", "atr", "relativeVolume", "status", "expiryAt",
)


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def validate_signal(sig: dict, cfg) -> list[str]:
    errors: list[str] = []
    missing = [f for f in REQUIRED_FIELDS if f not in sig]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]

    if not isinstance(sig["symbol"], str) or not sig["symbol"].endswith("USDT"):
        errors.append("symbol invalid")
    if sig["direction"] not in ("LONG", "SHORT"):
        errors.append("direction invalid")
    if not _finite(sig["triggerPrice"], sig["stopLoss"], sig["takeProfit"], sig["score"]):
        errors.append("non-finite prices/score")
        return errors
    if sig["stopLoss"] == sig["takeProfit"]:
        errors.append("SL == TP")
    # entryPrice is deliberately NOT in REQUIRED_FIELDS: it is null until the
    # signal triggers. Read it defensively so the validator cannot raise
    # KeyError on a payload it has already declared structurally complete.
    entry = sig.get("entryPrice") or sig["triggerPrice"]
    if sig["direction"] == "LONG" and not (sig["stopLoss"] < entry < sig["takeProfit"]):
        errors.append("LONG requires SL < entry < TP")
    if sig["direction"] == "SHORT" and not (sig["takeProfit"] < entry < sig["stopLoss"]):
        errors.append("SHORT requires TP < entry < SL")
    if not (0 <= sig["score"] <= 100):
        errors.append("score outside 0..100")
    thresholds = cfg.get("scoring", {})
    expected = ("A+" if sig["score"] >= thresholds.get("aPlusThreshold", 90)
                else "A" if sig["score"] >= thresholds.get("aThreshold", 85)
                else "B+" if sig["score"] >= thresholds.get("bPlusThreshold", 80) else None)
    if expected is None:
        errors.append(f"score {sig['score']} below publishable threshold")
    elif sig["quality"] != expected:
        errors.append(f"quality {sig['quality']} != expected {expected} for score {sig['score']}")
    if sig["riskReward"] < cfg.get("risk.minRr", 2.5):
        errors.append(f"RR {sig['riskReward']} below minimum")
    # RR math must be self-consistent
    entry_ref = sig["triggerPrice"]
    risk = (entry_ref - sig["stopLoss"]) if sig["direction"] == "LONG" else (sig["stopLoss"] - entry_ref)
    reward = (sig["takeProfit"] - entry_ref) if sig["direction"] == "LONG" else (entry_ref - sig["takeProfit"])
    if risk > 0 and reward > 0:
        calc = reward / risk
    else:
        calc = None
    if calc is None or abs(calc - sig["riskReward"]) > 0.02:
        errors.append(f"RR inconsistent (stated {sig['riskReward']}, computed {calc})")
    for ind in ("rsi", "adx", "atr", "relativeVolume"):
        if sig.get(ind) is None:
            errors.append(f"indicator {ind} missing")
    if not isinstance(sig.get("generatedAt"), int) or sig["generatedAt"] <= 0:
        errors.append("generatedAt invalid")
    if sig["status"] == TRIGGERED and not sig.get("entryPrice"):
        errors.append("triggered signal without entryPrice")
    if sig["status"] in (WIN, LOSS) and sig.get("rMultiple") is None:
        errors.append("resolved signal without rMultiple")
    return errors


def validate_signals_payload(payload: dict, cfg, now_ms: int,
                             max_data_age_ms: int = 30 * 60_000) -> list[str]:
    errors: list[str] = []
    signals = payload.get("signals", [])
    ids: set[str] = set()
    active_by_key: dict[tuple[str, str], int] = {}
    for i, sig in enumerate(signals):
        for e in validate_signal(sig, cfg):
            errors.append(f"signals[{i}] ({sig.get('id', '?')}): {e}")
        if sig.get("id") in ids:
            errors.append(f"duplicate id {sig.get('id')}")
        ids.add(sig.get("id"))
        if sig.get("status") in ACTIVE_STATUSES:
            key = (sig.get("symbol"), sig.get("direction"))
            active_by_key[key] = active_by_key.get(key, 0) + 1
    for key, count in active_by_key.items():
        if count > 1:
            errors.append(f"multiple active signals for {key}")
    return errors


def restore_immutable(old: dict, new: dict) -> dict:
    """Repair ``new`` in place by copying back the immutable fields from ``old``.

    Only the frozen fields are restored: a signal that legitimately progressed
    (e.g. TRIGGERED -> WIN) keeps its new status, outcome and timestamps.
    Replacing the whole record with the old one -- the previous behaviour --
    would have resurrected closed signals and discarded valid lifecycle
    updates, which is worse than the violation it was meant to correct.
    """
    for field in IMMUTABLE_FIELDS:
        if field in old:
            new[field] = old[field]
    return new


def check_immutability(old_signals: list[dict], new_signals: list[dict]) -> list[str]:
    """Historical parameters must never be rewritten (no repainting, §33)."""
    errors: list[str] = []
    old_by_id = {s["id"]: s for s in old_signals}
    for new in new_signals:
        old = old_by_id.get(new["id"])
        if old is None:
            continue
        if old is new:
            # Aliased input: the caller compared a signal with itself, so no
            # violation could ever be reported. Fail loudly instead of silently
            # returning [] -- this is exactly how the guard went unnoticed.
            errors.append(f"{new['id']}: old and new are the SAME OBJECT -- the "
                          "caller passed an aliased list, immutability is unverifiable")
            continue
        for field in IMMUTABLE_FIELDS:
            if field in old and field in new and old[field] != new[field]:
                errors.append(f"{new['id']}: immutable field {field} changed "
                              f"({old[field]!r} -> {new[field]!r})")
    return errors
