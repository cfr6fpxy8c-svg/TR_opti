"""Offline funnel diagnosis and threshold sensitivity.

    python -m scanner.diagnose
    python -m scanner.diagnose --sensitivity
    python -m scanner.diagnose --what-if signalModel.minRelVolume=1.0

Why this exists
---------------
The scanner can run for days publishing zero signals, and until now the only
evidence was a reject histogram whose keys embedded live float values, so it
could be neither aggregated nor compared between scans. "No signals" was
indistinguishable from "engine broken".

This tool reads the committed ``data/universe-snapshot.json`` -- the per-symbol
screener row the engine writes every scan -- and replays the *gate* portion of
the signal model over it. That covers the indicator/regime gates, which is
where the funnel is actually collapsing. Structural gates (sweep, CHoCH,
displacement, FVG/OB) need full candle history and are reported as the
remaining population rather than simulated.

It never touches the network, so it works in blocked/offline environments and
in CI.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from .config import Config
from .persist import data_dir

BULLISH = ("strong_bullish", "bullish")
BEARISH = ("strong_bearish", "bearish")

# Gate order mirrors signals.try_setup(); each gate only sees survivors.
GATES = (
    ("htf", "4H/1H alignment"),
    ("regime", "4H regime not RANGING"),
    ("volatility", "ATR% within band"),
    ("adx", "ADX established or emerging"),
    ("rsi", "RSI within band"),
    ("relvol", "Relative volume >= min"),
)


def _passes(row: dict, direction: str, cfg: Config) -> str | None:
    """Return the code of the first gate this row fails, or None if it survives."""
    model = cfg.get("signalModel", {})
    want, opp = (BULLISH, BEARISH) if direction == "long" else (BEARISH, BULLISH)
    strong = "strong_bullish" if direction == "long" else "strong_bearish"

    b4, b1 = row.get("bias4h"), row.get("bias1h")
    if b4 in opp and b1 in opp:
        return "htf"
    htf_ok = (b4 in want) or (b4 == "neutral" and b1 == strong)
    ltf_ok = (b1 in want) or (b1 == "neutral" and b4 == strong)
    if not (htf_ok and ltf_ok):
        return "htf"
    if row.get("regime") == "RANGING":
        return "regime"

    atr_pct = row.get("atrPercent")
    if atr_pct is None or not (model.get("minAtrPercent", 0.1) <= atr_pct
                               <= model.get("maxAtrPercent", 3.0)):
        return "volatility"
    adx = row.get("adx")
    # The screener snapshot has no DI/ADX-slope columns, so the "emerging
    # trend" branch cannot be reproduced here; this is the strict reading and
    # is reported as such.
    if adx is None or adx < model.get("minAdx15m", 15):
        return "adx"
    rsi = row.get("rsi")
    lo, hi = (model.get("rsiLongMin", 50), model.get("rsiLongMax", 72)) if direction == "long" \
        else (model.get("rsiShortMin", 28), model.get("rsiShortMax", 50))
    if rsi is None or not (lo <= rsi <= hi):
        return "rsi"
    rv = row.get("relVolume")
    if rv is None or rv < model.get("minRelVolume", 1.2):
        return "relvol"
    return None


def funnel(rows: list[dict], cfg: Config) -> dict:
    """Per-direction funnel over the screener snapshot."""
    out: dict = {"evaluations": len(rows) * 2, "byDirection": {}, "totals": {}}
    totals: dict[str, int] = {code: 0 for code, _ in GATES}
    survivors_all: list[str] = []
    for direction in ("long", "short"):
        counts = {code: 0 for code, _ in GATES}
        survivors = []
        for row in rows:
            failed = _passes(row, direction, cfg)
            if failed is None:
                survivors.append(row["symbol"])
            else:
                counts[failed] += 1
                totals[failed] += 1
        out["byDirection"][direction] = {"rejected": counts, "survivors": survivors}
        survivors_all.extend(f"{s}/{direction}" for s in survivors)
    out["totals"] = totals
    out["survivors"] = survivors_all
    return out


def _set_dotted(cfg: Config, dotted: str, value) -> None:
    node = cfg._data
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _coerce(text: str):
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    return text


def print_funnel(result: dict, rows: int) -> None:
    print(f"\nGate funnel over {rows} symbols x 2 directions = {result['evaluations']} evaluations")
    print(f"{'gate':<34}{'rejected':>10}{'survivors':>12}")
    print("-" * 56)
    remaining = result["evaluations"]
    for code, label in GATES:
        killed = result["totals"][code]
        remaining -= killed
        print(f"{label:<34}{killed:>10}{remaining:>12}")
    print("-" * 56)
    print(f"{'reaching structural/SMC gates':<34}{'':>10}{remaining:>12}")
    if result["survivors"]:
        print("\nsurvivors: " + ", ".join(result["survivors"]))
    else:
        print("\nsurvivors: none -- the indicator gates alone eliminate every candidate")


def sensitivity(rows: list[dict], cfg: Config) -> list[tuple[str, str, int]]:
    """How many more evaluations survive if one threshold is relaxed."""
    base = len(funnel(rows, cfg)["survivors"])
    knobs = [
        ("signalModel.minRelVolume", [1.0, 1.1, 1.2, 1.3]),
        ("signalModel.minAdx15m", [12, 15, 18, 20]),
        ("signalModel.rsiLongMin", [40, 45, 50]),
        ("signalModel.rsiLongMax", [70, 74, 78]),
        ("signalModel.maxAtrPercent", [3.0, 4.0, 5.0]),
    ]
    out = []
    for dotted, values in knobs:
        current = cfg.get(dotted)
        for value in values:
            trial = Config(copy.deepcopy(cfg._data))
            _set_dotted(trial, dotted, value)
            n = len(funnel(rows, trial)["survivors"])
            marker = "  <- current" if value == current else ""
            out.append((dotted, f"{value}{marker}", n - base))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline signal-funnel diagnosis")
    parser.add_argument("--snapshot", default=None,
                        help="path to universe-snapshot.json (default: data/)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--sensitivity", action="store_true",
                        help="show how each threshold changes the survivor count")
    parser.add_argument("--what-if", action="append", default=[],
                        metavar="dotted.key=value",
                        help="override a config value for this run (repeatable)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = Path(args.snapshot) if args.snapshot else data_dir() / "universe-snapshot.json"
    if not path.exists():
        print(f"no snapshot at {path} -- run a scan first", file=sys.stderr)
        return 1
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    if not rows:
        print("snapshot has no rows", file=sys.stderr)
        return 1

    cfg = Config.load(args.config)
    for override in args.what_if:
        if "=" not in override:
            print(f"bad --what-if {override!r}, expected dotted.key=value", file=sys.stderr)
            return 2
        key, _, raw = override.partition("=")
        _set_dotted(cfg, key.strip(), _coerce(raw.strip()))
        print(f"override: {key.strip()} = {_coerce(raw.strip())}")

    result = funnel(rows, cfg)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print_funnel(result, len(rows))
    if args.sensitivity:
        print("\nThreshold sensitivity (change in surviving evaluations vs current config)")
        print(f"{'parameter':<34}{'value':<18}{'delta':>8}")
        print("-" * 60)
        for dotted, value, delta in sensitivity(rows, cfg):
            print(f"{dotted:<34}{value:<18}{delta:>+8}")
        print("\nA relaxed gate is not a better gate: every extra survivor still has to "
              "clear\nstructure, SMC, risk/RR and the 80-point score. Re-run replay before "
              "committing\na change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
