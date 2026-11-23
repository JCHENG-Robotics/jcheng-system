from __future__ import annotations

from pathlib import Path

import pytest

from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock


def _armed(root: Path) -> MockStack:
    stack = MockStack.build(root, clock=ManualClock())
    stack.arm()
    return stack


def test_step_is_approved(root: Path) -> None:
    stack = _armed(root)
    lease = stack.lease()
    report = stack.arbiter.validate(stack.command(lease_id=lease.lease_id))
    assert report.approved


def test_joint_limit_veto(root: Path) -> None:
    stack = _armed(root)
    lease = stack.lease()
    command = stack.command(
        lease_id=lease.lease_id,
        joint_names=["joint2"],
        target_positions=[2.0],
    )
    report = stack.arbiter.validate(command)
    assert not report.approved
    assert report.reason_code == ErrorCode.JOINT_LIMIT.value


def test_stale_state_latches(root: Path) -> None:
    stack = _armed(root)
    lease = stack.lease()
    now = stack.clock.now()
    stack.safety.update_state(stack.hardware.state(now), now - 1.0)
    report = stack.arbiter.validate(stack.command(lease_id=lease.lease_id))
    assert report.reason_code == ErrorCode.STATE_STALE.value
    assert stack.safety.stop_latched


def test_wrong_profile_hash_rejects_arming(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    stack.sync_state()
    with pytest.raises(MotionRejected) as exc:
        stack.safety.arm(
            "operator",
            "checklist",
            stack.safety.limits.arm_confirm_phrase,
            stack.version,
            "sha256:" + "2" * 64,
        )
    assert exc.value.code == ErrorCode.PROFILE_HASH_MISMATCH.value
    assert stack.safety.state == "DISARMED"
