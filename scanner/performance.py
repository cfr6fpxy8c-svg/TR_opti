"""Performance engine (spec §21-22).

Win Rate = Wins / (Wins + Losses). WAITING / EXPIRED / AMBIGUOUS / CANCELLED
are NEVER in the denominator, and every displayed metric carries its sample
size. Profit factor = sum(R of wins) / |sum(R of losses)|.

Every metric is reported twice: GROSS (``winRate``, ``expectancyR``,
``profitFactor``, ``sharpeR``, ``monteCarlo``) and NET of fees + funding
(``winRateNet``, ``expectancyNetR``, ``profitFactorNet``, ``avgCostR``). Gross
is kept for continuity and for diagnosing the setup quality itself; net is the
number that describes what the system would actually have earned. A stop-out
is -1R only when the fill matched the trigger -- gap fills are charged their
true distance (see ``outcomes.r_multiple``).
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

from .signals import (WAITING_TRIGGER, TRIGGERED, WIN, LOSS, EXPIRED,
                      AMBIGUOUS, CANCELLED)


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 1) if denominator else None



def wilson_interval(wins: int, n: int, z: float = 1.96):
    # 95% Wilson score interval for a binomial proportion (win rate)
    if n <= 0:
        return None, None
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(100 * (centre - margin) / denom, 1), round(100 * (centre + margin) / denom, 1)


def _max_drawdown_r(r_sequence):
    # peak-to-trough drawdown of the cumulative R equity curve
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in r_sequence:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 3)


def _monte_carlo(r_sequence, paths=500, seed=1337, block=None):
    """Deterministic moving-block bootstrap over resolved R multiples.

    Contiguous BLOCKS are resampled rather than individual trades, so losing
    streaks survive the shuffle. An independent bootstrap assumes trades are
    unrelated, which they demonstrably are not in crypto: one BTC move takes
    most of a correlated book with it. Independent resampling therefore
    understates tail drawdown, and maxDDp95 is exactly the number a reader
    would act on.

    Block length defaults to ``round(n ** 1/3)`` capped at ``n // 2`` -- long
    enough to preserve local clustering, short enough that the path still
    mixes. Deterministic: fixed seed, no other engine randomness.
    """
    n = len(r_sequence)
    if n < 5:
        return None
    rng = random.Random(seed)  # fixed seed -> reproducible
    if block is None:
        block = max(1, min(max(1, n // 2), round(n ** (1.0 / 3.0))))
    block = max(1, min(block, n))
    starts = list(range(n - block + 1)) or [0]

    terminals, drawdowns = [], []
    for _ in range(paths):
        equity, peak, dd, drawn = 0.0, 0.0, 0.0, 0
        while drawn < n:
            s = rng.choice(starts)
            for k in range(min(block, n - drawn)):
                equity += r_sequence[s + k]
                peak = max(peak, equity)
                dd = max(dd, peak - equity)
                drawn += 1
        terminals.append(equity)
        drawdowns.append(dd)

    def pct(values, q):
        s = sorted(values)
        idx = min(len(s) - 1, int(q * len(s)))
        return round(s[idx], 2)

    return {
        "paths": paths, "tradesPerPath": n, "blockLength": block,
        "sampling": "moving-block",
        "terminalRp5": pct(terminals, 0.05), "terminalRp50": pct(terminals, 0.50),
        "terminalRp95": pct(terminals, 0.95),
        "maxDDp50": pct(drawdowns, 0.50), "maxDDp95": pct(drawdowns, 0.95),
        "note": "moving-block bootstrap of historical R multiples (deterministic "
                "seed); blocks preserve loss clustering that an independent "
                "shuffle would destroy. Describes the PAST distribution, not a "
                "prediction",
    }


def _streaks(results: list[str]) -> tuple[int, int]:
    max_w = max_l = cur_w = cur_l = 0
    for r in results:
        if r == WIN:
            cur_w += 1
            cur_l = 0
        elif r == LOSS:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def _duration_ms(sig: dict, from_ms: int, to_ms: int) -> int | None:
    start = sig.get(from_ms)
    end = sig.get(to_ms)
    if start and end and end >= start:
        return end - start
    return None


def _bucket_by_score(score: float, cfg) -> str:
    s = cfg.get("scoring", {})
    if score >= s.get("aPlusThreshold", 90):
        return "A+"
    if score >= s.get("aThreshold", 85):
        return "A"
    if score >= s.get("bPlusThreshold", 80):
        return "B+"
    return "below B+"


def _agg(items: list[dict]) -> dict:
    wins = [s for s in items if s["status"] == WIN]
    losses = [s for s in items if s["status"] == LOSS]
    r_wins = [s.get("rMultiple") for s in wins if s.get("rMultiple") is not None]
    r_losses = [s.get("rMultiple") for s in losses if s.get("rMultiple") is not None]
    gross_win = sum(r_wins)
    gross_loss = abs(sum(r_losses))
    tp_times = [s["closedAt"] - s["triggeredAt"] for s in wins
                if s.get("triggeredAt") and s.get("closedAt")]
    sl_times = [s["closedAt"] - s["triggeredAt"] for s in losses
                if s.get("triggeredAt") and s.get("closedAt")]
    resolved_rs = [s.get("rMultiple") for s in items if s["status"] in (WIN, LOSS)
                   and s.get("rMultiple") is not None]
    expectancy = round(sum(resolved_rs) / len(resolved_rs), 3) if resolved_rs else None
    std_r = (round(math.sqrt(sum((r - expectancy) ** 2 for r in resolved_rs) /
                            len(resolved_rs)), 3) if resolved_rs else None)
    sharpe_r = round(expectancy / std_r, 3) if (expectancy is not None and std_r and std_r > 0) else None
    slips = []
    for s in items:
        if s.get("entryPrice") and s.get("triggerPrice") and s["triggerPrice"] > 0:
            slips.append(abs(s["entryPrice"] - s["triggerPrice"]) / s["triggerPrice"] * 100.0)
    slippage = {"avgPct": round(sum(slips) / len(slips), 4) if slips else None,
                "maxPct": round(max(slips), 4) if slips else None,
                "fills": len(slips)}

    # ---- net-of-cost view -------------------------------------------------
    # costR is written by the lifecycle engine (see costs.py). Signals closed
    # before costing existed simply have no costR and fall back to gross, so
    # historical books stay readable instead of silently reading as zero-cost.
    resolved = [s for s in items if s["status"] in (WIN, LOSS)]

    def _net(s):
        return s.get("rMultipleNet") if s.get("rMultipleNet") is not None else s.get("rMultiple")

    net_rs = [r for r in (_net(s) for s in resolved) if r is not None]
    net_wins = sum(1 for r in net_rs if r > 0)
    net_losses = sum(1 for r in net_rs if r <= 0)
    net_gross_win = sum(r for r in net_rs if r > 0)
    net_gross_loss = abs(sum(r for r in net_rs if r <= 0))
    cost_rs = [s.get("costR") for s in resolved if s.get("costR") is not None]
    costs_modelled = len(cost_rs) > 0

    return {
        "total": len(items),
        "waiting": sum(1 for s in items if s["status"] == WAITING_TRIGGER),
        "triggered": sum(1 for s in items if s["status"] in (TRIGGERED, WIN, LOSS, AMBIGUOUS, CANCELLED)),
        "wins": len(wins),
        "losses": len(losses),
        "expired": sum(1 for s in items if s["status"] == EXPIRED),
        "ambiguous": sum(1 for s in items if s["status"] == AMBIGUOUS),
        "cancelled": sum(1 for s in items if s["status"] == CANCELLED),
        "winRate": _pct(len(wins), len(wins) + len(losses)),
        "resolvedTrades": len(wins) + len(losses),
        "avgRr": round(sum(s["riskReward"] for s in items) / len(items), 3) if items else None,
        "avgWinR": round(gross_win / len(r_wins), 3) if r_wins else None,
        "profitFactor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,  # undefined until a loss occurs
        "avgScore": round(sum(s["score"] for s in items) / len(items), 1) if items else None,
        "avgTimeToTpMs": round(sum(tp_times) / len(tp_times)) if tp_times else None,
        "avgTimeToSlMs": round(sum(sl_times) / len(sl_times)) if sl_times else None,
        "expectancyR": expectancy,
        "sharpeR": sharpe_r,
        "slippage": slippage,
        # net of fees + funding. Reported ALONGSIDE gross, never instead of it.
        "costsModelled": costs_modelled,
        "avgCostR": round(sum(cost_rs) / len(cost_rs), 4) if cost_rs else None,
        "totalCostR": round(sum(cost_rs), 3) if cost_rs else None,
        "netWins": net_wins if costs_modelled else None,
        "netLosses": net_losses if costs_modelled else None,
        "winRateNet": _pct(net_wins, net_wins + net_losses) if costs_modelled else None,
        "expectancyNetR": round(sum(net_rs) / len(net_rs), 3) if (net_rs and costs_modelled) else None,
        "profitFactorNet": (round(net_gross_win / net_gross_loss, 3)
                            if (net_gross_loss > 0 and costs_modelled) else None),
    }


def compute_performance(signals: list[dict], cfg, now_ms: int,
                        since_ms: int | None = None,
                        until_ms: int | None = None) -> dict:
    """Aggregate metrics; optional [since, until] window on generatedAt."""
    window = [s for s in signals
              if (since_ms is None or s.get("generatedAt", 0) >= since_ms)
              and (until_ms is None or s.get("generatedAt", 0) <= until_ms)]

    base = _agg(window)
    resolved = [s for s in window if s["status"] in (WIN, LOSS)]
    resolved.sort(key=lambda s: s.get("closedAt") or s.get("generatedAt", 0))
    max_w, max_l = _streaks([s["status"] for s in resolved])
    base.update({"maxWinningStreak": max_w, "maxLosingStreak": max_l})
    r_chrono = [s.get("rMultiple") for s in resolved if s.get("rMultiple") is not None]
    base["maxDrawdownR"] = _max_drawdown_r(r_chrono) if r_chrono else None

    # Feed the Monte Carlo the NET series when costs were modelled: a path
    # built from gross R describes a market with no fees, which is not the
    # market these signals would have traded in.
    if base.get("costsModelled"):
        r_paths = [s.get("rMultipleNet") if s.get("rMultipleNet") is not None
                   else s.get("rMultiple") for s in resolved]
        r_paths = [r for r in r_paths if r is not None]
        basis = "net"
    else:
        r_paths, basis = r_chrono, "gross"
    base["monteCarlo"] = _monte_carlo(r_paths, block=cfg.get("performance.monteCarloBlockLength"))
    if base["monteCarlo"]:
        base["monteCarlo"]["basis"] = basis
        # A field named *Net must never be populated from the gross series.
        # When costs were not modelled there is no net figure to report, and
        # null is the honest answer -- silently echoing the gross drawdown
        # under a "net" label understates the real cost of trading.
        base["monteCarloNet"] = (_max_drawdown_r(r_paths)
                                 if (r_paths and basis == "net") else None)

    # empirical score calibration: historical win rate per quality bucket with
    # Wilson 95% CI -- descriptive statistics, NOT probabilities of future wins
    calibration = {}
    for bucket in ("A+", "A", "B+"):
        bucket_signals = [s for s in window if s.get("quality") == bucket]
        bw = sum(1 for s in bucket_signals if s["status"] == WIN)
        bl = sum(1 for s in bucket_signals if s["status"] == LOSS)
        n_res = bw + bl
        lo, hi = wilson_interval(bw, n_res)
        calibration[bucket] = {"signals": len(bucket_signals), "resolved": n_res,
                               "wins": bw, "losses": bl,
                               "winRate": _pct(bw, n_res),
                               "wilson95": {"lower": lo, "upper": hi}}
    base["scoreCalibration"] = calibration

    def breakdown(key_fn) -> dict:
        groups: dict[str, list[dict]] = defaultdict(list)
        for s in window:
            groups[key_fn(s)].append(s)
        return {k: _agg(v) for k, v in sorted(groups.items())}

    base["byDirection"] = breakdown(lambda s: s["direction"])
    base["byQuality"] = breakdown(lambda s: s.get("quality", _bucket_by_score(s.get("score", 0), cfg)))
    base["byRegime"] = breakdown(lambda s: s.get("marketRegime", "unknown"))
    base["bySymbol"] = breakdown(lambda s: s["symbol"])
    base["byScoreRange"] = breakdown(lambda s: _bucket_by_score(s.get("score", 0), cfg))
    base["window"] = {"since": since_ms, "until": until_ms, "signalsInWindow": len(window)}
    base["generatedAt"] = now_ms
    base["disclaimer"] = (
        "Win rate = wins / (wins + losses); waiting/expired/ambiguous/cancelled "
        "signals are excluded from the denominator. Gross figures (winRate, "
        "expectancyR, profitFactor, sharpeR, monteCarlo) EXCLUDE fees and "
        "funding; the *Net figures include them and are the ones to read. The "
        "confluence score is a quality ranking, not a probability. Past "
        "performance does not guarantee future results."
    )
    return base
