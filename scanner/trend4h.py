"""Tendance 4H -- paper-trading engine (no order is ever sent).

Rules validated in research (5 cryptos, 2019-2026, fees + slippage + funding):
  * signal: a closed 4H candle closes beyond the high/low of the previous ``channelBars``;
  * filters: trade only in the direction of ``trendSymbol`` vs its EMA(``trendEma``) on 4H,
    signal-candle volume > ``volumeMultiple`` x its ``volumeLookback`` average,
    funding not already crowded in the trade direction;
  * entry at the next 4H open, initial stop ``stopAtr`` x ATR, exit when a 4H candle
    closes beyond the opposite ``trailBars`` channel;
  * size: ``riskPct`` of realized equity lost at the stop, ``maxPositions`` open at most
    (one per symbol), total notional capped at ``maxLeverage`` x equity.

State lives in data/trend4h-paper.json; every run only processes 4H candles closed since
the previous run, so missed runs are caught up and re-runs never duplicate trades.
"""
from __future__ import annotations

import argparse
import copy
import math
import sys
import time

from . import persist
from .config import Config
from .indicators import atr, ema, relative_volume
from .market_data import Candle, MarketDataError

FILE = "trend4h-paper.json"
H4_MS = 14_400_000
FUNDING_PERIOD_MS = 8 * 3_600_000
STATE_VERSION = 1
MAX_EVALUATIONS = 100
MAX_CURVE_POINTS = 2000


def _new_state(params: dict, now_ms: int) -> dict:
    capital = float(params["startCapital"])
    return {
        "version": STATE_VERSION, "mode": "paper", "generatedAt": now_ms,
        "startedAt": now_ms, "startCapital": capital, "equity": capital,
        "params": copy.deepcopy(params), "lastBarClose": {}, "positions": [], "pending": [],
        "trades": [], "evaluations": [], "equityCurve": [{"t": now_ms, "equity": capital}],
        "stats": {}, "lastRun": None,
    }


def _prepare(candles: list[Candle], now_ms: int, params: dict) -> dict:
    closed = [c for c in candles if c.closeTime < now_ms]
    live = candles[-1] if candles and candles[-1].closeTime >= now_ms else None
    highs = [c.high for c in closed]
    lows = [c.low for c in closed]
    closes = [c.close for c in closed]
    return {
        "closed": closed, "live": live,
        "index": {c.openTime: i for i, c in enumerate(closed)},
        "atr": atr(highs, lows, closes, int(params["atrPeriod"])),
        "relVol": relative_volume([c.volume for c in closed], int(params["volumeLookback"])),
    }


def _settlements(after_ms: int, until_ms: int):
    t = (after_ms // FUNDING_PERIOD_MS + 1) * FUNDING_PERIOD_MS
    while t <= until_ms:
        yield t
        t += FUNDING_PERIOD_MS


def _accrue_funding(pos: dict, rate: float | None, until_ms: int) -> None:
    """Long positions pay a positive funding rate, shorts receive it (Binance convention)."""
    if rate is None:
        return
    side = 1 if pos["side"] == "LONG" else -1
    for t in _settlements(pos["lastFundingTs"], until_ms):
        pos["fundingPaid"] += side * rate * pos["qty"] * pos["entryPrice"]
        pos["lastFundingTs"] = t


def _close_position(st: dict, pos: dict, exit_price: float, exit_ms: int, reason: str,
                    params: dict, rate: float | None) -> None:
    _accrue_funding(pos, rate, exit_ms)
    side = 1 if pos["side"] == "LONG" else -1
    r_gross = side * (exit_price - pos["entryPrice"]) / pos["riskPerUnit"]
    cost_pct = 2 * (float(params["takerFeePct"]) + float(params["slippagePct"])) / 100.0
    r_costs = cost_pct * pos["entryPrice"] / pos["riskPerUnit"]
    pnl = pos["stake"] * (r_gross - r_costs) - pos["fundingPaid"]
    st["equity"] = round(st["equity"] + pnl, 8)
    st["positions"].remove(pos)
    st["trades"].append({
        "id": pos["id"], "symbol": pos["symbol"], "side": pos["side"],
        "entryTime": pos["entryTime"], "entryPrice": pos["entryPrice"],
        "initialStop": pos["initialStop"], "exitTime": exit_ms, "exitPrice": exit_price,
        "exitReason": reason, "stake": round(pos["stake"], 6),
        "rGross": round(r_gross, 4), "rCosts": round(r_costs, 4),
        "fundingPaid": round(pos["fundingPaid"], 6),
        "rNet": round(pnl / pos["stake"], 4), "pnl": round(pnl, 6),
        "equityAfter": round(st["equity"], 6),
    })
    st["equityCurve"].append({"t": exit_ms, "equity": round(st["equity"], 6)})


def _manage(st: dict, sym: str, d: dict, k: int, params: dict, funding: dict) -> bool:
    """Apply stop / trailing exit on closed candle k. Returns True if the position closed."""
    pos = next((p for p in st["positions"] if p["symbol"] == sym), None)
    bar = d["closed"][k]
    if pos is None or bar.openTime < pos["entryTime"]:
        return False
    side = 1 if pos["side"] == "LONG" else -1
    trail = int(params["trailBars"])
    stop = pos["stop"]
    if (bar.low <= stop) if side > 0 else (bar.high >= stop):
        gapped = (bar.open <= stop) if side > 0 else (bar.open >= stop)
        price = bar.open if gapped and bar.openTime > pos["entryTime"] else stop
        _close_position(st, pos, price, bar.closeTime + 1, "STOP", params, funding.get(sym))
        return True
    if bar.openTime > pos["entryTime"] and k >= trail:
        window = d["closed"][k - trail:k]
        level = min(c.low for c in window) if side > 0 else max(c.high for c in window)
        if (bar.close < level) if side > 0 else (bar.close > level):
            _close_position(st, pos, bar.close, bar.closeTime + 1, "TRAIL", params, funding.get(sym))
            return True
    window = d["closed"][max(0, k - trail + 1):k + 1]
    pos["trailLevel"] = min(c.low for c in window) if side > 0 else max(c.high for c in window)
    return False


def _evaluate(st: dict, sym: str, d: dict, k: int, trend: dict, funding: dict, params: dict) -> None:
    channel = int(params["channelBars"])
    closed = d["closed"]
    if k < channel or d["atr"][k] is None or d["relVol"][k] is None:
        return
    bar = closed[k]
    hi = max(c.high for c in closed[k - channel:k])
    lo = min(c.low for c in closed[k - channel:k])
    side = 1 if bar.close > hi else (-1 if bar.close < lo else 0)
    if not side:
        return
    reasons = []
    trend_close, trend_ema = trend.get(bar.openTime, (None, None))
    if trend_ema is None:
        reasons.append(f"tendance {params['trendSymbol']} indisponible")
    elif side * (trend_close - trend_ema) <= 0:
        reasons.append(f"contre la tendance {params['trendSymbol']}")
    rel_vol = d["relVol"][k]
    if rel_vol <= float(params["volumeMultiple"]):
        reasons.append(f"volume {rel_vol:.2f}x <= {float(params['volumeMultiple']):g}x")
    rate = funding.get(sym)
    if rate is not None and side * rate >= float(params["maxFundingRate"]):
        reasons.append("financement deja sature dans ce sens")
    if len(st["positions"]) + len(st["pending"]) >= int(params["maxPositions"]):
        reasons.append("nombre maximal de positions atteint")
    st["evaluations"].append({
        "symbol": sym, "barClose": bar.closeTime + 1, "side": "LONG" if side > 0 else "SHORT",
        "close": bar.close, "channelLevel": hi if side > 0 else lo, "relVolume": round(rel_vol, 3),
        "trendAligned": trend_ema is not None and side * (trend_close - trend_ema) > 0,
        "fundingRate": rate, "taken": not reasons, "reasons": reasons,
    })
    st["evaluations"] = st["evaluations"][-MAX_EVALUATIONS:]
    if not reasons:
        st["pending"].append({
            "symbol": sym, "side": "LONG" if side > 0 else "SHORT", "signalClose": bar.closeTime,
            "riskPerUnit": float(params["stopAtr"]) * d["atr"][k],
            "stake": st["equity"] * float(params["riskPct"]) / 100.0,
        })


def _fill_pending(st: dict, sym: str, d: dict, params: dict) -> None:
    for p in [p for p in st["pending"] if p["symbol"] == sym]:
        open_ms = p["signalClose"] + 1
        idx = d["index"].get(open_ms)
        bar = d["closed"][idx] if idx is not None else (d["live"] if d["live"] and d["live"].openTime == open_ms else None)
        if bar is None:
            continue
        st["pending"].remove(p)
        side = 1 if p["side"] == "LONG" else -1
        qty = p["stake"] / p["riskPerUnit"]
        notional = qty * bar.open
        exposure = sum(x["notional"] for x in st["positions"]) + notional
        if exposure > float(params["maxLeverage"]) * st["equity"]:
            st["evaluations"].append({"symbol": sym, "barClose": open_ms, "side": p["side"],
                                      "taken": False, "reasons": ["levier maximal depasse"]})
            continue
        st["positions"].append({
            "id": f"{sym}-{open_ms}", "symbol": sym, "side": p["side"], "entryTime": open_ms,
            "entryPrice": bar.open, "riskPerUnit": p["riskPerUnit"],
            "stop": bar.open - side * p["riskPerUnit"], "initialStop": bar.open - side * p["riskPerUnit"],
            "trailLevel": None, "stake": p["stake"], "qty": qty, "notional": notional,
            "fundingPaid": 0.0, "lastFundingTs": open_ms, "markPrice": bar.open, "unrealizedR": 0.0,
        })


def _stats(st: dict) -> dict:
    trades = st["trades"]
    r = [t["rNet"] for t in trades]
    unrealized = sum(p["stake"] * p["unrealizedR"] - p["fundingPaid"] for p in st["positions"])
    peak, max_dd = st["startCapital"], 0.0
    for point in st["equityCurve"]:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = max(max_dd, 1 - point["equity"] / peak)
    equity_mtm = st["equity"] + unrealized
    return {
        "trades": len(r), "wins": sum(1 for x in r if x > 0),
        "winRate": round(sum(1 for x in r if x > 0) / len(r), 4) if r else None,
        "avgR": round(sum(r) / len(r), 4) if r else None, "totalR": round(sum(r), 4),
        "realizedPnl": round(st["equity"] - st["startCapital"], 6),
        "unrealizedPnl": round(unrealized, 6), "equityMarked": round(equity_mtm, 6),
        "returnPct": round(100 * (equity_mtm / st["startCapital"] - 1), 4),
        "maxDrawdownPct": round(100 * max_dd, 4),
        "openPositions": len(st["positions"]),
        "exposure": round(sum(p["notional"] for p in st["positions"]) / st["equity"], 4) if st["equity"] > 0 else None,
    }


def _watch(st: dict, params: dict, data: dict, trend: dict, funding: dict) -> dict:
    """What the next 4H close must do on each symbol, and the size a trade would get now."""
    channel = int(params["channelBars"])
    t_close, t_ema = trend[max(trend)] if trend else (None, None)
    trend_side = None if t_ema is None else ("LONG" if t_close > t_ema else "SHORT" if t_close < t_ema else None)
    stake = st["equity"] * float(params["riskPct"]) / 100.0
    out = {}
    for sym, d in data.items():
        closed = d["closed"]
        if len(closed) < channel:
            continue
        price = (d["live"] or closed[-1]).close
        last_atr, rel_vol = d["atr"][-1], d["relVol"][-1]
        risk = float(params["stopAtr"]) * last_atr if last_atr else None
        notional = stake / risk * price if risk else None
        out[sym] = {
            "price": price, "barClose": closed[-1].closeTime + 1,
            "channelHigh": max(c.high for c in closed[-channel:]),
            "channelLow": min(c.low for c in closed[-channel:]),
            "relVolume": round(rel_vol, 3) if rel_vol is not None else None,
            "fundingRate": funding.get(sym), "trendSide": trend_side,
            "trendClose": t_close, "trendEma": round(t_ema, 6) if t_ema is not None else None,
            "stopDistance": round(risk, 8) if risk else None,
            "plannedStake": round(stake, 6),
            "plannedNotional": round(notional, 6) if notional else None,
            "plannedLeverage": round(notional / st["equity"], 4) if notional and st["equity"] > 0 else None,
        }
    return out


def step(state: dict | None, params: dict, candles: dict[str, list[Candle]],
         funding: dict[str, float], now_ms: int) -> dict:
    """Advance the simulation with every 4H candle closed since the previous call."""
    st = copy.deepcopy(state) if state and state.get("version") == STATE_VERSION else _new_state(params, now_ms)
    symbols = [s for s in params["symbols"] if candles.get(s)]
    data = {s: _prepare(candles[s], now_ms, params) for s in symbols}
    trend_sym = params["trendSymbol"]
    trend: dict[int, tuple] = {}
    if candles.get(trend_sym):
        td = data[trend_sym] if trend_sym in data else _prepare(candles[trend_sym], now_ms, params)
        series = ema([c.close for c in td["closed"]], int(params["trendEma"]))
        trend = {c.openTime: (c.close, e) for c, e in zip(td["closed"], series)}
    for s in symbols:
        if s not in st["lastBarClose"] and len(data[s]["closed"]) >= 2:
            st["lastBarClose"][s] = data[s]["closed"][-2].closeTime  # start fresh: no backfill
    events = sorted((c.closeTime, symbols.index(s), s, k)
                    for s in symbols for k, c in enumerate(data[s]["closed"])
                    if c.closeTime > st["lastBarClose"].get(s, math.inf))
    for _, _, sym, k in events:
        d = data[sym]
        _fill_pending(st, sym, d, params)
        exited = _manage(st, sym, d, k, params, funding)
        busy = any(p["symbol"] == sym for p in st["positions"] + st["pending"])
        if not exited and not busy:
            _evaluate(st, sym, d, k, trend, funding, params)
        st["lastBarClose"][sym] = d["closed"][k].closeTime
    for sym in symbols:
        _fill_pending(st, sym, data[sym], params)
    for pos in st["positions"]:
        _accrue_funding(pos, funding.get(pos["symbol"]), now_ms)
        d = data.get(pos["symbol"])
        if d:
            last = d["live"] or (d["closed"][-1] if d["closed"] else None)
            if last:
                side = 1 if pos["side"] == "LONG" else -1
                pos["markPrice"] = last.close
                pos["unrealizedR"] = round(side * (last.close - pos["entryPrice"]) / pos["riskPerUnit"], 4)
    st["stats"] = _stats(st)
    st["watch"] = _watch(st, params, data, trend, funding)
    if events:
        st["equityCurve"].append({"t": now_ms, "equity": st["stats"]["equityMarked"]})
        st["equityCurve"] = st["equityCurve"][-MAX_CURVE_POINTS:]
    st["params"] = copy.deepcopy(params)
    st["generatedAt"] = now_ms
    return st


def run(cfg: Config, client=None, now_ms: int | None = None, log=print) -> dict | None:
    params = cfg.get("trend4h", {})
    if not params.get("enabled", False):
        log("trend4h: disabled")
        return None
    now = now_ms or int(time.time() * 1000)
    state = persist.load_json(FILE, None)  # raises rather than wiping an unreadable history
    if client is None:
        from .main import make_client
        client = make_client(cfg)
    wanted = list(dict.fromkeys([*params["symbols"], params["trendSymbol"]]))
    candles, errors = {}, []
    for sym in wanted:
        try:
            candles[sym] = client.klines(sym, "4h", int(params["klineLimit"]))
        except MarketDataError as exc:
            errors.append(f"{sym}: {exc}")
    funding: dict[str, float] = {}
    try:
        for row in client.premium_index():
            if row.get("symbol") in wanted:
                try:
                    funding[row["symbol"]] = float(row.get("lastFundingRate") or 0)
                except (TypeError, ValueError):
                    continue
    except MarketDataError as exc:
        errors.append(f"funding: {exc}")
    if params["trendSymbol"] not in candles:
        # Without the trend filter no bar may be consumed; keep state and retry next run.
        new = copy.deepcopy(state) if state and state.get("version") == STATE_VERSION else _new_state(params, now)
        new["generatedAt"] = now
    else:
        new = step(state, params, candles, funding, now)
    new["lastRun"] = {"at": now, "status": "ok" if not errors else "degraded", "errors": errors[:10]}
    persist.write_data_file(FILE, new)
    s = new.get("stats", {})
    log(f"trend4h: equity {s.get('equityMarked')} | open {s.get('openPositions')} | trades {s.get('trades')}"
        + (f" | errors: {'; '.join(errors)}" if errors else ""))
    return new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tendance 4H paper-trading step")
    parser.add_argument("--config", default=None, help="path to strategy.json")
    args = parser.parse_args(argv)
    cfg = Config.load(args.config)
    problems = cfg.validate()
    if problems:
        print("config invalid: " + "; ".join(problems), file=sys.stderr)
        return 1
    run(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
