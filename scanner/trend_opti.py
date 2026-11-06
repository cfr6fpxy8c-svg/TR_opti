"""4H + 1H optimise -- third paper portfolio, fully separate from trend4h.py and trend_combo.py.

Same two books as the 4H + renfort 1H portfolio, with three settings found in research (2019 -> 10/2024,
checked on 11/2024 ->):
  * slower BTC trend filter: 4H close vs EMA ``trendEma`` (300 instead of 200);
  * at most ``maxSameSide`` open positions in the same direction, both books counted together
    (the 5 cryptos move together, so 5 longs are one big bet);
  * doubled risk per trade (``risk4hPct`` / ``risk1hPct``).
Unchanged engine helpers are imported from trend_combo (that module is never modified for this one).
State lives in data/trend-opti-paper.json.
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
from .indicators import ema
from .market_data import Candle, MarketDataError
from .trend_combo import (GATE_REASON, H1_MS, H4_MS, MAX_CURVE_POINTS, MAX_EVALUATIONS, STATE_VERSION,
                          _accrue_funding, _book_stats, _fill_pending, _manage, _new_book, _prepare, _realized,
                          _shadow_stats, _update_shadows, _watch, gate_from)

FILE = "trend-opti-paper.json"


def side_cap_reason(max_same: int) -> str:
    return f"deja {max_same} positions ouvertes dans ce sens"


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
    if gate is not None:
        reasons.extend(gate(st, sym, side_name, bar.closeTime + 1))
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


def step_book(state: dict | None, params: dict, candles: dict[str, list[Candle]], funding: dict[str, float],
              now_ms: int, equity: float, interval_ms: int, gate=None,
              trend_candles: list[Candle] | None = None) -> dict:
    """Same as trend_combo.step_book; the gate sees the book and returns rejection reasons."""
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
    c = cfg.get("trendOpti", {})
    common = {**base, "trendEma": int(c["trendEma"]), "startCapital": float(c["startCapital"])}
    p4 = {**common, "riskPct": float(c["risk4hPct"])}
    p1 = {**common, "riskPct": float(c["risk1hPct"]),
          "channelBars": int(c["h1ChannelBars"]), "trailBars": int(c["h1TrailBars"]),
          "stopAtr": float(c["h1StopAtr"]), "klineLimit": int(c["h1KlineLimit"])}
    return p4, p1


def _same_side(book: dict | None, side: str) -> int:
    book = book or {}
    return sum(1 for p in book.get("positions", []) + book.get("pending", []) if p["side"] == side)


def cap_gate(other: dict | None, max_same: int):
    """Reject once ``max_same`` positions (both books together) already run in that direction."""
    def gate(st: dict, sym: str, side: str, t: int) -> list[str]:
        return [side_cap_reason(max_same)] if _same_side(st, side) + _same_side(other, side) >= max_same else []
    return gate


def h1_gate(book4h: dict, max_same: int):
    held, cap = gate_from(book4h), cap_gate(book4h, max_same)

    def gate(st: dict, sym: str, side: str, t: int) -> list[str]:
        return ([] if held(sym, side, t) else [GATE_REASON]) + cap(st, sym, side, t)
    return gate


def combine(state: dict | None, cfg: Config, candles4: dict, candles1: dict, funding: dict, now_ms: int) -> dict:
    p4, p1 = book_params(cfg)
    capital = float(cfg.get("trendOpti.startCapital"))
    max_same = int(cfg.get("trendOpti.maxSameSide"))
    if not state or state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "mode": "paper", "startedAt": now_ms, "startCapital": capital,
                 "book4h": None, "book1h": None, "equityCurve": [{"t": now_ms, "equity": capital}]}
    st = copy.deepcopy(state)
    cash = st["startCapital"] + _realized(st["book4h"]) + _realized(st["book1h"])
    st["book4h"] = step_book(st["book4h"], p4, candles4, funding, now_ms, cash, H4_MS,
                             gate=cap_gate(st["book1h"], max_same))
    cash = st["startCapital"] + _realized(st["book4h"]) + _realized(st["book1h"])
    st["book1h"] = step_book(st["book1h"], p1, candles1, funding, now_ms, cash, H1_MS,
                             gate=h1_gate(st["book4h"], max_same), trend_candles=candles4.get(p4["trendSymbol"]))
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
                    "trendEma": p4["trendEma"], "maxSameSide": max_same,
                    "h1ChannelBars": p1["channelBars"], "h1TrailBars": p1["trailBars"], "h1StopAtr": p1["stopAtr"]}
    st["generatedAt"] = now_ms
    return st


def run(cfg: Config, client=None, now_ms: int | None = None, log=print) -> dict | None:
    if not cfg.get("trendOpti.enabled", False):
        log("trend-opti: disabled")
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
    log(f"trend-opti: equity {s.get('equityMarked')} | open {s.get('openPositions')} | trades {s.get('trades')}"
        + (f" | errors: {'; '.join(errors)}" if errors else ""))
    return new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="4H + 1H optimise paper-trading step")
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
