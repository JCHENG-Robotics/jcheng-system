"""M5.1 ROS transport runtime (Mock hardware only).

This node exposes the public services/action over the existing pure-Python
core. It deliberately uses ``MockStack``: it proves the ROS contract, lease,
safety, stop-latch, and action paths without publishing any hardware command.

The real hardware bridge and supervised single-joint/gripper run are M5.2 and
require separate authorization.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any
import uuid

from jcheng_interfaces.action import ExecuteMotion
from jcheng_interfaces.msg import MotionFeedback, RobotState, SafetyState, TeleopCommand
from jcheng_interfaces.srv import (
    AcquireLease,
    ArmSystem,
    DisarmSystem,
    GetCapabilities,
    GetSafetyState,
    ReleaseLease,
    RenewLease,
    ResetSafety,
    RevokeLease,
    ValidateMotion,
)
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import Bool

from jcheng_system_core.config import find_repo_root
from jcheng_system_core.errors import MotionRejected
from jcheng_system_core.stack import MockStack
from jcheng_system_core.teleop import TeleopIntake, TeleopSample

from .conversions import (
    capabilities,
    command_to_core,
    contract_to_core,
    contract_to_msg,
    lease_to_msg,
    report_to_msg,
    result_to_msg,
    ros_time,
    safety_to_msg,
    state_to_msg,
)


class RuntimeNode(Node):
    def __init__(self) -> None:
        super().__init__("jcheng_runtime")
        self.declare_parameter("root", "")
        self.declare_parameter("evidence_path", "")
        self.declare_parameter("sim_bridge", False)
        self.declare_parameter("robot", "armpi_ultra")
        root_value = str(self.get_parameter("root").value or os.environ.get("JCHENG_ROOT", ""))
        root = Path(root_value).expanduser() if root_value else find_repo_root()
        evidence = str(self.get_parameter("evidence_path").value or "") or None
        self.stack = MockStack.build(
            root,
            evidence_path=evidence,
            robot=str(self.get_parameter("robot").value or "armpi_ultra"),
        )
        self._sim_context = None
        self._sim_node = None
        self._sim_executor = None
        self._sim_thread = None
        if bool(self.get_parameter("sim_bridge").value):
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node as RclpyNode

            from .sim_bridge import RosSimHardwareBridge

            self._sim_context = rclpy.Context()
            rclpy.init(context=self._sim_context, domain_id=43)
            self._sim_node = RclpyNode("jcheng_sim_bridge", context=self._sim_context)
            self._sim_executor = SingleThreadedExecutor(context=self._sim_context)
            self._sim_executor.add_node(self._sim_node)
            self._sim_thread = threading.Thread(target=self._sim_executor.spin, daemon=True)
            self._sim_thread.start()
            bridge = RosSimHardwareBridge(self._sim_node, self.stack.profile, self.stack.clock)
            self.stack.hardware = bridge
            self.stack.arbiter.hardware = bridge
            self.get_logger().info(
                "teleop simulation bridge enabled: runtime on its domain, bridge on domain 43 (test-only)"
            )
        self.teleop = TeleopIntake(self.stack)
        self._lock = threading.RLock()
        self._feedback_seq = 0
        callback_group = ReentrantCallbackGroup()

        # Reuse the M4 namespace so a UI has one safety topic in Mock mode.
        self._safety_pub = self.create_publisher(
            SafetyState,
            "/jcheng/telemetry/safety",
            10,
        )
        # Read-only state tap for capability consumers (teleop capture). It is
        # not the production telemetry node: the mock backend is relabelled
        # `commanded`/`timed` because its fixture labels itself measured/bus to
        # exercise the safety path, which is not a measurement claim.
        self._state_pub = self.create_publisher(
            RobotState,
            "/jcheng/telemetry/state",
            10,
        )
        self._state_seq = 0
        self._state_source = (
            "jcheng_runtime/sim_bridge"
            if self._sim_node is not None
            else "jcheng_runtime/mock"
        )
        self._state_labels = (
            ("measured", "simulated")
            if self._sim_node is not None
            else ("commanded", "timed")
        )

        self.create_service(
            AcquireLease,
            "/jcheng/authority/acquire_lease",
            self._acquire,
            callback_group=callback_group,
        )
        self.create_service(
            RenewLease,
            "/jcheng/authority/renew_lease",
            self._renew,
            callback_group=callback_group,
        )
        self.create_service(
            ReleaseLease,
            "/jcheng/authority/release_lease",
            self._release,
            callback_group=callback_group,
        )
        self.create_service(
            RevokeLease,
            "/jcheng/authority/revoke_lease",
            self._revoke,
            callback_group=callback_group,
        )
        self.create_service(
            ValidateMotion,
            "/jcheng/safety/validate_motion",
            self._validate,
            callback_group=callback_group,
        )
        self.create_service(
            GetSafetyState,
            "/jcheng/safety/get_state",
            self._get_safety,
            callback_group=callback_group,
        )
        self.create_service(
            ArmSystem,
            "/jcheng/safety/arm",
            self._arm,
            callback_group=callback_group,
        )
        self.create_service(
            DisarmSystem,
            "/jcheng/safety/disarm",
            self._disarm,
            callback_group=callback_group,
        )
        self.create_service(
            ResetSafety,
            "/jcheng/safety/reset",
            self._reset,
            callback_group=callback_group,
        )
        self.create_service(
            GetCapabilities,
            "/jcheng/capabilities",
            self._capabilities,
            callback_group=callback_group,
        )

        self._action_server = ActionServer(
            self,
            ExecuteMotion,
            "/jcheng/motion/execute",
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=self._cancel,
            callback_group=callback_group,
        )
        self.create_subscription(
            Bool,
            "/safety_stop",
            self._on_safety_stop,
            10,
            callback_group=callback_group,
        )
        teleop_qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.create_subscription(
            TeleopCommand,
            "/jcheng/intent/teleop",
            self._on_teleop,
            teleop_qos,
            callback_group=callback_group,
        )
        self.create_timer(0.02, self._tick, callback_group=callback_group)
        self.get_logger().info(
            "M5.1 runtime started: Mock hardware only, no vendor command publisher"
        )

    # ---- helpers ----

    def _reject(self, code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "code": code, "message": message}

    def _acquire(self, request: AcquireLease.Request, response: AcquireLease.Response):
        try:
            with self._lock:
                lease = self.stack.authority.acquire(
                    holder=str(request.holder),
                    scope=str(request.scope),
                    mode=str(request.mode),
                    priority=int(request.priority),
                    ttl_ms=int(request.ttl_ms),
                    deadman_required=bool(request.deadman_required),
                    contract=contract_to_core(request.contract),
                )
            response.granted = True
            response.lease = lease_to_msg(lease, self.stack.clock)
            response.reason_code = "OK"
            response.message = "lease granted"
        except MotionRejected as exc:
            response.granted = False
            response.reason_code = exc.code
            response.message = exc.message
        return response

    def _renew(self, request: RenewLease.Request, response: RenewLease.Response):
        try:
            with self._lock:
                lease = self.stack.authority.renew(
                    str(request.lease_id),
                    int(request.ttl_ms),
                    contract_to_core(request.contract),
                )
            response.granted = True
            response.lease = lease_to_msg(lease, self.stack.clock)
            response.reason_code = "OK"
            response.message = "lease renewed"
        except MotionRejected as exc:
            response.granted = False
            response.reason_code = exc.code
            response.message = exc.message
        return response

    def _release(self, request: ReleaseLease.Request, response: ReleaseLease.Response):
        with self._lock:
            released = self.stack.authority.release(str(request.lease_id))
        response.released = bool(released)
        response.reason_code = "OK" if released else "NOT_FOUND"
        response.message = "lease released" if released else "lease not found"
        return response

    def _revoke(self, request: RevokeLease.Request, response: RevokeLease.Response):
        try:
            with self._lock:
                revoked = self.stack.authority.revoke(
                    str(request.lease_id),
                    str(request.operator_id),
                    str(request.reason),
                    contract_to_core(request.contract),
                )
            response.revoked = bool(revoked)
            response.reason_code = "OK" if revoked else "NOT_FOUND"
            response.message = "lease revoked" if revoked else "lease not found"
        except MotionRejected as exc:
            response.revoked = False
            response.reason_code = exc.code
            response.message = exc.message
        return response

    def _validate(self, request: ValidateMotion.Request, response: ValidateMotion.Response):
        command = command_to_core(request.command)
        with self._lock:
            report = self.stack.arbiter.validate(command)
        response.approved = bool(report.approved)
        response.reason_code = str(report.reason_code)
        response.violations = list(report.violations)
        response.report = report_to_msg(report, self.stack.clock)
        response.validation_id = f"val-{uuid.uuid4().hex[:12]}"
        response.expires_at = ros_time(self.stack.clock, self.stack.clock.now() + 30.0)
        return response

    def _get_safety(self, request: GetSafetyState.Request, response: GetSafetyState.Response):
        with self._lock:
            state = self.stack.safety.current_state()
        response.state = safety_to_msg(state, self.stack.clock)
        return response

    def _arm(self, request: ArmSystem.Request, response: ArmSystem.Response):
        try:
            with self._lock:
                state = self.stack.safety.arm(
                    operator_id=str(request.operator_id),
                    checklist_hash=str(request.checklist_hash),
                    confirm_phrase=str(request.confirm_phrase),
                    contract=contract_to_core(request.contract),
                    profile_hash=str(request.profile_hash),
                )
            response.armed = True
            response.safety = safety_to_msg(state, self.stack.clock)
            response.message = "armed"
        except MotionRejected as exc:
            response.armed = False
            response.safety = safety_to_msg(
                self.stack.safety.current_state(), self.stack.clock
            )
            response.message = f"{exc.code}: {exc.message}"
        return response

    def _disarm(self, request: DisarmSystem.Request, response: DisarmSystem.Response):
        try:
            with self._lock:
                state = self.stack.safety.disarm(str(request.operator_id), str(request.reason))
            response.disarmed = True
            response.safety = safety_to_msg(state, self.stack.clock)
        except MotionRejected as exc:
            response.disarmed = False
            response.safety = safety_to_msg(
                self.stack.safety.current_state(), self.stack.clock
            )
            response.safety.violations.append(f"{exc.code}: {exc.message}")
        return response

    def _reset(self, request: ResetSafety.Request, response: ResetSafety.Response):
        try:
            with self._lock:
                state = self.stack.safety.reset(
                    operator_id=str(request.operator_id),
                    reason=str(request.reason),
                    confirm_phrase=str(request.confirm_phrase),
                    contract=contract_to_core(request.contract),
                    bus_ok=True,
                )
            response.accepted = True
            response.safety = safety_to_msg(state, self.stack.clock)
            response.message = "safety reset"
        except MotionRejected as exc:
            response.accepted = False
            response.safety = safety_to_msg(
                self.stack.safety.current_state(), self.stack.clock
            )
            response.message = f"{exc.code}: {exc.message}"
        return response

    def _capabilities(
        self, request: GetCapabilities.Request, response: GetCapabilities.Response
    ):
        response.capabilities = capabilities(self.stack.version)
        response.supported_contracts = [contract_to_msg(self.stack.version)]
        return response

    # ---- action ----

    def _goal(self, goal_request: ExecuteMotion.Goal) -> GoalResponse:
        try:
            command = command_to_core(goal_request.command)
        except (TypeError, ValueError) as exc:
            self.get_logger().warning(f"rejecting malformed goal: {exc}")
            return GoalResponse.REJECT
        if not command.intent_id or not command.idempotency_key:
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _cancel(self, goal_handle: Any) -> CancelResponse:
        # The arbiter serializes its own transitions; cancel must not wait on
        # the lock held by an executing goal.
        self.teleop.abort("action_cancel")
        self.stack.arbiter.stop("action_cancel", "action goal cancelled")
        return CancelResponse.ACCEPT

    def _feedback(self, goal_handle: Any, phase: str, progress: float) -> None:
        feedback = MotionFeedback()
        feedback.intent_id = str(goal_handle.request.command.intent_id)
        feedback.phase = phase
        feedback.progress = float(progress)
        feedback.measured_positions = list(self.stack.hardware.state().positions)
        feedback.bus_verified = False
        feedback.stamp = ros_time(self.stack.clock, self.stack.clock.now())
        wrapper = ExecuteMotion.Feedback()
        wrapper.feedback = feedback
        goal_handle.publish_feedback(wrapper)

    def _execute(self, goal_handle: Any):
        command = command_to_core(goal_handle.request.command)
        self._feedback(goal_handle, "VALIDATING", 0.0)
        # No outer lock: the arbiter is the single writer for the session and
        # a concurrent cancel must be able to reach stop().
        self._feedback(goal_handle, "EXECUTING", 0.1)
        result = self.stack.arbiter.execute(command)
        self._feedback(goal_handle, "VERIFYING", 0.8)
        if result.outcome == "COMPLETED":
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return ExecuteMotion.Result(result=result_to_msg(result, self.stack.clock))

    # ---- runtime ----

    def _on_teleop(self, msg: TeleopCommand) -> None:
        stamp_s = float(msg.header.stamp.sec) + float(msg.header.stamp.nanosec) / 1e9
        sample = TeleopSample(
            seq=int(msg.seq),
            lease_id=str(msg.lease_id),
            source=str(msg.source),
            contract=contract_to_core(msg.contract),
            mode=str(msg.mode),
            joint_velocities_rad_s=tuple(float(value) for value in msg.joint_velocity_rad_s),
            deadman=bool(msg.deadman),
            stamp_s=stamp_s,
            received_s=self.stack.clock.now(),
        )
        try:
            self.teleop.on_sample(sample)
        except MotionRejected as exc:
            self.get_logger().warning(f"teleop rejected: {exc.code}: {exc.message}")

    def _on_safety_stop(self, msg: Bool) -> None:
        if not msg.data:
            return
        self.teleop.abort("safety_stop")
        self.stack.arbiter.stop("safety_stop", "external /safety_stop")
        state = self.stack.safety.current_state()
        self._safety_pub.publish(safety_to_msg(state, self.stack.clock))

    def destroy_node(self) -> bool:
        if self._sim_executor is not None:
            self._sim_executor.shutdown()
        if self._sim_node is not None:
            self._sim_node.destroy_node()
        if self._sim_context is not None:
            import rclpy

            if rclpy.ok(context=self._sim_context):
                rclpy.shutdown(context=self._sim_context)
        return super().destroy_node()

    def _state_message(self) -> RobotState:
        state = self.stack.hardware.state()
        self._state_seq += 1
        truth, verification = self._state_labels
        return state_to_msg(
            state,
            self.stack.clock,
            seq=self._state_seq,
            source=self._state_source,
            truth=truth,
            verification=verification,
        )

    def _tick(self) -> None:
        # No outer lock: this timer must be able to observe lease loss while
        # an execute is in flight.
        self.stack.sync_state()
        self.stack.arbiter.tick()
        result = self.teleop.tick()
        if result is not None and result.outcome != "COMPLETED":
            self.get_logger().warning(
                f"teleop step did not complete: {result.outcome}/{result.reason_code}"
            )
        state = self.stack.safety.current_state()
        self._safety_pub.publish(safety_to_msg(state, self.stack.clock))
        try:
            self._state_pub.publish(self._state_message())
        except Exception as exc:  # a telemetry tap must never stop the runtime
            self.get_logger().error(f"state telemetry publish failed: {exc}")


def main() -> int:
    import rclpy

    rclpy.init(args=None)
    node = None
    executor = None
    try:
        node = RuntimeNode()
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
