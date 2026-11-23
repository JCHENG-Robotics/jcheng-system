from __future__ import annotations

from pathlib import Path

from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock


def test_guard_allows_bridge_only() -> None:
    guard = PublisherGuard()
    assert guard.check({"jcheng_hardware"}).ok
    result = guard.check({"jcheng_hardware", "rogue"})
    assert not result.ok
    assert any("rogue" in item for item in result.violations)


def test_guard_latches_safety(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    guard = PublisherGuard()
    guard.enforce(stack.safety, {"jcheng_hardware", "rogue"})
    assert stack.safety.stop_latched
