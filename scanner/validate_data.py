"""Standalone JSON validation CLI (used by the GitHub Actions workflow).

    python -m scanner.validate_data

Exits non-zero if any /data JSON file is unparseable or if the signals
payload fails integrity validation. Invalid data must never reach the
dashboard/commit stage.
"""
from __future__ import annotations

import json
import sys

from .config import Config, repo_root
from .validation import validate_signals_payload
from .signals import ACTIVE_STATUSES


def main() -> int:
    cfg = Config.load()
    errors: list[str] = []
    data = repo_root() / "data"
    files = ["signals.json", "performance.json", "system-status.json",
             "market-snapshots.json", "latest-chart.json", "universe-snapshot.json",
             "trend4h-paper.json", "ai-analyst.json", "trend-combo-paper.json", "ai-combo.json",
             "trend-opti-paper.json"]
    payloads: dict[str, object] = {}
    for name in files:
        path = data / name
        if not path.exists():
            errors.append(f"{name}: missing")
            continue
        try:
            if path.is_symlink() or path.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("unsafe path or excessive file size")
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = list if name == "market-snapshots.json" else dict
            if not isinstance(payload, expected):
                raise ValueError("unexpected JSON root type")
            mirror = repo_root() / "frontend" / "data" / name
            if mirror.is_symlink() or mirror.read_bytes() != path.read_bytes():
                raise ValueError("frontend mirror differs or is unsafe")
            payloads[name] = payload
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{name}: invalid JSON ({exc})")

    if "signals.json" in payloads:
        import time
        errs = validate_signals_payload(payloads["signals.json"], cfg, int(time.time() * 1000))
        errors.extend(f"signals.json: {e}" for e in errs)
        signals = payloads["signals.json"].get("signals", [])
        if not isinstance(signals, list):
            signals = []
        statuses = [s.get("status") for s in signals if isinstance(s, dict)]
        print(f"signals.json: {len(signals)} signals, "
              f"{sum(1 for s in statuses if isinstance(s, str) and s in ACTIVE_STATUSES)} active")
        allowed = {"WAITING_TRIGGER", "TRIGGERED", "WIN", "LOSS", "EXPIRED", "AMBIGUOUS", "CANCELLED"}
        bad = [s for s in statuses if not isinstance(s, str) or s not in allowed]
        if bad:
            errors.append("signals.json: unknown or malformed statuses")

    if "universe-snapshot.json" in payloads:
        snap = payloads["universe-snapshot.json"]
        rows = snap.get("rows")
        if not isinstance(rows, list):
            errors.append("universe-snapshot.json: rows must be a list")
        else:
            print(f"universe-snapshot.json: {len(rows)} screener rows")

    if "trend4h-paper.json" in payloads:
        paper = payloads["trend4h-paper.json"]
        if not isinstance(paper.get("positions"), list) or not isinstance(paper.get("trades"), list):
            errors.append("trend4h-paper.json: positions and trades must be lists")
        elif paper.get("version"):
            equity = paper.get("equity")
            if not isinstance(equity, (int, float)) or equity != equity:
                errors.append("trend4h-paper.json: equity must be a number")
            else:
                print(f"trend4h-paper.json: equity {equity:.2f}, {len(paper['positions'])} open, "
                      f"{len(paper['trades'])} closed")

    if "ai-analyst.json" in payloads:
        analyses = payloads["ai-analyst.json"].get("analyses")
        if not isinstance(analyses, list):
            errors.append("ai-analyst.json: analyses must be a list")
        else:
            print(f"ai-analyst.json: {len(analyses)} analyses")

    for name in ("trend-combo-paper.json", "trend-opti-paper.json"):
        if name not in payloads:
            continue
        combo = payloads[name]
        if combo.get("version") and not all(isinstance(combo.get(k), dict) for k in ("book4h", "book1h")):
            errors.append(f"{name}: book4h and book1h must be objects")
        elif combo.get("version"):
            print(f"{name}: equity {combo.get('stats', {}).get('equityMarked')}")

    if "ai-combo.json" in payloads and not isinstance(payloads["ai-combo.json"].get("analyses"), list):
        errors.append("ai-combo.json: analyses must be a list")

    if "system-status.json" in payloads:
        st = payloads["system-status.json"]
        if not isinstance(st.get("health"), str):
            errors.append("system-status.json: health missing")

    if "latest-chart.json" in payloads:
        chart = payloads["latest-chart.json"]
        if not isinstance(chart.get("candles"), list):
            errors.append("latest-chart.json: candles must be a list")
        elif chart["candles"]:
            required = {"openTime", "open", "high", "low", "close"}
            if not required.issubset(chart["candles"][-1]):
                errors.append("latest-chart.json: last candle is missing OHLC fields")

    if errors:
        for e in errors:
            print(f"[VALIDATION ERROR] {e}", file=sys.stderr)
        return 1
    print("data validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
