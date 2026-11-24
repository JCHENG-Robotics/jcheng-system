"""M5.2 vendor hardware runtime.

Only ``JOINT_STEP`` and ``GRIPPER`` are accepted. The bridge publishes one
target through ``/servo_controller``; completion is verified with
``GetBusServoState``. Stop latches safety and publishes a hold using the last
measured pulses.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading

from jcheng_interfaces.action import ExecuteMotion
from jcheng_interfaces.msg import MotionFeedback, SafetyState
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
from std_msgs.msg import Bool

from jcheng_hardware.readonly import ReadOnlyMapping
from jcheng_runtime.conversions import (
    capabilities,
    command_to_core,
    contract_to_core,
    contract_to_msg,
    lease_to_msg,
    report_to_msg,
    result_to_msg,
    ros_time,
    safety_to_msg,
)
from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import RobotProfile, find_repo_root, load_contract
from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.evidence import EvidenceStore
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock

from .bridge import VendorBridge
from .executor import HardwareExecutor


class VendorRuntimeNode(Node):
    def __init__(self) -> None:
        super().__init__("jcheng_vendor_runtime")
        self.declare_parameter("root", "")
        self.declare_parameter("evidence_path", "")
        self.declare_parameter("bus_service", "/ros_robot_controller/bus_servo/get_state")
        self.declare_parameter("command_topic", "/servo_controller")
        root_value = str(self.get_parameter("root").value or os.environ.get("JCHENG_ROOT", ""))
        self.root = Path(root_value).expanduser() if root_value else find_repo_root()
        self.profile = RobotProfile.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
        )
        self.contract = load_contract(self.root / "configs" / "contract.yaml")
        self.mapping = ReadOnlyMapping.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
        )
        self.clock = Clock()
        limits = SafetyLimits.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml"
        )
        self.safety = SafetySupervisor(self.profile, self.contract.version, self.clock, limits)
        self.authority = LeaseManager(self.contract.version, self.clock)
        evidence_path = str(self.get_parameter("evidence_path").value or "") or None
        self.evidence = EvidenceStore(self.clock, evidence_path)
        self.bridge = VendorBridge(
            self,
            self.mapping,
            self.profile.profile_hash,
            self.clock,
            bus_service=str(self.get_parameter("bus_service").value),
            command_topic=str(self.get_parameter("command_topic").value),
        )
        self.motion_executor = HardwareExecutor(
            profile=self.profile,
            contract=self.contract.version,
            clock=self.clock,
            safety=self.safety,
            authority=self.authority,
            guard=PublisherGuard(),
            evidence=self.evidence,
            bridge=self.bridge,
        )
        self._lock = threading.RLock()
        callback_group = ReentrantCallbackGroup()
        self._safety_pub = self.create_publisher(
            SafetyState, "/jcheng/telemetry/safety", 10
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
        self.create_timer(0.25, self._poll_state, callback_group=callback_group)
        self.create_timer(0.02, self._tick, callback_group=callback_group)
        self._initial_timer = self.create_timer(0.5, self._initial_poll_once, callback_group=callback_group)
        self.get_logger().info(
            "M5.2 vendor runtime started: only JOINT_STEP/GRIPPER; stop latches and holds"
        )

    def _initial_poll_once(self) -> None:
        if self._initial_timer is not None:
            self.destroy_timer(self._initial_timer)
            self._initial_timer = None
        self._initial_poll()

    def _initial_poll(self) -> None:
        try:
            sample = self.bridge.read_bus(timeout_s=2.0)
            self.safety.update_state(sample.state)
            self.get_logger().info(
                "initial bus read: " + ", ".join(
                    f"{name}={pulse}" for name, pulse in sorted(sample.pulses.items())
                )
            )
        except Exception as exc:  # noqa: BLE001 - startup boundary
            self.safety.latch(ErrorCode.BUS_READ_FAILED, f"initial bus read failed: {exc}")
            self.get_logger().error(f"initial bus read failed: {exc}")

    # ---- leases ----

    def _acquire(self, request: AcquireLease.Request, response: AcquireLease.Response):
        try:
            with self._lock:
                lease = self.authority.acquire(
                    holder=str(request.holder),
                    scope=str(request.scope),
                    mode=str(request.mode),
                    priority=int(request.priority),
                    ttl_ms=int(request.ttl_ms),
                    deadman_required=bool(request.deadman_required),
                    contract=contract_to_core(request.contract),
                )
            response.granted = True
            response.lease = lease_to_msg(lease, self.clock)
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
                lease = self.authority.renew(
                    str(request.lease_id),
                    int(request.ttl_ms),
                    contract_to_core(request.contract),
                )
            response.granted = True
            response.lease = lease_to_msg(lease, self.clock)
            response.reason_code = "OK"
            response.message = "lease renewed"
        except MotionRejected as exc:
            response.granted = False
            response.reason_code = exc.code
            response.message = exc.message
        return response

    def _release(self, request: ReleaseLease.Request, response: ReleaseLease.Response):
        with self._lock:
            released = self.authority.release(str(request.lease_id))
        response.released = bool(released)
        response.reason_code = "OK" if released else "NOT_FOUND"
        response.message = "lease released" if released else "lease not found"
        return response

    def _revoke(self, request: RevokeLease.Request, response: RevokeLease.Response):
        try:
            with self._lock:
                revoked = self.authority.revoke(
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

    # ---- safety ----

    def _validate(self, request: ValidateMotion.Request, response: ValidateMotion.Response):
        command = command_to_core(request.command)
        with self._lock:
            report = self.motion_executor.validate(command)
        response.approved = bool(report.approved)
        response.reason_code = str(report.reason_code)
        response.violations = list(report.violations)
        response.report = report_to_msg(report, self.clock)
        response.validation_id = "val-" + os.urandom(6).hex()
        response.expires_at = ros_time(self.clock, self.clock.now() + 30.0)
        return response

    def _get_safety(self, request: GetSafetyState.Request, response: GetSafetyState.Response):
        with self._lock:
            state = self.safety.current_state()
        response.state = safety_to_msg(state, self.clock)
        return response

    def _arm(self, request: ArmSystem.Request, response: ArmSystem.Response):
        try:
            with self._lock:
                state = self.safety.arm(
                    operator_id=str(request.operator_id),
                    checklist_hash=str(request.checklist_hash),
                    confirm_phrase=str(request.confirm_phrase),
                    contract=contract_to_core(request.contract),
                    profile_hash=str(request.profile_hash),
                )
            response.armed = True
            response.safety = safety_to_msg(state, self.clock)
            response.message = "armed"
        except MotionRejected as exc:
            response.armed = False
            response.safety = safety_to_msg(self.safety.current_state(), self.clock)
            response.message = f"{exc.code}: {exc.message}"
        return response

    def _disarm(self, request: DisarmSystem.Request, response: DisarmSystem.Response):
        if self.motion_executor.active:
            self.motion_executor.stop(str(request.operator_id), str(request.reason))
            response.disarmed = False
            response.safety = safety_to_msg(self.safety.current_state(), self.clock)
            response.message = "active motion stopped and latched; reset required"
            return response
        try:
            with self._lock:
                state = self.safety.disarm(str(request.operator_id), str(request.reason))
            response.disarmed = True
            response.safety = safety_to_msg(state, self.clock)
        except MotionRejected as exc:
            response.disarmed = False
            response.safety = safety_to_msg(self.safety.current_state(), self.clock)
            response.message = f"{exc.code}: {exc.message}"
        return response

    def _reset(self, request: ResetSafety.Request, response: ResetSafety.Response):
        if self.motion_executor.active:
            response.accepted = False
            response.safety = safety_to_msg(self.safety.current_state(), self.clock)
            response.message = "cannot reset while motion is active; stop first"
            return response
        bus_ok = False
        try:
            self.bridge.read_bus(timeout_s=1.0)
            bus_ok = True
        except Exception:  # noqa: BLE001 - reset must fail closed
            bus_ok = False
        try:
            with self._lock:
                state = self.safety.reset(
                    operator_id=str(request.operator_id),
                    reason=str(request.reason),
                    confirm_phrase=str(request.confirm_phrase),
                    contract=contract_to_core(request.contract),
                    bus_ok=bus_ok,
                )
            response.accepted = True
            response.safety = safety_to_msg(state, self.clock)
            response.message = "safety reset"
        except MotionRejected as exc:
            response.accepted = False
            response.safety = safety_to_msg(self.safety.current_state(), self.clock)
            response.message = f"{exc.code}: {exc.message}"
        return response

    def _capabilities(
        self, request: GetCapabilities.Request, response: GetCapabilities.Response
    ):
        manifests = capabilities(self.contract.version)
        for manifest in manifests:
            manifest.name = "jcheng_vendor_runtime"
            manifest.hardware_access = True
            manifest.publishes = ["/servo_controller"]
            manifest.subscribes = []
            manifest.services = []
        response.capabilities = manifests
        response.supported_contracts = [contract_to_msg(self.contract.version)]
        return response

    # ---- action ----

    def _goal(self, goal_request: ExecuteMotion.Goal) -> GoalResponse:
        try:
            command = command_to_core(goal_request.command)
        except (TypeError, ValueError):
            return GoalResponse.REJECT
        if not command.intent_id or not command.idempotency_key:
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _cancel(self, goal_handle) -> CancelResponse:
        self.motion_executor.stop("action_cancel", "action goal cancelled")
        return CancelResponse.ACCEPT

    def _feedback(self, goal_handle, phase: str, progress: float) -> None:
        feedback = MotionFeedback()
        feedback.intent_id = str(goal_handle.request.command.intent_id)
        feedback.phase = phase
        feedback.progress = float(progress)
        feedback.measured_positions = list(self.bridge.state().positions)
        feedback.bus_verified = False
        feedback.stamp = ros_time(self.clock, self.clock.now())
        wrapper = ExecuteMotion.Feedback()
        wrapper.feedback = feedback
        goal_handle.publish_feedback(wrapper)

    def _execute(self, goal_handle):
        command = command_to_core(goal_handle.request.command)
        self._feedback(goal_handle, "VALIDATING", 0.0)
        self._feedback(goal_handle, "EXECUTING", 0.1)
        result = self.motion_executor.execute(command)
        self._feedback(goal_handle, "VERIFYING", 0.9)
        if result.outcome == "COMPLETED":
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return ExecuteMotion.Result(result=result_to_msg(result, self.clock))

    # ---- runtime ----

    def _poll_state(self) -> None:
        try:
            sample = self.bridge.read_bus(timeout_s=0.5)
            with self._lock:
                self.safety.update_state(sample.state)
        except Exception as exc:  # noqa: BLE001 - fail closed
            with self._lock:
                self.safety.latch(ErrorCode.BUS_READ_FAILED, f"bus poll failed: {exc}")
            self.get_logger().error(f"bus poll failed: {exc}")

    def _publish_safety(self) -> None:
        with self._lock:
            state = self.safety.current_state()
        self._safety_pub.publish(safety_to_msg(state, self.clock))

    def _on_safety_stop(self, msg: Bool) -> None:
        if not msg.data:
            return
        self.motion_executor.stop("safety_stop", "external /safety_stop")
        self._publish_safety()
        self.get_logger().warning(
            f"STOP_LATCHED; hold latency {self.motion_executor.last_stop_latency_ms} ms"
        )

    def _tick(self) -> None:
        with self._lock:
            self.motion_executor.tick()
        self._publish_safety()


def main() -> int:
    import rclpy

    rclpy.init(args=None)
    node = None
    executor = None
    try:
        node = VendorRuntimeNode()
        executor = MultiThreadedExecutor(num_threads=6)
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
