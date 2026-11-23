from __future__ import annotations

from pathlib import Path

from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock


def test_lease_preempts_lower_priority(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    low = stack.lease(holder="low", scope="arm", priority=40)
    high = stack.lease(holder="high", scope="arm", priority=60)
    assert not stack.authority.check(low.lease_id, "arm", stack.clock.now()).ok
    assert stack.authority.check(high.lease_id, "arm", stack.clock.now()).ok


def test_scope_mismatch_rejected(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    lease = stack.lease(holder="arm", scope="arm", priority=60)
    check = stack.authority.check(lease.lease_id, "base", stack.clock.now())
    assert not check.ok
    assert check.code == ErrorCode.NOT_LEASE_HOLDER.value


def test_lease_expiry(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    lease = stack.lease(holder="short", scope="arm", priority=60, ttl_ms=100)
    stack.clock.advance(0.2)
    check = stack.authority.check(lease.lease_id, "arm", stack.clock.now())
    assert not check.ok
    assert check.code == ErrorCode.LEASE_EXPIRED.value
