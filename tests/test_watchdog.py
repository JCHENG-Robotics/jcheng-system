"""Independent heartbeat watchdog tests.

The watchdog is a pure core with an injected clock: it trips when the upper
loop is blocked (thread test), when a heartbeat source such as the gateway
exits, and when it is armed without any heartbeat. The timeout callback must
produce a stop/zero output; a latched trip only clears on an explicit re-arm.
"""

from __future__ import annotations

from pathlib import Path
import threading

from jcheng_system_core.base import BaseAuthority, BaseConfig, BaseIntent
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock
from jcheng_system_core.watchdog import HeartbeatWatchdog

ROOT = Path(__file__).resolve().parents[1]


class Counter:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self) -> None:
        with self.lock:
            self.calls += 1


def test_timeout_trips_once_and_stays_latched() -> None:
    callback = Counter()
    watchdog = HeartbeatWatchdog(0.2, callback, name="base")
    watchdog.arm(0.0)
    assert watchdog.heartbeat(0.05) is True
    assert watchdog.poll(0.2) is False
    assert watchdog.poll(0.251) is True
    assert callback.calls == 1
    assert watchdog.expired is True
    # A late heartbeat/recovery does not silently resume supervision.
    assert watchdog.heartbeat(0.3) is False
    assert watchdog.poll(0.9) is False
    assert callback.calls == 1
    assert watchdog.trip_count == 1

    watchdog.arm(1.0)
    assert watchdog.expired is False
    assert watchdog.heartbeat(1.05) is True
    assert watchdog.poll(1.1) is False


def test_no_heartbeat_after_arm_trips_fail_closed() -> None:
    callback = Counter()
    watchdog = HeartbeatWatchdog(0.2, callback)
    watchdog.arm(0.0)
    assert watchdog.poll(0.0) is False
    # last_heartbeat is seeded by arm, so the timeout above is based on arm.
    assert watchdog.poll(0.201) is True
    assert callback.calls == 1
    assert watchdog.expired is True


def test_watchdog_runs_while_the_upper_loop_is_blocked() -> None:
    callback = Counter()
    watchdog = HeartbeatWatchdog(0.1, callback)
    watchdog.arm(0.0)
    blocked = threading.Event()
    release = threading.Event()

    def upper_loop() -> None:
        # A blocked execution loop cannot call tick(); only the separate
        # watchdog poll can notice the missing heartbeat.
        blocked.set()
        release.wait(2.0)

    thread = threading.Thread(target=upper_loop)
    thread.start()
    assert blocked.wait(1.0)
    assert watchdog.poll(0.15) is True
    assert callback.calls == 1
    assert thread.is_alive() is True
    release.set()
    thread.join(2.0)
    assert thread.is_alive() is False


def test_gateway_exit_is_a_heartbeat_loss() -> None:
    callback = Counter()
    watchdog = HeartbeatWatchdog(0.2, callback, name="base-intake")
    watchdog.arm(0.0)
    # The gateway/heartbeat source is alive for a while.
    for now in (0.05, 0.1, 0.15):
        assert watchdog.heartbeat(now) is True
        assert watchdog.poll(now) is False
    # The gateway exits: no more heartbeats. The independent poller trips.
    assert watchdog.poll(0.36) is True
    assert callback.calls == 1
    assert watchdog.status()["expired"] is True


def test_callback_failure_keeps_the_trip_and_records_the_error() -> None:
    def boom() -> None:
        raise RuntimeError("zero output unavailable")

    watchdog = HeartbeatWatchdog(0.1, boom)
    watchdog.arm(0.0)
    assert watchdog.poll(0.2) is True
    status = watchdog.status()
    assert status["expired"] is True
    assert status["last_error"] == "zero output unavailable"


def test_disarmed_watchdog_never_trips() -> None:
    callback = Counter()
    watchdog = HeartbeatWatchdog(0.1, callback)
    assert watchdog.heartbeat(0.0) is False
    assert watchdog.poll(10.0) is False
    watchdog.arm(0.0)
    watchdog.disarm()
    assert watchdog.poll(10.0) is False
    assert callback.calls == 0


def test_timeout_callback_stops_the_base_output() -> None:
    clock = ManualClock()
    stack = MockStack.build(ROOT, clock=clock)
    outputs: list[object] = []
    base = BaseAuthority(
        authority=stack.authority,
        clock=clock,
        sink=lambda intent, command: outputs.append(command),
        config=BaseConfig(enabled=True, intent_timeout_ms=200.0),
        safety=stack.safety,
        contract=stack.version,
    )
    lease = stack.authority.acquire(
        holder="nav",
        scope="base",
        mode="exclusive",
        priority=50,
        ttl_ms=30_000,
        deadman_required=False,
        contract=stack.version,
    )
    intent = BaseIntent(
        contract=stack.version,
        lease_id=lease.lease_id,
        holder="nav",
        command_id="cmd-1",
        seq=1,
        linear_x=0.05,
        source="test",
    )
    assert base.submit(intent, now=0.0).accepted is True
    assert outputs[-1].is_zero is False

    watchdog = HeartbeatWatchdog(
        0.1,
        lambda: base.stop("watchdog", "heartbeat lost"),
        name="base",
    )
    watchdog.arm(0.0)
    assert watchdog.poll(0.15) is True

    assert base.state == "STOP_LATCHED"
    assert stack.safety.stop_latched is True
    assert outputs[-1].is_zero is True
    assert base.tick(0.2) is None
    assert outputs[-1].is_zero is True
