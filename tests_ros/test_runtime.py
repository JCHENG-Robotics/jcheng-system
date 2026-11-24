from __future__ import annotations

from pathlib import Path

import pytest
import rclpy
from jcheng_interfaces.msg import MotionCommand as MotionCommandMsg
from jcheng_interfaces.srv import (
    AcquireLease,
    ArmSystem,
    DisarmSystem,
    GetCapabilities,
    GetSafetyState,
    ResetSafety,
    ValidateMotion,
)
from jcheng_runtime.conversions import contract_to_msg
from jcheng_runtime.node import RuntimeNode
from std_msgs.msg import Bool


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


def _lease(node: RuntimeNode, scope: str = "arm"):
    request = AcquireLease.Request()
    request.holder = "pytest"
    request.scope = scope
    request.mode = "exclusive"
    request.priority = 100
    request.ttl_ms = 30_000
    request.deadman_required = False
    request.contract = contract_to_msg(node.stack.version)
    response = node._acquire(request, AcquireLease.Response())
    assert response.granted
    return response.lease


def _command(node: RuntimeNode, lease_id: str) -> MotionCommandMsg:
    command = MotionCommandMsg()
    command.intent_id = "intent-pytest"
    command.idempotency_key = "idem-pytest"
    command.lease_id = lease_id
    command.source = "pytest"
    command.kind = "JOINT_STEP"
    command.profile = "step"
    command.contract = contract_to_msg(node.stack.version)
    command.joint_names = ["joint1"]
    command.target_positions = [0.05]
    command.max_velocity_rad_s = 0.5
    command.max_acceleration_rad_s2 = 0.5
    command.duration_s = 1.0
    command.verify_bus = True
    command.require_measured_state = True
    return command


def _arm(node: RuntimeNode) -> None:
    request = ArmSystem.Request()
    request.operator_id = "pytest"
    request.checklist_hash = "checklist"
    request.confirm_phrase = node.stack.safety.limits.arm_confirm_phrase
    request.contract = contract_to_msg(node.stack.version)
    request.profile_hash = node.stack.profile.profile_hash
    response = node._arm(request, ArmSystem.Response())
    assert response.armed, response.message


def test_safety_arm_lease_and_validation(runtime: RuntimeNode) -> None:
    state = runtime._get_safety(GetSafetyState.Request(), GetSafetyState.Response())
    assert state.state.state == "DISARMED"
    _arm(runtime)
    lease = _lease(runtime)
    request = ValidateMotion.Request(command=_command(runtime, lease.lease_id))
    response = runtime._validate(request, ValidateMotion.Response())
    assert response.approved, response.violations
    assert response.report.approved
    assert response.validation_id.startswith("val-")


def test_safety_stop_latches_and_rejects(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    runtime._on_safety_stop(Bool(data=True))
    state = runtime._get_safety(GetSafetyState.Request(), GetSafetyState.Response())
    assert state.state.stop_latched is True
    assert state.state.state == "STOP_LATCHED"
    request = ValidateMotion.Request(command=_command(runtime, lease.lease_id))
    response = runtime._validate(request, ValidateMotion.Response())
    assert response.approved is False
    assert response.reason_code == "SAFETY_STOP_LATCHED"

    reset = ResetSafety.Request()
    reset.operator_id = "pytest"
    reset.reason = "test reset"
    reset.confirm_phrase = runtime.stack.safety.limits.reset_confirm_phrase
    reset.contract = contract_to_msg(runtime.stack.version)
    reset_response = runtime._reset(reset, ResetSafety.Response())
    assert reset_response.accepted
    assert reset_response.safety.stop_latched is False


def test_disarm_and_capabilities(runtime: RuntimeNode) -> None:
    _arm(runtime)
    request = DisarmSystem.Request()
    request.operator_id = "pytest"
    request.reason = "test"
    response = runtime._disarm(request, DisarmSystem.Response())
    assert response.disarmed
    assert response.safety.state == "DISARMED"

    caps = runtime._capabilities(GetCapabilities.Request(), GetCapabilities.Response())
    assert caps.capabilities
    assert caps.capabilities[0].name == "jcheng_runtime"
    assert caps.supported_contracts[0].contract_hash == runtime.stack.version.contract_hash


def test_goal_accepts_valid_and_rejects_malformed(runtime: RuntimeNode) -> None:
    lease = _lease(runtime)
    good = MotionCommandMsg()
    good.intent_id = "intent-goal"
    good.idempotency_key = "idem-goal"
    good.lease_id = lease.lease_id
    good.kind = "JOINT_STEP"
    good.profile = "step"
    good.contract = contract_to_msg(runtime.stack.version)
    from rclpy.action import GoalResponse

    assert runtime._goal(type("Goal", (), {"command": good})()) == GoalResponse.ACCEPT
    bad = MotionCommandMsg()
    assert runtime._goal(type("Goal", (), {"command": bad})()) == GoalResponse.REJECT


def test_execute_action_completes_with_bus_verification(runtime: RuntimeNode) -> None:
    _arm(runtime)
    lease = _lease(runtime)
    command = _command(runtime, lease.lease_id)

    class GoalHandle:
        def __init__(self) -> None:
            self.request = type("Request", (), {"command": command})()
            self.feedback = []
            self.succeeded = False

        def publish_feedback(self, feedback) -> None:
            self.feedback.append(feedback.feedback.phase)

        def succeed(self) -> None:
            self.succeeded = True

        def abort(self) -> None:
            raise AssertionError("action must not abort")

    handle = GoalHandle()
    result = runtime._execute(handle)
    assert handle.succeeded
    assert result.result.outcome == "COMPLETED"
    assert result.result.bus_verified is True
    assert handle.feedback == ["VALIDATING", "EXECUTING", "VERIFYING"]
    assert result.result.measured_positions[0] == pytest.approx(0.05)
