"""Replay / backtest mode (spec §35).

Replays history candle-by-candle: at each step t the engine receives ONLY
candles with openTime <= step candle (sliced 4H/1H/15M frames), so signal
generation is exactly what it would have been live -- no lookahead by
construction. Outcomes are then evaluated with the subsequent candles (the
point of a backtest).

Usage:
    python -m scanner.replay --symbols BTCUSDT ETHUSDT --candles 700
    python -m scanner.replay --symbols BTCUSDT --determinism-check

Notes:
* Deterministic: same data + config -> byte-identical signals (verified by
  --determinism-check).
* Sample-size honesty: short replays produce few/no signals because the
  quality gate intentionally rejects most setups.
"""
from __future__ import annotations

import argparse
import bisect
import json
import time
from collections import Counter
from pathlib import Path

from .config import Config
from .market_data import INTERVAL_MS, MarketDataClient, drop_incomplete
from .analysis import analyze_symbol
from .signals import generate_signals, ACTIVE_STATUSES
from .outcomes import update_outcomes
from .performance import compute_performance
from .main import make_client
from . import persist
from .persist import atomic_write_json, data_dir

WARMUP_15M = 240  # bars needed before the first evaluation step (EMA200 etc.)


def _klines_paged(client: MarketDataClient, symbol: str, tf: str, count: int) -> list:
    if count <= 1500:
        return client.klines(symbol, tf, count)
    step = INTERVAL_MS[tf]
    start = int(time.time() * 1000) - count * step
    out: list = []
    while True:
        page = client.klines_since(symbol, tf, start)
        out.extend([c for c in page if not out or c.openTime > out[-1].openTime])
        if len(page) < 1000:
            return out
        start = page[-1].openTime + step


def fetch_history(client: MarketDataClient, symbol: str, limits: dict) -> dict:
    frames = {}
    for tf in ("1d", "4h", "1h", "15m"):
        frames[tf] = drop_incomplete(_klines_paged(client, symbol, tf, limits.get(tf, 500)))
    return frames


def slice_frames(frames: dict, until_open_ms: int) -> dict:
    """Causal slice: only candles that had opened (and closed) by until_open_ms.

    A 4H candle whose openTime <= until_open_ms may still be unclosed at that
    moment; to stay strictly causal we require closeTime <= until_open_ms +
    its own duration is handled by drop_incomplete at replay time -- here we
    keep candles with closeTime < until_close_ms (end of the 15M step candle).
    """
    until_close_ms = until_open_ms + 900_000  # end of the step's 15m candle
    out = {}
    for tf, candles in frames.items():
        out[tf] = [c for c in candles if c.closeTime <= until_close_ms]
    return out


def replay_symbol(symbol: str, frames: dict, cfg: Config,
                  funnel: Counter | None = None) -> list[dict]:
    """Walk the 15M series; generate + resolve signals exactly as live."""
    candles_15m = frames["15m"]
    signals: list[dict] = []
    # Same candle window per timeframe as a live scan, so indicators warm up identically.
    window = cfg.get("dataSource.klineLimits", {})
    closes = {tf: [c.closeTime for c in cs] for tf, cs in frames.items()}
    start = max(WARMUP_15M, cfg.get("indicators.emaSlow", 200) + 40, window.get("15m", 0))
    for t in range(start, len(candles_15m)):
        step_candle = candles_15m[t]
        now_ms = step_candle.closeTime + 1
        until_close_ms = step_candle.openTime + 900_000
        sliced = {}
        for tf, cs in frames.items():
            end = bisect.bisect_right(closes[tf], until_close_ms)
            sliced[tf] = cs[max(0, end - window.get(tf, end)):end]
        analysis = analyze_symbol(symbol, sliced, cfg, now_ms)
        if not analysis["ok"]:
            if funnel is not None:
                funnel["analysis_not_ok"] += 1
            continue
        new_signals, rejects = generate_signals([analysis], cfg, signals, now_ms,
                                                data_source="replay")
        if funnel is not None:
            funnel["evaluations"] += 1
            funnel["proposed"] += len(new_signals)
            funnel.update(r["code"] for r in rejects)
        signals.extend(new_signals)
        # advance lifecycle with candles closed by now (strictly causal)
        signals = update_outcomes(signals, {symbol: candles_15m}, now_ms,
                                  client=None, allow_1m=False)
    return signals


def run_replay(cfg: Config, symbols: list[str], candles: int, log=print) -> dict:
    client = make_client(cfg)
    base = cfg.get("dataSource.klineLimits", {})
    limits = {tf: base.get(tf, 500) + candles * 900_000 // INTERVAL_MS[tf] + 1
              for tf in ("1d", "4h", "1h", "15m")}
    results: list[dict] = []
    funnel: Counter = Counter()
    for sym in symbols:
        t0 = time.monotonic()
        frames = fetch_history(client, sym, limits)
        need = {"4h": cfg.get("indicators.emaSlow", 200) + 10,
                "1h": cfg.get("indicators.emaSlow", 200) + 10,
                "15m": WARMUP_15M + 10}
        short = [tf for tf, n in need.items() if len(frames.get(tf, [])) < n]
        if short:
            log(f"{sym}: skipped, insufficient history {short}")
            continue
        signals = replay_symbol(sym, frames, cfg, funnel)
        took = round(time.monotonic() - t0, 1)
        log(f"{sym}: replayed {len(frames['15m'])} 15m candles -> {len(signals)} signals ({took}s)")
        results.extend(signals)
    performance = compute_performance(results, cfg, int(time.time() * 1000))
    report = {
        "mode": "replay",
        "symbols": symbols,
        "candles": candles,
        "generatedAt": int(time.time() * 1000),
        "signals": results,
        "performance": performance,
        "funnel": dict(funnel.most_common()),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="historical replay / backtest")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--candles", type=int, default=700, help="15m candles to replay")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=None, help="report path (default data/replay-report.json)")
    parser.add_argument("--determinism-check", action="store_true")
    parser.add_argument("--into-history", action="store_true",
                        help="merge resolved replay proposals into data/signals.json (tagged replay)")
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    report = run_replay(cfg, args.symbols, args.candles)
    if args.determinism_check:
        report2 = run_replay(cfg, args.symbols, args.candles, log=lambda *_: None)
        a = json.dumps(report["signals"], sort_keys=True)
        b = json.dumps(report2["signals"], sort_keys=True)
        if a != b:
            print("[DETERMINISM FAIL] replay produced different signals on identical data",
                  file=__import__("sys").stderr)
            return 1
        print("determinism check OK (identical output on identical input)")

    out = Path(args.out) if args.out else data_dir() / "replay-report.json"
    atomic_write_json(out, report)
    perf = report["performance"]
    print(f"replay report -> {out}")
    print(f"signals: {perf['total']}  wins: {perf['wins']}  losses: {perf['losses']}  "
          f"win rate: {perf['winRate']}% (n={perf['resolvedTrades']})  "
          f"profit factor: {perf['profitFactor']}")
    print(f"funnel: {report['funnel']}")
    if args.into_history:
        print(f"history: {merge_into_history(report['signals'], cfg)} replay proposals added")
    return 0


def merge_into_history(replayed: list[dict], cfg: Config) -> int:
    """Replace the replay part of the history with resolved proposals predating live ones."""
    payload = persist.load_json("signals.json", {"generatedAt": 0, "signals": []})
    live = [s for s in payload.get("signals", []) if s.get("dataSource") != "replay"]
    cutoff = min((s["generatedAt"] for s in live), default=float("inf"))
    fresh = [s for s in replayed if s["status"] not in ACTIVE_STATUSES
             and s["generatedAt"] < cutoff]
    signals = sorted(live + fresh, key=lambda s: s["generatedAt"])
    now_ms = int(time.time() * 1000)
    persist.write_data_file("signals.json", {"generatedAt": now_ms, "signals": signals})
    persist.write_data_file("performance.json", compute_performance(signals, cfg, now_ms))
    return len(fresh)


if __name__ == "__main__":
    raise SystemExit(main())
