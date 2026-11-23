"""Monotonic control time with a wall-clock projection for ISO fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import time


@dataclass
class Clock:
    t0_mono: float = field(default_factory=time.monotonic)
    t0_wall: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def now(self) -> float:
        return time.monotonic() - self.t0_mono

    def iso(self, seconds: float) -> str:
        return (self.t0_wall + timedelta(seconds=float(seconds))).isoformat()


class ManualClock:
    """Deterministic clock for tests and scenarios."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)
        self.t0_wall = datetime(2026, 11, 23, 0, 0, 0, tzinfo=timezone.utc)

    def now(self) -> float:
        return self._now

    def iso(self, seconds: float) -> str:
        return (self.t0_wall + timedelta(seconds=float(seconds))).isoformat()

    def advance(self, delta: float) -> None:
        self._now += float(delta)
