"""Tendance 4H + renfort 1H -- second paper portfolio, fully separate from scanner/trend4h.py.

Two books share one capital (``startCapital``):
  * 4H book: exactly the Tendance 4H rules (channel / trailing / stop / 3 filters), risk ``risk4hPct``;
  * 1H book: same rules on 1H candles (channel ``h1ChannelBars``, trailing ``h1TrailBars``,
    stop ``h1StopAtr`` x ATR), same 3 filters with the BTC trend read on 4H, risk ``risk1hPct``,
    and only while the 4H book holds a position on that crypto in the same direction.
The engine is a deliberate copy of trend4h.py (so the live 4H test is never affected), extended
with an entry gate, a 4H trend source for the 1H book and the candle interval for shadow trades.
State lives in data/trend-combo-paper.json.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import math
import sys
import time

from . import persist
from .config import Config
from .indicators import atr, ema, relative_volume
from .market_data import Candle, MarketDataError

FILE = "trend-combo-paper.json"
H1_MS = 3_600_000
H4_MS = 14_400_000
FUNDING_PERIOD_MS = 8 * 3_600_000
STATE_VERSION = 1
MAX_EVALUATIONS = 1000
MAX_CURVE_POINTS = 2000
CHART_BARS = 60
GATE_REASON = "pas de position 4H ouverte dans ce sens"


# ------------------------------------------------------------------ book engine (copy of trend4h)
def _new_book(params: dict, now_ms: int, capital: float) -> dict:
    return {
        "version": STATE_VERSION, "generatedAt": now_ms, "startedAt": now_ms, "startCapital": capital,
        "equity": capital, "params": copy.deepcopy(params), "lastBarClose": {}, "positions": [],
        "pending": [], "trades": [], "evaluations": [], "stats": {},
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


def _manage(st: dict, sym: str, d: dict, k: int, params: dict, funding: dict) -> bool:
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


def _evaluate(st: dict, sym: str, d: dict, k: int, trend: dict, funding: dict, params: dict, gate=None) -> None:
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
    side_name = "LONG" if side > 0 else "SHORT"
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
    if gate is not None and not gate(sym, side_name, bar.closeTime + 1):
        reasons.append(GATE_REASON)
    st["evaluations"].append({
        "symbol": sym, "barClose": bar.closeTime + 1, "side": side_name,
        "close": bar.close, "channelLevel": hi if side > 0 else lo, "relVolume": round(rel_vol, 3),
        "trendAligned": trend_ema is not None and side * (trend_close - trend_ema) > 0,
        "fundingRate": rate, "taken": not reasons, "reasons": reasons,
    })
    st["evaluations"] = st["evaluations"][-MAX_EVALUATIONS:]
    if not reasons:
        st["pending"].append({
            "symbol": sym, "side": side_name, "signalClose": bar.closeTime,
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


def _shadow_bar(sh: dict, d: dict, k: int, params: dict) -> None:
    bar = d["closed"][k]
    side = 1 if sh["side"] == "LONG" else -1
    if sh["status"] == "pending":
        if bar.openTime != sh["entryTime"]:
            return
        sh.update(status="open", entryPrice=bar.open, stop=bar.open - side * sh["riskPerUnit"])
    stop, trail, exit_price, reason = sh["stop"], int(params["trailBars"]), None, None
    if (bar.low <= stop) if side > 0 else (bar.high >= stop):
        gapped = (bar.open <= stop) if side > 0 else (bar.open >= stop)
        exit_price, reason = (bar.open if gapped and bar.openTime > sh["entryTime"] else stop), "STOP"
    elif bar.openTime > sh["entryTime"] and k >= trail:
        window = d["closed"][k - trail:k]
        level = min(c.low for c in window) if side > 0 else max(c.high for c in window)
        if (bar.close < level) if side > 0 else (bar.close > level):
            exit_price, reason = bar.close, "TRAIL"
    if exit_price is None:
        sh["markR"] = round(side * (bar.close - sh["entryPrice"]) / sh["riskPerUnit"], 4)
        return
    cost_pct = 2 * (float(params["takerFeePct"]) + float(params["slippagePct"])) / 100.0
    r_gross = side * (exit_price - sh["entryPrice"]) / sh["riskPerUnit"]
    sh.update(status="closed", exitTime=bar.closeTime + 1, exitPrice=exit_price, exitReason=reason,
              rNet=round(r_gross - cost_pct * sh["entryPrice"] / sh["riskPerUnit"], 4))
    sh.pop("markR", None)


def _update_shadows(st: dict, data: dict, params: dict, interval_ms: int) -> None:
    for e in st["evaluations"]:
        d = data.get(e["symbol"])
        if d is None or "close" not in e:
            continue
        sh = e.get("shadow")
        if sh is None:
            sig = d["index"].get(e["barClose"] - interval_ms)
            if sig is None or d["atr"][sig] is None:
                continue
            sh = e["shadow"] = {"status": "pending", "side": e["side"], "entryTime": e["barClose"],
                                 "riskPerUnit": float(params["stopAtr"]) * d["atr"][sig],
                                 "lastBar": e["barClose"] - 1}
        k = d["index"].get(sh["lastBar"] + 1)
        while sh["status"] != "closed" and k is not None and k < len(d["closed"]):
            _shadow_bar(sh, d, k, params)
            sh["lastBar"] = d["closed"][k].closeTime
            k += 1


def _shadow_stats(evaluations: list) -> dict:
    out = {}
    for key, taken in (("taken", True), ("rejected", False)):
        rows = [e["shadow"] for e in evaluations if e.get("taken") is taken and e.get("shadow")]
        r = [s["rNet"] for s in rows if s["status"] == "closed"]
        out[key] = {"count": len(rows), "closed": len(r), "wins": sum(1 for x in r if x > 0),
                    "avgR": round(sum(r) / len(r), 4) if r else None, "totalR": round(sum(r), 4)}
    return out


def _book_stats(st: dict) -> dict:
    r = [t["rNet"] for t in st["trades"]]
    return {
        "trades": len(r), "wins": sum(1 for x in r if x > 0),
        "winRate": round(sum(1 for x in r if x > 0) / len(r), 4) if r else None,
        "avgR": round(sum(r) / len(r), 4) if r else None, "totalR": round(sum(r), 4),
        "realizedPnl": round(sum(t["pnl"] for t in st["trades"]), 6),
        "unrealizedPnl": round(sum(p["stake"] * p["unrealizedR"] - p["fundingPaid"] for p in st["positions"]), 6),
        "openPositions": len(st["positions"]),
        "notional": round(sum(p["notional"] for p in st["positions"]), 6),
    }


def _watch(st: dict, params: dict, data: dict, trend: dict, funding: dict) -> dict:
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
            "candles": [[c.openTime, c.open, c.high, c.low, c.close]
                        for c in closed[-CHART_BARS:] + ([d["live"]] if d["live"] else [])],
        }
    return out


def step_book(state: dict | None, params: dict, candles: dict[str, list[Candle]], funding: dict[str, float],
              now_ms: int, equity: float, interval_ms: int, gate=None,
              trend_candles: list[Candle] | None = None) -> dict:
    """Advance one book with every candle closed since its previous call, sizing on ``equity``.

    trend_candles (4H) gives the trend filter to a 1H book: each bar uses the last 4H candle closed by then.
    """
    st = copy.deepcopy(state) if state and state.get("version") == STATE_VERSION else _new_book(params, now_ms, equity)
    st["equity"] = equity
    symbols = [s for s in params["symbols"] if candles.get(s)]
    data = {s: _prepare(candles[s], now_ms, params) for s in symbols}
    trend: dict[int, tuple] = {}
    source = trend_candles if trend_candles is not None else candles.get(params["trendSymbol"])
    if source:
        tc = [c for c in source if c.closeTime < now_ms]
        series = ema([c.close for c in tc], int(params["trendEma"]))
        closes = [c.closeTime for c in tc]
        for c in {c.openTime: c for s in symbols for c in data[s]["closed"]}.values():
            i = bisect.bisect_right(closes, c.closeTime) - 1
            if i >= 0:
                trend[c.openTime] = (tc[i].close, series[i])
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
            _evaluate(st, sym, d, k, trend, funding, params, gate)
        st["lastBarClose"][sym] = d["closed"][k].closeTime
    for sym in symbols:
        _fill_pending(st, sym, data[sym], params)
    for pos in st["positions"]:
        _accrue_funding(pos, funding.get(pos["symbol"]), now_ms)
        d = data.get(pos["symbol"])
        last = (d["live"] or (d["closed"][-1] if d["closed"] else None)) if d else None
        if last:
            side = 1 if pos["side"] == "LONG" else -1
            pos["markPrice"] = last.close
            pos["unrealizedR"] = round(side * (last.close - pos["entryPrice"]) / pos["riskPerUnit"], 4)
    _update_shadows(st, data, params, interval_ms)
    st["shadowStats"] = _shadow_stats(st["evaluations"])
    st["stats"] = _book_stats(st)
    st["watch"] = _watch(st, params, data, trend, funding)
    st["params"] = copy.deepcopy(params)
    st["generatedAt"] = now_ms
    st["hadEvents"] = bool(events)
    return st


# ------------------------------------------------------------------ combined portfolio
def book_params(cfg: Config) -> tuple[dict, dict]:
    base = copy.deepcopy(cfg.get("trend4h", {}))
    c = cfg.get("trendCombo", {})
    p4 = {**base, "riskPct": float(c["risk4hPct"]), "startCapital": float(c["startCapital"])}
    p1 = {**base, "riskPct": float(c["risk1hPct"]), "startCapital": float(c["startCapital"]),
          "channelBars": int(c["h1ChannelBars"]), "trailBars": int(c["h1TrailBars"]),
          "stopAtr": float(c["h1StopAtr"]), "klineLimit": int(c["h1KlineLimit"])}
    return p4, p1


def gate_from(book4h: dict):
    """1H entries only while the 4H book holds the same direction on that crypto."""
    spans: dict[str, list] = {}
    for t in book4h.get("trades", []):
        spans.setdefault(t["symbol"], []).append((t["entryTime"], t["exitTime"], t["side"]))
    for p in book4h.get("positions", []):
        spans.setdefault(p["symbol"], []).append((p["entryTime"], math.inf, p["side"]))

    def gate(sym: str, side: str, t: int) -> bool:
        return any(a <= t < b and s == side for a, b, s in spans.get(sym, []))
    return gate


def _realized(book: dict | None) -> float:
    return sum(t["pnl"] for t in (book or {}).get("trades", []))


def combine(state: dict | None, cfg: Config, candles4: dict, candles1: dict, funding: dict, now_ms: int) -> dict:
    p4, p1 = book_params(cfg)
    capital = float(cfg.get("trendCombo.startCapital"))
    if not state or state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "mode": "paper", "startedAt": now_ms, "startCapital": capital,
                 "book4h": None, "book1h": None, "equityCurve": [{"t": now_ms, "equity": capital}]}
    st = copy.deepcopy(state)
    cash = st["startCapital"] + _realized(st["book4h"]) + _realized(st["book1h"])
    st["book4h"] = step_book(st["book4h"], p4, candles4, funding, now_ms, cash, H4_MS)
    cash = st["startCapital"] + _realized(st["book4h"]) + _realized(st["book1h"])
    st["book1h"] = step_book(st["book1h"], p1, candles1, funding, now_ms, cash, H1_MS,
                             gate=gate_from(st["book4h"]), trend_candles=candles4.get(p4["trendSymbol"]))
    s4, s1 = st["book4h"]["stats"], st["book1h"]["stats"]
    realized = st["startCapital"] + s4["realizedPnl"] + s1["realizedPnl"]
    marked = realized + s4["unrealizedPnl"] + s1["unrealizedPnl"]
    if st["book4h"].pop("hadEvents") | st["book1h"].pop("hadEvents"):
        st["equityCurve"].append({"t": now_ms, "equity": round(marked, 6)})
        st["equityCurve"] = st["equityCurve"][-MAX_CURVE_POINTS:]
    peak, max_dd = st["startCapital"], 0.0
    for point in st["equityCurve"]:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = max(max_dd, 1 - point["equity"] / peak)
    st["equity"] = round(realized, 6)
    st["stats"] = {
        "equityMarked": round(marked, 6), "returnPct": round(100 * (marked / st["startCapital"] - 1), 4),
        "maxDrawdownPct": round(100 * max_dd, 4),
        "trades": s4["trades"] + s1["trades"], "openPositions": s4["openPositions"] + s1["openPositions"],
        "exposure": round((s4["notional"] + s1["notional"]) / realized, 4) if realized > 0 else None,
    }
    st["params"] = {"startCapital": capital, "risk4hPct": p4["riskPct"], "risk1hPct": p1["riskPct"],
                    "h1ChannelBars": p1["channelBars"], "h1TrailBars": p1["trailBars"], "h1StopAtr": p1["stopAtr"]}
    st["generatedAt"] = now_ms
    return st


def run(cfg: Config, client=None, now_ms: int | None = None, log=print) -> dict | None:
    if not cfg.get("trendCombo.enabled", False):
        log("trend-combo: disabled")
        return None
    p4, p1 = book_params(cfg)
    now = now_ms or int(time.time() * 1000)
    state = persist.load_json(FILE, None)  # raises rather than wiping an unreadable history
    if client is None:
        from .main import make_client
        client = make_client(cfg)
    wanted = list(dict.fromkeys([*p4["symbols"], p4["trendSymbol"]]))
    candles4, candles1, errors = {}, {}, []
    for sym in wanted:
        try:
            candles4[sym] = client.klines(sym, "4h", int(p4["klineLimit"]))
        except MarketDataError as exc:
            errors.append(f"{sym} 4h: {exc}")
    for sym in p1["symbols"]:
        try:
            candles1[sym] = client.klines(sym, "1h", int(p1["klineLimit"]))
        except MarketDataError as exc:
            errors.append(f"{sym} 1h: {exc}")
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
    if p4["trendSymbol"] not in candles4:
        # Without the trend filter no bar may be consumed; keep state and retry next run.
        new = copy.deepcopy(state) if state and state.get("version") == STATE_VERSION else combine(
            None, cfg, {}, {}, {}, now)
        new["generatedAt"] = now
    else:
        new = combine(state, cfg, candles4, candles1, funding, now)
    new["lastRun"] = {"at": now, "status": "ok" if not errors else "degraded", "errors": errors[:10]}
    persist.write_data_file(FILE, new)
    s = new.get("stats", {})
    log(f"trend-combo: equity {s.get('equityMarked')} | open {s.get('openPositions')} | trades {s.get('trades')}"
        + (f" | errors: {'; '.join(errors)}" if errors else ""))
    return new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tendance 4H + renfort 1H paper-trading step")
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
