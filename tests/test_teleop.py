from __future__ import annotations

import pytest

from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.stack import MockStack
from jcheng_system_core.teleop import TeleopIntake, TeleopSample
from jcheng_system_core.timebase import ManualClock


def _stack(root):
    stack = MockStack.build(root, clock=ManualClock())
    stack.arm()
    return stack


def _sample(stack, lease_id: str, *, seq: int = 0, deadman: bool = True, velocities=(0.1, 0.0, 0.0, 0.0, 0.0), stamp_s: float = 0.0, received_s: float = 0.0):
    return TeleopSample(
        seq=seq,
        lease_id=lease_id,
        source="jcheng-teleop",
        contract=stack.version,
        mode="JOINT_VELOCITY",
        joint_velocities_rad_s=velocities,
        deadman=deadman,
        stamp_s=stamp_s,
        received_s=received_s,
    )


def test_single_step_goes_through_arbiter(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    intake.on_sample(_sample(stack, lease.lease_id), now=0.0)
    result = intake.tick(0.0)
    assert result is not None
    assert result.outcome == "COMPLETED"
    assert result.bus_verified is True
    assert stack.hardware.positions[0] == pytest.approx(0.01, abs=1e-6)


def test_no_lease_is_rejected(root):
    stack = _stack(root)
    intake = TeleopIntake(stack)
    with pytest.raises(MotionRejected) as exc:
        intake.on_sample(_sample(stack, "missing"), now=0.0)
    assert exc.value.code == ErrorCode.NO_LEASE.value


def test_sequence_must_increase(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1), now=0.0)
    with pytest.raises(MotionRejected) as exc:
        intake.on_sample(_sample(stack, lease.lease_id, seq=1), now=0.01)
    assert exc.value.code == ErrorCode.INVALID_COMMAND.value


def test_deadman_release_is_non_latching_abort(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1), now=0.0)
    intake.on_sample(_sample(stack, lease.lease_id, seq=2, deadman=False), now=0.01)
    assert intake.active is False
    assert stack.safety.stop_latched is False


def test_input_timeout_aborts_without_latch(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1), now=0.0)
    intake.tick(0.0)
    assert intake.tick(0.31) is None
    assert intake.active is False
    assert stack.safety.stop_latched is False


def test_explicit_stop_latches(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1), now=0.0)
    result = intake.stop("operator_stop", now=0.01)
    assert result.accepted is True
    assert stack.safety.stop_latched is True


def test_cartesian_mode_is_rejected(root):
    stack = _stack(root)
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    sample = _sample(stack, lease.lease_id, seq=1)
    sample = TeleopSample(**{**sample.__dict__, "mode": "CARTESIAN_VELOCITY"})
    intake = TeleopIntake(stack)
    with pytest.raises(MotionRejected):
        intake.on_sample(sample, now=0.0)


def test_gate2_two_joint_profile_steps(root):
    stack = MockStack.build(root, robot="gate2_arm", clock=ManualClock())
    stack.arm()
    lease = stack.lease(holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True)
    intake = TeleopIntake(stack)
    sample = _sample(stack, lease.lease_id, velocities=(0.5, -0.5))
    intake.on_sample(sample, now=0.0)
    result = intake.tick(0.0)
    assert result is not None and result.outcome == "COMPLETED"
    assert stack.hardware.positions == pytest.approx([0.05, -0.05], abs=1e-6)
