"""Independent heartbeat watchdog core.

The watchdog does not depend on the upper execution loop: a separate process
or thread owns it and calls :meth:`HeartbeatWatchdog.poll` on its own clock.
The timeout callback must produce a fail-closed output (zero base duty and/or
arm hold); it must never start a motion. A trip is latched: only an explicit
:meth:`arm` clears it, so a recovered upper layer cannot silently resume.

The timeout is the caller's policy value. The 200 ms default used by the base
intake is provisional and has not been validated on hardware.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Any

DEFAULT_TIMEOUT_S = 0.2


class HeartbeatWatchdog:
    def __init__(
        self,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        on_timeout: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = "base",
    ) -> None:
        if float(timeout_s) <= 0:
            raise ValueError("watchdog timeout must be positive")
        self.timeout_s = float(timeout_s)
        self.on_timeout = on_timeout
        self._clock = clock
        self.name = str(name)
        self._lock = threading.RLock()
        self.armed = False
        self.expired = False
        self.last_heartbeat: float | None = None
        self.trip_count = 0
        self.last_error = ""
        self.last_trip_at: float | None = None

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    def arm(self, now: float | None = None) -> None:
        """Start (or restart) supervision; clears a latched trip."""

        with self._lock:
            self.armed = True
            self.expired = False
            self.last_heartbeat = self._now(now)

    def disarm(self) -> None:
        with self._lock:
            self.armed = False
            self.expired = False
            self.last_heartbeat = None

    def heartbeat(self, now: float | None = None) -> bool:
        """Record liveness. Returns False when the watchdog is not running.

        A heartbeat after a trip does not clear the trip; call ``arm`` after
        the operator has restored the output path explicitly.
        """

        with self._lock:
            if not self.armed or self.expired:
                return False
            self.last_heartbeat = self._now(now)
            return True

    def poll(self, now: float | None = None) -> bool:
        """Check the deadline. Returns True on a new trip.

        The callback runs outside the lock (a callback may call ``status`` or
        ``disarm``); callback errors are recorded but do not clear the trip.
        """

        now = self._now(now)
        with self._lock:
            if not self.armed or self.expired:
                return False
            if self.last_heartbeat is None:
                tripped = True
            else:
                tripped = (now - self.last_heartbeat) > self.timeout_s
            if not tripped:
                return False
            self.expired = True
            self.trip_count += 1
            self.last_trip_at = now
            callback = self.on_timeout
        if callback is not None:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - watchdog boundary
                with self._lock:
                    self.last_error = str(exc)
        return True

    # ``check`` is the name used by the authority design document.
    def check(self, now: float | None = None) -> bool:
        return self.poll(now)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "armed": self.armed,
                "expired": self.expired,
                "timeout_s": self.timeout_s,
                "last_heartbeat": self.last_heartbeat,
                "trip_count": self.trip_count,
                "last_trip_at": self.last_trip_at,
                "last_error": self.last_error,
                "has_callback": self.on_timeout is not None,
            }
