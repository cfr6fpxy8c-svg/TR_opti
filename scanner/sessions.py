"""ICT kill-zone / session tagging (deterministic, display-only).

Maps a millisecond timestamp to a named trading session window in UTC
(e.g. ASIA range, London open, New York AM). Crypto trades 24/7 — these tags
are *context* on signal cards, never a scoring or rejection factor unless a
future config explicitly enables it.
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone


def session_at(ms: int, kill_zones: list[dict]) -> str:
    hour = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).hour
    for kz in kill_zones:
        start, end = int(kz.get("startUtcHour", 0)), int(kz.get("endUtcHour", 0))
        if start <= end:
            if start <= hour < end:
                return str(kz.get("name", "?")).upper()
        else:  # window wraps midnight (e.g. 22 -> 4)
            if hour >= start or hour < end:
                return str(kz.get("name", "?")).upper()
    return "OFF"


def default_kill_zones() -> list[dict]:
    return [
        {"name": "ASIA", "startUtcHour": 0, "endUtcHour": 6},
        {"name": "LONDON", "startUtcHour": 7, "endUtcHour": 10},
        {"name": "NEW_YORK", "startUtcHour": 12, "endUtcHour": 15},
    ]
