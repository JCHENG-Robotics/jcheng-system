"""Conversions between jcheng_interfaces messages and the pure-Python core."""

from __future__ import annotations

from typing import Any

from builtin_interfaces.msg import Time
from jcheng_interfaces.msg import (
    CapabilityManifest,
    ContractVersion as ContractVersionMsg,
    ControlLease,
    MotionCommand as MotionCommandMsg,
    MotionResult as MotionResultMsg,
    RobotState as RobotStateMsg,
    SafetyState as SafetyStateMsg,
    ValidationReport as ValidationReportMsg,
)
from jcheng_system_core.models import (
    BasePulse,
    ContractVersion,
    GripperCommand,
    JointTrajectory,
    JointTrajectoryPoint,
    Lease,
    MotionCommand,
    MotionResult,
    RobotState,
    SafetyState,
    ValidationReport,
)


def ros_time(clock: Any, seconds: float) -> Time:
    wall = clock.t0_wall.timestamp() + float(seconds)
    sec = int(wall)
    nanosec = int((wall - sec) * 1e9)
    if nanosec >= 1_000_000_000:
        sec += 1
        nanosec -= 1_000_000_000
    return Time(sec=sec, nanosec=nanosec)


def core_time(clock: Any, msg: Time) -> float:
    return (float(msg.sec) + float(msg.nanosec) * 1e-9) - clock.t0_wall.timestamp()


def contract_to_core(msg: ContractVersionMsg) -> ContractVersion:
    return ContractVersion(
        major=int(msg.major),
        minor=int(msg.minor),
        patch=int(msg.patch),
        contract_hash=str(msg.contract_hash),
    )


def contract_to_msg(value: ContractVersion) -> ContractVersionMsg:
    return ContractVersionMsg(
        major=int(value.major),
        minor=int(value.minor),
        patch=int(value.patch),
        contract_hash=str(value.contract_hash),
    )


def safety_to_msg(value: SafetyState, clock: Any) -> SafetyStateMsg:
    msg = SafetyStateMsg()
    msg.header.stamp = ros_time(clock, value.last_valid_state_at)
    msg.header.frame_id = "base_link"
    msg.seq = int(value.seq)
    msg.state = str(value.state)
    msg.stop_latched = bool(value.stop_latched)
    msg.violation_codes = list(value.violation_codes)
    msg.violations = list(value.violations)
    msg.config_hash = str(value.config_hash)
    msg.profile_hash = str(value.profile_hash)
    msg.last_valid_state_at = ros_time(clock, value.last_valid_state_at)
    return msg


def state_to_msg(
    value: RobotState,
    clock: Any,
    *,
    seq: int,
    source: str,
    truth: str | None = None,
    verification: str | None = None,
) -> RobotStateMsg:
    """Read-only state message.

    `truth`/`verification` default to the hardware port's labels. A caller may
    override them only to stop a fixture from claiming a measurement: the mock
    bridge labels itself `measured`/`bus` to exercise the safety path, which is
    not a provenance claim for a published topic.
    """
    msg = RobotStateMsg()
    msg.header.stamp = ros_time(clock, value.stamp)
    msg.header.frame_id = "base_link"
    msg.seq = int(seq)
    msg.source = str(source)
    msg.fresh = bool(value.fresh)
    msg.joint_names = list(value.joint_names)
    msg.positions = [float(item) for item in value.positions]
    msg.velocities = [float(item) for item in value.velocities]
    msg.gripper_position = float(value.gripper_position)
    msg.base_duty = [float(item) for item in value.base_duty]
    msg.battery_voltage = float(value.battery_voltage)
    msg.profile_hash = str(value.profile_hash)
    msg.truth = str(truth if truth is not None else value.truth)
    msg.verification = str(verification if verification is not None else value.verification)
    return msg


def lease_to_msg(value: Lease, clock: Any) -> ControlLease:
    msg = ControlLease()
    msg.lease_id = str(value.lease_id)
    msg.holder = str(value.holder)
    msg.scope = str(value.scope)
    msg.mode = str(value.mode)
    msg.priority = int(value.priority)
    msg.deadman_required = bool(value.deadman_required)
    msg.issued_at = ros_time(clock, value.issued_at)
    msg.expires_at = ros_time(clock, value.expires_at)
    msg.contract = contract_to_msg(value.contract)
    return msg


def command_to_core(msg: MotionCommandMsg) -> MotionCommand:
    trajectory = None
    if msg.trajectory.joint_names or msg.trajectory.points:
        trajectory = JointTrajectory(
            joint_names=[str(name) for name in msg.trajectory.joint_names],
            points=[
                JointTrajectoryPoint(
                    positions=[float(value) for value in point.positions],
                    velocities=[float(value) for value in point.velocities],
                    accelerations=[float(value) for value in point.accelerations],
                    time_from_start=float(point.time_from_start),
                )
                for point in msg.trajectory.points
            ],
        )
    base_pulse = None
    if msg.kind == "BASE_PULSE":
        base_pulse = BasePulse(
            duty=[float(value) for value in msg.base_pulse.duty],
            duration_s=float(msg.base_pulse.duration_s),
            require_stopped_after=bool(msg.base_pulse.require_stopped_after),
        )
    gripper = None
    if msg.kind == "GRIPPER":
        gripper = GripperCommand(
            position_rad=float(msg.gripper.position_rad),
            max_velocity_rad_s=float(msg.gripper.max_velocity_rad_s),
            duration_s=float(msg.gripper.duration_s),
            verify_bus=bool(msg.gripper.verify_bus),
        )
    target_pose = None
    if msg.kind == "POSE":
        target_pose = {
            "position": {
                "x": float(msg.target_pose.position.x),
                "y": float(msg.target_pose.position.y),
                "z": float(msg.target_pose.position.z),
            },
            "orientation": {
                "x": float(msg.target_pose.orientation.x),
                "y": float(msg.target_pose.orientation.y),
                "z": float(msg.target_pose.orientation.z),
                "w": float(msg.target_pose.orientation.w),
            },
        }
    return MotionCommand(
        intent_id=str(msg.intent_id),
        idempotency_key=str(msg.idempotency_key),
        lease_id=str(msg.lease_id),
        source=str(msg.source),
        kind=str(msg.kind),
        profile=str(msg.profile),
        contract=contract_to_core(msg.contract),
        trajectory=trajectory,
        joint_names=[str(name) for name in msg.joint_names],
        target_positions=[float(value) for value in msg.target_positions],
        max_velocity_rad_s=float(msg.max_velocity_rad_s) if msg.max_velocity_rad_s else None,
        max_acceleration_rad_s2=(
            float(msg.max_acceleration_rad_s2) if msg.max_acceleration_rad_s2 else None
        ),
        duration_s=float(msg.duration_s) if msg.duration_s else None,
        target_pose=target_pose,
        tip_link=str(msg.tip_link),
        frame_id=str(msg.frame_id),
        base_pulse=base_pulse,
        gripper=gripper,
        verify_bus=bool(msg.verify_bus),
        require_measured_state=bool(msg.require_measured_state),
        expected_profile_hash=str(msg.expected_profile_hash),
    )


def result_to_msg(value: MotionResult, clock: Any) -> MotionResultMsg:
    msg = MotionResultMsg()
    msg.intent_id = str(value.intent_id)
    msg.outcome = str(value.outcome)
    msg.reason_code = str(value.reason_code)
    msg.message = str(value.message)
    msg.safety = safety_to_msg(value.safety, clock)
    msg.measured_positions = [float(item) for item in value.measured_positions]
    msg.bus_verified = bool(value.bus_verified)
    msg.calibration_invalidated = bool(value.calibration_invalidated)
    msg.completed_at = ros_time(clock, value.completed_at)
    msg.evidence_id = str(value.evidence_id)
    return msg


def report_to_msg(value: ValidationReport, clock: Any) -> ValidationReportMsg:
    msg = ValidationReportMsg()
    msg.schema_version = 1
    msg.approved = bool(value.approved)
    msg.reason_code = str(value.reason_code)
    msg.check_ids = list(value.check_ids)
    msg.violations = list(value.violations)
    msg.duration_s = float(value.duration_s)
    msg.point_count = int(value.point_count)
    msg.workspace_checked = bool(value.workspace_checked)
    msg.table_checked = bool(value.table_checked)
    msg.max_velocity_rad_s = float(value.max_velocity_rad_s)
    msg.max_acceleration_rad_s2 = float(value.max_acceleration_rad_s2)
    msg.checked_at = ros_time(clock, value.checked_at)
    return msg


def capabilities(contract: ContractVersion) -> list[CapabilityManifest]:
    manifest = CapabilityManifest()
    manifest.schema_version = "1"
    manifest.name = "jcheng_runtime"
    manifest.version = "0.3.0-dev"
    manifest.contract = contract_to_msg(contract)
    manifest.scopes = ["arm", "gripper", "full"]
    manifest.hardware_access = False
    manifest.publishes = ["/jcheng/telemetry/state", "/jcheng/telemetry/safety"]
    manifest.subscribes = ["/jcheng/telemetry/state", "/safety_stop"]
    manifest.services = []
    manifest.max_rate_hz = 20
    manifest.deadman_ms = 300
    return [manifest]
