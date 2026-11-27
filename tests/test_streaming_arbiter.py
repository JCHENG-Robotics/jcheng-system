"""M6 arbiter streaming-session tests (mock bridge, no hardware)."""

from __future__ import annotations

import uuid

from jcheng_system_core.config import RobotProfile
from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.models import MotionCommand
from jcheng_system_core.stack import MockStack
from jcheng_system_core.streaming import MockStreamingBridge
from jcheng_system_core.timebase import ManualClock

OFFSETS = (0.02, 0.02, -0.02, 0.02, -0.02)


def _stack(root):
    clock = ManualClock()
    profile = RobotProfile.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    )
    bridge = MockStreamingBridge(profile, clock)
    stack = MockStack.build(root, clock=clock, hardware=bridge)
    stack.arm()
    return stack, bridge, clock


def _lease(stack):
    return stack.lease(
        holder="teleop", scope="arm", priority=40, ttl_ms=5000, deadman_required=True
    )


def _command(stack, lease_id: str, *, duration_s: float = 0.05) -> MotionCommand:
    targets = [
        position + offset
        for position, offset in zip(stack.hardware.positions, OFFSETS)
    ]
    return MotionCommand(
        intent_id=f"stream-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"stream-{uuid.uuid4().hex}",
        lease_id=lease_id,
        holder="teleop",
        source="test-stream",
        kind="JOINT_STEP",
        profile="pose",
        contract=stack.version,
        joint_names=list(stack.profile.joint_names),
        target_positions=targets,
        max_velocity_rad_s=0.5,
        duration_s=duration_s,
        verify_bus=True,
        expected_profile_hash=stack.profile.profile_hash,
    )


def _advance(clock, stack, delta: float) -> float:
    clock.advance(delta)
    stack.sync_state()
    return clock.now()


def test_stream_session_publishes_frames_until_finish(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    stack.sync_state()
    assert stack.arbiter.start(_command(stack, lease.lease_id), 0.0) is None
    assert len(bridge.frames) == 1
    for index in range(1, 6):
        now = _advance(clock, stack, 0.051)
        result = stack.arbiter.stream(_command(stack, lease.lease_id), now)
        assert result is None, (index, result)
    assert len(bridge.frames) == 6
    result = stack.arbiter.finish(clock.now())
    assert result is not None and result.outcome == "COMPLETED"
    assert result.bus_verified is True
    assert len(bridge.frames) == 6


def test_lease_revoke_aborts_and_latches_the_stream(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    stack.sync_state()
    assert stack.arbiter.start(_command(stack, lease.lease_id), 0.0) is None
    frames = len(bridge.frames)
    stack.authority.revoke(lease.lease_id, "test", "revoked", stack.version)
    now = _advance(clock, stack, 0.051)
    result = stack.arbiter.stream(_command(stack, lease.lease_id), now)
    assert result is not None
    assert result.outcome == "ABORTED"
    assert result.reason_code == ErrorCode.LEASE_REVOKED.value
    assert stack.safety.stop_latched is True
    assert len(bridge.frames) == frames


def test_stop_latch_prevents_further_frames(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    stack.sync_state()
    assert stack.arbiter.start(_command(stack, lease.lease_id), 0.0) is None
    frames = len(bridge.frames)
    stack.arbiter.stop("test", "operator stop", 0.01)
    now = _advance(clock, stack, 0.051)
    result = stack.arbiter.stream(_command(stack, lease.lease_id), now)
    assert result is not None and result.outcome != "COMPLETED"
    assert len(bridge.frames) == frames


def test_invalid_frame_aborts_the_stream(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    stack.sync_state()
    assert stack.arbiter.start(_command(stack, lease.lease_id), 0.0) is None
    frames = len(bridge.frames)
    now = _advance(clock, stack, 0.051)
    command = _command(stack, lease.lease_id)
    command.target_positions[2] = 0.5  # joint3 upper limit is 0.0
    result = stack.arbiter.stream(command, now)
    assert result is not None
    assert result.outcome == "ABORTED"
    assert result.reason_code == ErrorCode.JOINT_LIMIT.value
    assert len(bridge.frames) == frames


def test_stream_without_an_active_session_is_rejected(root) -> None:
    stack, bridge, clock = _stack(root)
    lease = _lease(stack)
    stack.sync_state()
    result = stack.arbiter.stream(_command(stack, lease.lease_id), 0.0)
    assert result is not None
    assert result.outcome == "REJECTED"
    assert result.reason_code == ErrorCode.MOTION_BUSY.value
    assert bridge.frames == []
