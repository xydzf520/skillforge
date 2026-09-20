"""Time window check helper."""

from __future__ import annotations

from datetime import datetime, time


def in_window(current: datetime, start: time, end: time) -> bool:
    now = current.time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end
