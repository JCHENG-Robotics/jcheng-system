from __future__ import annotations

from pathlib import Path

import pytest
import rclpy
from jcheng_interfaces.msg import TeleopCommand
from jcheng_interfaces.srv import AcquireLease, ArmSystem
from jcheng_runtime.conversions import contract_to_msg, ros_time
from jcheng_runtime.node import RuntimeNode


@pytest.fixture()
def runtime(root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JCHENG_ROOT", str(root))
    if not rclpy.ok():
        rclpy.init()
    node = RuntimeNode()
    node._tick()
    try:
        yield node
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def _arm(node: RuntimeNode) -> None:
    request = ArmSystem.Request()
    request.operator_id = "pytest"
    request.checklist_hash = "checklist"
    request.confirm_phrase = node.stack.safety.limits.arm_confirm_phrase
    request.contract = contract_to_msg(node.stack.version)
    request.profile_hash = node.stack.profile.profile_hash
    response = node._arm(request, ArmSystem.Response())
    assert response.armed, response.message


def _lease(node: RuntimeNode):
    request = AcquireLease.Request()
    request.holder = "teleop-pytest"
    request.scope = "arm"
    request.mode = "exclusive"
    request.priority = 40
    request.ttl_ms = 30_000
    request.deadman_required = True
    request.contract = contract_to_msg(node.stack.version)
    response = node._acquire(request, AcquireLease.Response())
    assert response.granted
    return response.lease


def _teleop(node: RuntimeNode, lease_id: str, *, seq: int = 1, deadman: bool = True) -> TeleopCommand:
    message = TeleopCommand()
    message.header.stamp = ros_time(node.stack.clock, node.stack.clock.now())
    message.seq = seq
    message.lease_id = lease_id
    message.source = "pytest"
    message.contract = contract_to_msg(node.stack.version)
    message.mode = "JOINT_VELOCITY"
    message.joint_velocity_rad_s = [0.5, 0.0, 0.0, 0.0, 0.0]
    message.deadman = deadman
    return message


def test_teleop_subscriber_steps_through_arbiter(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    runtime._on_teleop(_teleop(runtime, lease.lease_id))
    runtime._tick()
    assert runtime.teleop.active is True
    assert runtime.stack.hardware.positions[0] == pytest.approx(0.05, abs=1e-6)
    assert runtime.stack.safety.stop_latched is False


def test_state_telemetry_is_read_only_and_honest(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    runtime._on_teleop(_teleop(runtime, lease.lease_id))
    runtime._tick()
    message = runtime._state_message()
    assert message.truth == "commanded"
    assert message.verification == "timed"
    assert message.source == "jcheng_runtime/mock"
    assert list(message.joint_names) == runtime.stack.profile.joint_names
    assert list(message.positions) == runtime.stack.hardware.positions
    assert message.profile_hash == runtime.stack.profile.profile_hash
    assert message.fresh is True
    assert message.seq >= 1


def test_state_to_msg_preserves_a_simulation_label(runtime: RuntimeNode) -> None:
    from jcheng_runtime.conversions import state_to_msg

    state = runtime.stack.hardware.state()
    message = state_to_msg(
        state, runtime.stack.clock, seq=1, source="jcheng_runtime/sim_bridge"
    )
    assert message.truth == state.truth
    assert message.verification == state.verification


def test_teleop_deadman_release_aborts_without_latch(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    runtime._on_teleop(_teleop(runtime, lease.lease_id, seq=1))
    runtime._tick()
    runtime._on_teleop(_teleop(runtime, lease.lease_id, seq=2, deadman=False))
    assert runtime.teleop.active is False
    assert runtime.stack.safety.stop_latched is False


def test_safety_stop_aborts_teleop_and_latches(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    runtime._on_teleop(_teleop(runtime, lease.lease_id))
    runtime._tick()
    from std_msgs.msg import Bool

    runtime._on_safety_stop(Bool(data=True))
    assert runtime.teleop.active is False
    assert runtime.stack.safety.stop_latched is True
