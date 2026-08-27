"""Idle detection: is the worker active enough, or does it need a wake?"""

from __future__ import annotations

import time

from engine.config import Settings


class IdleDetector:
    def __init__(self, settings: Settings):
        self.settings = settings

    def is_idle(self, last_activity_ts: float | None, now: float | None = None) -> bool:
        """True when there is no recent activity (or no activity recorded at all)."""
        now = now if now is not None else time.time()
        if last_activity_ts is None:
            return True
        return (now - last_activity_ts) > float(self.settings.loop.idle_timeout_min) * 60.0

    def idle_for_minutes(self, last_activity_ts: float | None, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        if last_activity_ts is None:
            return float("inf")
        return max((now - last_activity_ts) / 60.0, 0.0)
