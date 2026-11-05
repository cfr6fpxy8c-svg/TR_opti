"""JSON persistence: atomic writes to /data (source of truth) mirrored into
/frontend/data for static hosting on Cloudflare Pages. Retention applied to
market snapshots and logs; signal history is kept forever (persistent).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import repo_root

DATA_FILES = ("signals.json", "performance.json", "system-status.json",
              "market-snapshots.json", "latest-chart.json", "universe-snapshot.json",
              "trend4h-paper.json", "ai-analyst.json", "trend-combo-paper.json", "ai-combo.json")


def _data_path(directory: Path, name: str) -> Path:
    if name not in DATA_FILES:
        raise ValueError("unexpected data filename")
    if any(parent.is_symlink() for parent in (directory, *directory.parents)):
        raise ValueError("data directories must not contain symlinks")
    path = directory / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("data path must stay within its directory")
    return path


def data_dir() -> Path:
    return repo_root() / "data"


def frontend_data_dir() -> Path:
    return repo_root() / "frontend" / "data"


def load_json(name: str, default):
    path = _data_path(data_dir(), name)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        # Never silently replace a corrupted trade history with an empty one.
        raise ValueError(f"refusing to reset unreadable data file: {name}") from exc


def atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False, default=str, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_data_file(name: str, payload) -> None:
    # Validate both destinations before writing either copy.
    target = _data_path(data_dir(), name)
    mirror = _data_path(frontend_data_dir(), name)
    atomic_write_json(target, payload)
    atomic_write_json(mirror, payload)


def seed_empty_files() -> None:
    defaults = {
        "signals.json": {"generatedAt": 0, "signals": []},
        "performance.json": {"generatedAt": 0},
        "system-status.json": {"systemOnline": False, "health": "INITIALISING",
                               "lastSuccessfulScan": None, "logs": []},
        "market-snapshots.json": [],
        "universe-snapshot.json": {"generatedAt": 0, "rows": []},
        "trend4h-paper.json": {"version": 0, "generatedAt": 0, "positions": [], "trades": []},
        "ai-analyst.json": {"version": 0, "generatedAt": 0, "analyses": []},
        "trend-combo-paper.json": {"version": 0, "generatedAt": 0},
        "ai-combo.json": {"version": 0, "generatedAt": 0, "analyses": []},
        "latest-chart.json": {"symbol": "BTCUSDT", "timeframe": "15m",
                              "generatedAt": 0, "candles": [], "indicators": {},
                              "context": {}, "structure": {}},
    }
    for name, payload in defaults.items():
        if not (data_dir() / name).exists():
            write_data_file(name, payload)
