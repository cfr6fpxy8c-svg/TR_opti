"""Multi-timeframe analysis: 4H macro, 1H directional, 15M signal frame.

``analyze_symbol`` is pure: it receives already-truncated (closed-candles
only) OHLCV series for each timeframe and returns every derived value the
signal models need. Determinism: identical inputs -> identical output dict.
No lookahead: every series is indexed to its own end; structure events are
only exposed once confirmed (see structure.py).
"""
from __future__ import annotations

from typing import Optional

from .config import Config
from . import indicators as ind
from . import structure as st
from . import smc


TIMEFRAMES = ("4h", "1h", "15m")


def min_adx_for(cfg, timeframe: str) -> float:
    """ADX threshold for a given timeframe.

    ADX is not scale-free across timeframes: the same market reads differently
    on 15M and on 1D, so a single ``signalModel.minAdx15m`` used everywhere
    mis-classifies higher-timeframe regimes (it was applied to the 4H regime
    and the 1D bias). Per-timeframe overrides live in
    ``signalModel.minAdxByTimeframe``; the 15M value remains the fallback so
    existing configs keep working.
    """
    per_tf = cfg.get("signalModel.minAdxByTimeframe", {}) or {}
    fallback = cfg.get("signalModel.minAdx15m", 15)
    value = per_tf.get(timeframe)
    return float(value) if value is not None else float(fallback)


def htf_bias(closes: list[float], ema_fast: list, ema_mid: list, ema_slow: list,
             adx_value: Optional[float], min_adx: float) -> dict:
    """Classify a higher timeframe as strong_bullish..strong_bearish.

    strong_bullish : close > EMA200 and EMA50 > EMA200 and EMA20 > EMA50  (stacked)
    bullish        : (close > EMA200 and EMA20 > EMA50) or EMA20 > EMA50 > EMA200
    neutral        : neither side qualifies
    bearish / strong_bearish : mirrored.
    """
    if not closes or ema_fast[-1] is None or ema_mid[-1] is None or ema_slow[-1] is None:
        return {"bias": "unknown", "strength": 0.0}
    c = closes[-1]
    f, m, s = ema_fast[-1], ema_mid[-1], ema_slow[-1]
    strong_bull = c > s and m > s and f > m
    bull = (c > s and f > m) or (f > m > s)
    strong_bear = c < s and m < s and f < m
    bear = (c < s and f < m) or (f < m < s)
    if strong_bull:
        bias = "strong_bullish"
    elif bull:
        bias = "bullish"
    elif strong_bear:
        bias = "strong_bearish"
    elif bear:
        bias = "bearish"
    else:
        bias = "neutral"
    strength = 2 if bias.startswith("strong") else (1 if bias in ("bullish", "bearish") else 0)
    # A directional EMA stack with no trend strength behind it is a drift, not
    # a bias. Demote it so the signal models do not treat a flat tape as an
    # aligned higher timeframe. (The previous version of this branch assigned
    # "neutral" to something already "neutral" -- a no-op that demoted nothing.)
    if adx_value is not None and min_adx and adx_value < min_adx and bias in ("bullish", "bearish"):
        bias, strength = "neutral", 0
    return {"bias": bias, "strength": strength, "emaFast": f, "emaMid": m, "emaSlow": s,
            "adx": adx_value}


def market_regime(bias_4h: dict, adx_4h: Optional[float], min_adx: float) -> str:
    """TRENDING_UP / TRENDING_DOWN / RANGING / MIXED."""
    if adx_4h is not None and adx_4h < min_adx:
        return "RANGING"
    b = bias_4h.get("bias")
    if b in ("strong_bullish", "bullish"):
        return "TRENDING_UP"
    if b in ("strong_bearish", "bearish"):
        return "TRENDING_DOWN"
    return "MIXED"


def _series(candles):
    return ([c.open for c in candles], [c.high for c in candles],
            [c.low for c in candles], [c.close for c in candles],
            [c.volume for c in candles])


def analyze_symbol(symbol: str, klines: dict, cfg: Config, now_ms: int) -> dict:
    """Compute the full analysis snapshot for one symbol.

    ``klines`` maps timeframe -> list[Candle] (closed candles only; the
    caller is responsible for truncation -- replay passes slices).
    """
    icfg = cfg.get("indicators")
    scfg = cfg.get("structure")
    mcfg = cfg.get("smc")
    out: dict = {"symbol": symbol, "ok": False, "reasons": []}

    frames: dict[str, dict] = {}
    for tf in TIMEFRAMES:
        candles = klines.get(tf) or []
        min_needed = icfg.get("emaSlow") + 5
        if len(candles) < min_needed:
            out["reasons"].append(f"{tf}: insufficient candles ({len(candles)}<{min_needed})")
            return out
        o, h, l, c, v = _series(candles)
        atr_series = ind.atr(h, l, c, icfg.get("atrPeriod"))
        adx_pack = ind.adx(h, l, c, icfg.get("adxPeriod"))
        swings_hi = st.find_swing_highs(h, scfg.get("swingLookback"))
        swings_lo = st.find_swing_lows(l, scfg.get("swingLookback"))
        events = st.detect_structure_events(c, swings_hi, swings_lo, scfg.get("maxStructureSwings"))
        frames[tf] = {
            "candles": candles, "o": o, "h": h, "l": l, "c": c, "v": v,
            "atr": atr_series,
            "emaFast": ind.ema(c, icfg.get("emaFast")),
            "emaMid": ind.ema(c, icfg.get("emaMid")),
            "emaSlow": ind.ema(c, icfg.get("emaSlow")),
            "rsi": ind.rsi(c, icfg.get("rsiPeriod")),
            "adx": adx_pack["adx"], "plusDi": adx_pack["plusDi"], "minusDi": adx_pack["minusDi"],
            "relVol": ind.relative_volume(v, icfg.get("relVolumeLookback")),
            "vwap": ind.rolling_vwap(h, l, c, v, icfg.get("vwapWindow")),
            "swingHighs": swings_hi, "swingLows": swings_lo,
            "events": events,
            "equalHighs": st.equal_levels(swings_hi, atr_series, scfg.get("equalLevelAtrTolerance")),
            "equalLows": st.equal_levels(swings_lo, atr_series, scfg.get("equalLevelAtrTolerance")),
            "displacements": st.find_displacements(o, c, atr_series, scfg.get("displacementBodyAtrMultiple")),
            "sweeps": st.find_liquidity_sweeps(h, l, c, swings_hi, swings_lo, atr_series,
                                               scfg.get("minSwingAgeBars"), scfg.get("sweepMaxAtrMultiple")),
            "fvgs": smc.find_fvgs(o, h, l, c, atr_series, mcfg.get("fvgMinGapAtrMultiple")),
            "orderBlocks": smc.find_order_blocks(o, h, l, c,
                                                 st.find_displacements(o, c, atr_series, scfg.get("displacementBodyAtrMultiple")),
                                                 events, mcfg.get("obLookback")),
        }

    # optional 1D macro bias -- never blocks when absent/insufficient
    d1 = klines.get("1d") or []
    if len(d1) >= icfg.get("emaSlow") + 5:
        o1, h1, l1, c1, v1 = _series(d1)
        bias_1d = htf_bias(c1, ind.ema(c1, icfg.get("emaFast")),
                           ind.ema(c1, icfg.get("emaMid")),
                           ind.ema(c1, icfg.get("emaSlow")),
                           ind.value_at(ind.adx(h1, l1, c1, icfg.get("adxPeriod"))["adx"],
                                        len(c1) - 1),
                           min_adx_for(cfg, "1d"))
    else:
        bias_1d = {"bias": "unknown", "strength": 0}

    f4, f1, f15 = frames["4h"], frames["1h"], frames["15m"]
    last = len(f15["c"]) - 1

    bias_4h = htf_bias(f4["c"], f4["emaFast"], f4["emaMid"], f4["emaSlow"],
                       ind.value_at(f4["adx"], len(f4["c"]) - 1), min_adx_for(cfg, "4h"))
    bias_1h = htf_bias(f1["c"], f1["emaFast"], f1["emaMid"], f1["emaSlow"],
                       ind.value_at(f1["adx"], len(f1["c"]) - 1), min_adx_for(cfg, "1h"))

    out.update({
        "ok": True,
        "symbol": symbol,
        "last15mIndex": last,
        "last15mCloseTime": f15["candles"][last].closeTime,
        "last15mOpenTime": f15["candles"][last].openTime,
        "price": f15["candles"][last].close,
        "regime": market_regime(bias_4h, ind.value_at(f4["adx"], len(f4["c"]) - 1),
                                min_adx_for(cfg, "4h")),
        "bias1d": bias_1d,
        "bias4h": bias_4h,
        "bias1h": bias_1h,
        "rsi": ind.value_at(f15["rsi"], last),
        "adx": ind.value_at(f15["adx"], last),
        "plusDi": ind.value_at(f15["plusDi"], last),
        "minusDi": ind.value_at(f15["minusDi"], last),
        "atr": ind.value_at(f15["atr"], last),
        "atrPercent": (ind.value_at(f15["atr"], last) / f15["candles"][last].close * 100.0)
                      if ind.value_at(f15["atr"], last) else None,
        "relVolume": ind.value_at(f15["relVol"], last),
        "vwap": ind.value_at(f15["vwap"], last),
        "frames": frames,
    })
    return out
