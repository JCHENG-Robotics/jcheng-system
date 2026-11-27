"""Dataclass models mirroring the v0.1 contract."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from .errors import ErrorCode
from .timebase import Clock


def _floats(values: Any) -> list[float]:
    return [float(value) for value in values or []]


@dataclass(frozen=True)
class ContractVersion:
    major: int
    minor: int
    patch: int
    contract_hash: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContractVersion":
        return cls(
            major=int(value["major"]),
            minor=int(value["minor"]),
            patch=int(value["patch"]),
            contract_hash=str(value["contract_hash"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
            "contract_hash": self.contract_hash,
        }

    def mismatch(self, other: "ContractVersion") -> tuple[str, str] | None:
        if self.major != other.major:
            return (
                ErrorCode.CONTRACT_MAJOR_MISMATCH.value,
                f"major {self.major} != {other.major}",
            )
        if self.contract_hash != other.contract_hash:
            return (
                ErrorCode.CONTRACT_HASH_MISMATCH.value,
                "contract hash does not match the deployed interfaces",
            )
        return None


@dataclass
class JointTrajectoryPoint:
    positions: list[float] = field(default_factory=list)
    velocities: list[float] = field(default_factory=list)
    accelerations: list[float] = field(default_factory=list)
    time_from_start: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JointTrajectoryPoint":
        return cls(
            positions=_floats(value.get("positions")),
            velocities=_floats(value.get("velocities")),
            accelerations=_floats(value.get("accelerations")),
            time_from_start=float(value.get("time_from_start", 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": self.positions,
            "velocities": self.velocities,
            "accelerations": self.accelerations,
            "time_from_start": self.time_from_start,
        }


@dataclass
class JointTrajectory:
    joint_names: list[str] = field(default_factory=list)
    points: list[JointTrajectoryPoint] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JointTrajectory":
        return cls(
            joint_names=[str(name) for name in value.get("joint_names", [])],
            points=[JointTrajectoryPoint.from_dict(item) for item in value.get("points", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "joint_names": self.joint_names,
            "points": [point.to_dict() for point in self.points],
        }


@dataclass
class BasePulse:
    duty: list[float] = field(default_factory=list)
    duration_s: float = 0.0
    require_stopped_after: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BasePulse":
        return cls(
            duty=_floats(value.get("duty")),
            duration_s=float(value.get("duration_s", 0.0)),
            require_stopped_after=bool(value.get("require_stopped_after", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "duty": self.duty,
            "duration_s": self.duration_s,
            "require_stopped_after": self.require_stopped_after,
        }


@dataclass
class GripperCommand:
    position_rad: float = 0.0
    max_velocity_rad_s: float = 0.0
    duration_s: float = 0.0
    verify_bus: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GripperCommand":
        return cls(
            position_rad=float(value.get("position_rad", 0.0)),
            max_velocity_rad_s=float(value.get("max_velocity_rad_s", 0.0)),
            duration_s=float(value.get("duration_s", 0.0)),
            verify_bus=bool(value.get("verify_bus", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_rad": self.position_rad,
            "max_velocity_rad_s": self.max_velocity_rad_s,
            "duration_s": self.duration_s,
            "verify_bus": self.verify_bus,
        }


@dataclass
class MotionCommand:
    intent_id: str
    idempotency_key: str
    lease_id: str
    source: str
    kind: str
    profile: str
    contract: ContractVersion
    trajectory: JointTrajectory | None = None
    joint_names: list[str] = field(default_factory=list)
    target_positions: list[float] = field(default_factory=list)
    max_velocity_rad_s: float | None = None
    max_acceleration_rad_s2: float | None = None
    duration_s: float | None = None
    target_pose: dict[str, Any] | None = None
    tip_link: str = ""
    frame_id: str = ""
    base_pulse: BasePulse | None = None
    gripper: GripperCommand | None = None
    verify_bus: bool = True
    require_measured_state: bool = True
    expected_profile_hash: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MotionCommand":
        trajectory = value.get("trajectory")
        base_pulse = value.get("base_pulse")
        gripper = value.get("gripper")
        return cls(
            intent_id=str(value["intent_id"]),
            idempotency_key=str(value["idempotency_key"]),
            lease_id=str(value["lease_id"]),
            source=str(value["source"]),
            kind=str(value["kind"]),
            profile=str(value["profile"]),
            contract=ContractVersion.from_dict(value["contract"]),
            trajectory=JointTrajectory.from_dict(trajectory) if trajectory else None,
            joint_names=[str(name) for name in value.get("joint_names", [])],
            target_positions=_floats(value.get("target_positions")),
            max_velocity_rad_s=(
                float(value["max_velocity_rad_s"]) if value.get("max_velocity_rad_s") is not None else None
            ),
            max_acceleration_rad_s2=(
                float(value["max_acceleration_rad_s2"])
                if value.get("max_acceleration_rad_s2") is not None
                else None
            ),
            duration_s=float(value["duration_s"]) if value.get("duration_s") is not None else None,
            target_pose=value.get("target_pose"),
            tip_link=str(value.get("tip_link", "")),
            frame_id=str(value.get("frame_id", "")),
            base_pulse=BasePulse.from_dict(base_pulse) if base_pulse else None,
            gripper=GripperCommand.from_dict(gripper) if gripper else None,
            verify_bus=bool(value.get("verify_bus", True)),
            require_measured_state=bool(value.get("require_measured_state", True)),
            expected_profile_hash=str(value.get("expected_profile_hash", "")),
        )

    def to_dict(self, include_idempotency_key: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "intent_id": self.intent_id,
            "lease_id": self.lease_id,
            "source": self.source,
            "kind": self.kind,
            "profile": self.profile,
            "contract": self.contract.to_dict(),
            "joint_names": self.joint_names,
            "target_positions": self.target_positions,
            "verify_bus": self.verify_bus,
            "require_measured_state": self.require_measured_state,
            "expected_profile_hash": self.expected_profile_hash,
        }
        if include_idempotency_key:
            data["idempotency_key"] = self.idempotency_key
        if self.trajectory is not None:
            data["trajectory"] = self.trajectory.to_dict()
        if self.max_velocity_rad_s is not None:
            data["max_velocity_rad_s"] = self.max_velocity_rad_s
        if self.max_acceleration_rad_s2 is not None:
            data["max_acceleration_rad_s2"] = self.max_acceleration_rad_s2
        if self.duration_s is not None:
            data["duration_s"] = self.duration_s
        if self.target_pose is not None:
            data["target_pose"] = self.target_pose
        if self.tip_link:
            data["tip_link"] = self.tip_link
        if self.frame_id:
            data["frame_id"] = self.frame_id
        if self.base_pulse is not None:
            data["base_pulse"] = self.base_pulse.to_dict()
        if self.gripper is not None:
            data["gripper"] = self.gripper.to_dict()
        return data

    def payload_hash(self) -> str:
        canonical = json.dumps(
            self.to_dict(include_idempotency_key=False),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Lease:
    lease_id: str
    holder: str
    scope: str
    mode: str
    priority: int
    deadman_required: bool
    issued_at: float
    expires_at: float
    contract: ContractVersion

    def active(self, now: float) -> bool:
        return now < self.expires_at

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "holder": self.holder,
            "scope": self.scope,
            "mode": self.mode,
            "priority": self.priority,
            "deadman_required": self.deadman_required,
            "issued_at": clock.iso(self.issued_at),
            "expires_at": clock.iso(self.expires_at),
            "contract": self.contract.to_dict(),
        }


@dataclass(frozen=True)
class SafetyState:
    state: str
    stop_latched: bool
    violation_codes: tuple[str, ...]
    violations: tuple[str, ...]
    config_hash: str
    profile_hash: str
    last_valid_state_at: float
    seq: int

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "header": {"stamp": clock.iso(self.last_valid_state_at), "frame_id": "base_link"},
            "seq": self.seq,
            "state": self.state,
            "stop_latched": self.stop_latched,
            "violation_codes": list(self.violation_codes),
            "violations": list(self.violations),
            "config_hash": self.config_hash,
            "profile_hash": self.profile_hash,
            "last_valid_state_at": clock.iso(self.last_valid_state_at),
        }


@dataclass
class RobotState:
    joint_names: list[str]
    positions: list[float]
    velocities: list[float]
    gripper_position: float
    base_duty: list[float]
    battery_voltage: float
    profile_hash: str
    truth: str
    verification: str
    fresh: bool
    stamp: float

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "header": {"stamp": clock.iso(self.stamp), "frame_id": "base_link"},
            "seq": 0,
            "source": "jcheng_hardware",
            "fresh": self.fresh,
            "joint_names": self.joint_names,
            "positions": self.positions,
            "velocities": self.velocities,
            "gripper_position": self.gripper_position,
            "base_duty": self.base_duty,
            "battery_voltage": self.battery_voltage,
            "profile_hash": self.profile_hash,
            "truth": self.truth,
            "verification": self.verification,
        }


@dataclass
class ValidationReport:
    approved: bool
    reason_code: str
    violations: list[str]
    check_ids: list[str]
    duration_s: float
    point_count: int
    workspace_checked: bool
    table_checked: bool
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    checked_at: float

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "approved": self.approved,
            "reason_code": self.reason_code,
            "check_ids": self.check_ids,
            "violations": self.violations,
            "duration_s": self.duration_s,
            "point_count": self.point_count,
            "workspace_checked": self.workspace_checked,
            "table_checked": self.table_checked,
            "max_velocity_rad_s": self.max_velocity_rad_s,
            "max_acceleration_rad_s2": self.max_acceleration_rad_s2,
            "checked_at": clock.iso(self.checked_at),
        }


@dataclass
class MotionResult:
    intent_id: str
    outcome: str
    reason_code: str
    message: str
    safety: SafetyState
    measured_positions: list[float]
    bus_verified: bool
    calibration_invalidated: bool
    completed_at: float
    evidence_id: str = ""

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "message": self.message,
            "safety": self.safety.to_dict(clock),
            "measured_positions": self.measured_positions,
            "bus_verified": self.bus_verified,
            "calibration_invalidated": self.calibration_invalidated,
            "completed_at": clock.iso(self.completed_at),
            "evidence_id": self.evidence_id,
        }


@dataclass
class StopResult:
    accepted: bool
    safety: SafetyState
    message: str
    requested_at: float
    confirmation: str = "request_accepted"
    physical_stop: str = "unverified"

    def to_dict(self, clock: Clock) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "safety": self.safety.to_dict(clock),
            "message": self.message,
            "requested_at": clock.iso(self.requested_at),
            "confirmation": self.confirmation,
            "physical_stop": self.physical_stop,
        }
