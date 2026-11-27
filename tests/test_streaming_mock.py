"""M6 mock streaming bridge tests: one atomic multi-joint frame per step."""

from __future__ import annotations

import pytest

from jcheng_system_core.config import RobotProfile
from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.stack import MockStack
from jcheng_system_core.streaming import MockStreamingBridge
from jcheng_system_core.teleop import TeleopIntake, TeleopSample
from jcheng_system_core.timebase import ManualClock

JOINTS = 5
VELOCITY = 0.2
VELOCITIES = (VELOCITY, VELOCITY, -VELOCITY, VELOCITY, -VELOCITY)
PERIOD = 0.1
MAX_VELOCITY = 0.5


def _stack(root):
    clock = ManualClock()
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    bridge = MockStreamingBridge(profile, clock)
    stack = MockStack.build(root, clock=clock, hardware=bridge)
    stack.arm()
    return stack, bridge, clock


def _sample(stack, lease_id: str, *, seq: int, received_s: float, deadman: bool = True):
    return TeleopSample(
        seq=seq,
        lease_id=lease_id,
        source="jcheng-teleop",
        contract=stack.version,
        mode="JOINT_VELOCITY",
        joint_velocities_rad_s=VELOCITIES,
        deadman=deadman,
        stamp_s=received_s,
        received_s=received_s,
    )


def _lease(stack):
    return stack.lease(
        holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True
    )


def test_each_step_is_one_atomic_multi_joint_frame(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(
        stack, step_period_s=PERIOD, max_velocity_rad_s=MAX_VELOCITY
    )
    for seq in range(1, 11):
        now = round((seq - 1) * (PERIOD + 0.01) + 0.001, 6)
        clock.advance(now - clock.now())
        stack.sync_state()
        intake.on_sample(_sample(stack, lease.lease_id, seq=seq, received_s=now), now=now)
        result = intake.tick(now)
        assert result is not None and result.outcome == "COMPLETED", (seq, result)
    assert len(bridge.frames) == 10
    for frame in bridge.frames:
        assert frame.names == tuple(stack.profile.joint_names)
        assert len(frame.targets) == JOINTS
        assert frame.duration_s == pytest.approx(PERIOD)
    # One 20 mrad increment per joint per 100 ms step; no serialized joint moves.
    assert bridge.frames[0].targets == pytest.approx((0.02, 0.02, -0.02, 0.02, -0.02))
    assert bridge.frames[-1].targets == pytest.approx((0.2, 0.2, -0.2, 0.2, -0.2))
    assert all(len(frame.names) > 1 for frame in bridge.frames)


def test_deadman_release_stops_frames_without_latching(root) -> None:
    stack, bridge, _clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(stack, step_period_s=PERIOD)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1, received_s=0.0), now=0.0)
    assert intake.tick(0.0) is not None
    frames = len(bridge.frames)
    intake.on_sample(
        _sample(stack, lease.lease_id, seq=2, received_s=0.1, deadman=False), now=0.1
    )
    assert intake.tick(0.1) is None
    assert len(bridge.frames) == frames
    assert stack.safety.stop_latched is False


def test_lease_loss_is_rejected_before_a_frame(root) -> None:
    stack, bridge, _clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(stack, step_period_s=PERIOD)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1, received_s=0.0), now=0.0)
    assert intake.tick(0.0) is not None
    frames = len(bridge.frames)
    stack.authority.revoke(lease.lease_id, "test", "lease lost", stack.version)
    with pytest.raises(MotionRejected) as exc:
        intake.on_sample(
            _sample(stack, lease.lease_id, seq=2, received_s=0.1), now=0.1
        )
    assert exc.value.code in {ErrorCode.NO_LEASE.value, ErrorCode.LEASE_REVOKED.value}
    assert len(bridge.frames) == frames


def test_stop_latch_prevents_further_frames(root) -> None:
    stack, bridge, _clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(stack, step_period_s=PERIOD)
    intake.on_sample(_sample(stack, lease.lease_id, seq=1, received_s=0.0), now=0.0)
    assert intake.tick(0.0) is not None
    frames = len(bridge.frames)
    stack.arbiter.stop("test", "operator stop", 0.05)
    result = intake.tick(0.1)
    assert result is None or result.outcome != "COMPLETED"
    assert len(bridge.frames) == frames
    assert stack.safety.stop_latched is True


def test_streaming_intake_opens_one_session_and_finishes_on_release(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(
        stack, step_period_s=0.05, max_velocity_rad_s=MAX_VELOCITY, streaming=True
    )
    for seq in range(1, 11):
        now = round((seq - 1) * 0.051, 6)
        clock.advance(now - clock.now())
        stack.sync_state()
        intake.on_sample(_sample(stack, lease.lease_id, seq=seq, received_s=now), now=now)
        assert intake.tick(now) is None
    assert len(bridge.frames) == 10
    assert stack.arbiter._active is not None
    frames = len(bridge.frames)
    intake.on_sample(
        _sample(stack, lease.lease_id, seq=11, received_s=0.6, deadman=False), now=0.6
    )
    assert intake.active is False
    assert stack.arbiter._active is None
    assert len(bridge.frames) == frames
    assert stack.safety.stop_latched is False


def test_streaming_intake_stop_latches_without_further_frames(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(stack, step_period_s=0.05, streaming=True)
    for seq in range(1, 4):
        now = round((seq - 1) * 0.051, 6)
        clock.advance(now - clock.now())
        stack.sync_state()
        intake.on_sample(_sample(stack, lease.lease_id, seq=seq, received_s=now), now=now)
        assert intake.tick(now) is None
    frames = len(bridge.frames)
    intake.stop("operator stop", now=0.3)
    assert stack.safety.stop_latched is True
    assert stack.arbiter._active is None or stack.arbiter._active.terminal is not None
    assert len(bridge.frames) == frames


def test_streaming_intake_lease_loss_aborts_the_session(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    intake = TeleopIntake(stack, step_period_s=0.05, streaming=True)
    for seq in range(1, 4):
        now = round((seq - 1) * 0.051, 6)
        clock.advance(now - clock.now())
        stack.sync_state()
        intake.on_sample(_sample(stack, lease.lease_id, seq=seq, received_s=now), now=now)
        assert intake.tick(now) is None
    frames = len(bridge.frames)
    stack.authority.revoke(lease.lease_id, "test", "lease lost", stack.version)
    now = round(3 * 0.051, 6)
    clock.advance(now - clock.now())
    stack.sync_state()
    result = intake.tick(now)
    assert result is not None and result.outcome == "ABORTED"
    assert intake.active is False
    assert stack.safety.stop_latched is True
    assert len(bridge.frames) == frames


def test_single_joint_step_is_not_logged_as_a_stream_frame(root) -> None:
    stack, bridge, _clock = _stack(root)
    lease = _lease(stack)
    from jcheng_system_core.models import MotionCommand

    command = MotionCommand(
        intent_id="single-1",
        idempotency_key="single-key",
        lease_id=lease.lease_id,
        source="test",
        kind="JOINT_STEP",
        profile="step",
        contract=stack.version,
        joint_names=["joint1"],
        target_positions=[0.01],
        duration_s=1.0,
        verify_bus=True,
        expected_profile_hash=stack.profile.profile_hash,
    )
    result = stack.arbiter.execute(command, 0.0)
    assert result.outcome == "COMPLETED"
    assert bridge.frames == []
