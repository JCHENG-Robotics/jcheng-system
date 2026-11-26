"""Fail-closed safety supervisor for the mock system."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any
import math
import threading

import yaml

from .config import RobotProfile
from .errors import ErrorCode, MotionRejected
from .models import (
    ContractVersion,
    MotionCommand,
    RobotState,
    SafetyState,
    ValidationReport,
)
from .timebase import Clock

ARM_CONFIRM = "I_HAVE_A_CLEAR_WORKSPACE_AND_SUPPORT"
RESET_CONFIRM = "I_HAVE_VERIFIED_THE_BUS_AND_CLEARED_THE_STOP"

ALLOWED_KINDS = {
    "JOINT_TRAJECTORY": {"moveit", "fast"},
    "JOINT_STEP": {"step", "pose"},
    "POSE": {"pose"},
    "HOME": {"pose", "step"},
    "BASE_PULSE": {"base"},
    "GRIPPER": {"gripper"},
}


@dataclass(frozen=True)
class SafetyLimits:
    state_timeout_s: float = 0.5
    start_state_max_distance_rad: float = 0.08
    trajectory_max_duration_s: float = 30.0
    trajectory_max_points: int = 5000
    arm_confirm_phrase: str = ARM_CONFIRM
    reset_confirm_phrase: str = RESET_CONFIRM

    @classmethod
    def from_file(cls, path: str | Path) -> "SafetyLimits":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            state_timeout_s=float(raw.get("state_timeout_s", 0.5)),
            start_state_max_distance_rad=float(raw.get("start_state_max_distance_rad", 0.08)),
            trajectory_max_duration_s=float(raw.get("trajectory_max_duration_s", 30.0)),
            trajectory_max_points=int(raw.get("trajectory_max_points", 5000)),
            arm_confirm_phrase=str(raw.get("arm_confirm_phrase", ARM_CONFIRM)),
            reset_confirm_phrase=str(raw.get("reset_confirm_phrase", RESET_CONFIRM)),
        )


def _locked(method):
    """Serialize a safety transition; the lock is reentrant."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class SafetySupervisor:
    def __init__(
        self,
        profile: RobotProfile,
        contract: ContractVersion,
        clock: Clock,
        limits: SafetyLimits | None = None,
    ) -> None:
        self.profile = profile
        self.contract = contract
        self.clock = clock
        self.limits = limits or SafetyLimits()
        self._lock = threading.RLock()
        self.state = "DISARMED"
        self.stop_latched = False
        self.unknown_latched = False
        self.violation_codes: list[str] = []
        self.violations: list[str] = []
        self.seq = 0
        self.last_state: RobotState | None = None
        self.last_state_at: float = 0.0
        self.active_motion = False
        self.config_hash = profile.config_hash
        self.profile_hash = profile.profile_hash

    # ---- state ----

    def _bump(self) -> None:
        self.seq += 1

    @_locked
    def current_state(self) -> SafetyState:
        return SafetyState(
            state=self.state,
            stop_latched=self.stop_latched,
            violation_codes=tuple(self.violation_codes),
            violations=tuple(self.violations),
            config_hash=self.config_hash,
            profile_hash=self.profile_hash,
            last_valid_state_at=self.last_state_at,
            seq=self.seq,
        )

    @_locked
    def latch(self, code: ErrorCode | str, message: str) -> SafetyState:
        code_value = code.value if isinstance(code, ErrorCode) else str(code)
        self.stop_latched = True
        self.state = "STOP_LATCHED"
        self.active_motion = False
        if code_value not in self.violation_codes:
            self.violation_codes.append(code_value)
        self.violations.append(message)
        if code_value == ErrorCode.MOTION_OUTCOME_UNKNOWN.value:
            self.unknown_latched = True
        self._bump()
        return self.current_state()

    def _require_contract(self, contract: ContractVersion) -> None:
        mismatch = contract.mismatch(self.contract)
        if mismatch:
            raise MotionRejected(mismatch[0], mismatch[1])

    def _require_fresh_state(self, now: float) -> None:
        if self.last_state is None or now - self.last_state_at > self.limits.state_timeout_s:
            self.latch(ErrorCode.STATE_STALE, "measured state is missing or stale")
            raise MotionRejected(ErrorCode.STATE_STALE, "measured state is missing or stale")

    @_locked
    def update_state(self, robot_state: RobotState, now: float | None = None) -> SafetyState:
        now = self.clock.now() if now is None else now
        self.last_state = robot_state
        self.last_state_at = now
        self._bump()
        if not robot_state.fresh:
            self.latch(ErrorCode.STATE_STALE, "robot state is marked stale")
        if len(robot_state.positions) != len(self.profile.joint_names):
            self.latch(ErrorCode.STATE_STALE, "robot state joint count mismatch")
        return self.current_state()

    @_locked
    def heartbeat(self, now: float | None = None) -> SafetyState:
        now = self.clock.now() if now is None else now
        self.last_state_at = now
        self._bump()
        return self.current_state()

    @_locked
    def tick(self, now: float | None = None) -> SafetyState:
        now = self.clock.now() if now is None else now
        if self.state in ("ARMED_IDLE", "MOTION_ACTIVE"):
            if self.last_state is None or now - self.last_state_at > self.limits.state_timeout_s:
                return self.latch(ErrorCode.SAFETY_HEARTBEAT_MISSING, "safety heartbeat is missing")
        return self.current_state()

    @_locked
    def arm(
        self,
        operator_id: str,
        checklist_hash: str,
        confirm_phrase: str,
        contract: ContractVersion,
        profile_hash: str,
        now: float | None = None,
    ) -> SafetyState:
        self._require_contract(contract)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        if not checklist_hash.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "checklist_hash is required")
        if confirm_phrase != self.limits.arm_confirm_phrase:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "confirmation phrase mismatch")
        if profile_hash and profile_hash != self.profile_hash:
            raise MotionRejected(ErrorCode.PROFILE_HASH_MISMATCH, "profile hash mismatch")
        if self.stop_latched:
            raise MotionRejected(ErrorCode.SAFETY_STOP_LATCHED, "safety stop is latched")
        if self.state != "DISARMED":
            return self.current_state()
        now = self.clock.now() if now is None else now
        if self.last_state is None or now - self.last_state_at > self.limits.state_timeout_s:
            raise MotionRejected(ErrorCode.STATE_STALE, "cannot arm without fresh measured state")
        self.state = "ARMED_IDLE"
        self._bump()
        return self.current_state()

    @_locked
    def disarm(self, operator_id: str, reason: str) -> SafetyState:
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        self.state = "DISARMED"
        self.active_motion = False
        self.violations.append(f"disarmed by {operator_id}: {reason}")
        self._bump()
        return self.current_state()

    @_locked
    def reset(
        self,
        operator_id: str,
        reason: str,
        confirm_phrase: str,
        contract: ContractVersion,
        now: float | None = None,
        bus_ok: bool = True,
    ) -> SafetyState:
        self._require_contract(contract)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        if confirm_phrase != self.limits.reset_confirm_phrase:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "reset confirmation phrase mismatch")
        if not self.stop_latched:
            return self.current_state()
        if self.unknown_latched and not bus_ok:
            raise MotionRejected(
                ErrorCode.MOTION_OUTCOME_UNKNOWN,
                "bus check is required before clearing an unknown outcome",
            )
        now = self.clock.now() if now is None else now
        if self.last_state is None or now - self.last_state_at > self.limits.state_timeout_s:
            raise MotionRejected(ErrorCode.STATE_STALE, "cannot reset without fresh measured state")
        self.stop_latched = False
        self.unknown_latched = False
        self.state = "DISARMED"
        self.active_motion = False
        self.violation_codes.clear()
        self.violations.clear()
        self.violations.append(f"reset by {operator_id}: {reason}")
        self._bump()
        return self.current_state()

    @_locked
    def begin_motion(self) -> SafetyState:
        if self.state != "ARMED_IDLE" or self.stop_latched:
            raise MotionRejected(ErrorCode.SYSTEM_DISARMED, "system is not armed")
        self.state = "MOTION_ACTIVE"
        self.active_motion = True
        self._bump()
        return self.current_state()

    @_locked
    def end_motion(self) -> SafetyState:
        self.active_motion = False
        if self.state == "MOTION_ACTIVE":
            self.state = "ARMED_IDLE"
        self._bump()
        return self.current_state()

    # ---- validation ----

    def _report(
        self,
        now: float,
        *,
        approved: bool,
        code: str,
        violations: list[str],
        check_ids: list[str],
        duration_s: float = 0.0,
        point_count: int = 0,
        workspace_checked: bool = False,
        table_checked: bool = False,
        max_velocity: float = 0.0,
        max_acceleration: float = 0.0,
    ) -> ValidationReport:
        return ValidationReport(
            approved=approved,
            reason_code=code,
            violations=violations,
            check_ids=check_ids,
            duration_s=duration_s,
            point_count=point_count,
            workspace_checked=workspace_checked,
            table_checked=table_checked,
            max_velocity_rad_s=max_velocity,
            max_acceleration_rad_s2=max_acceleration,
            checked_at=now,
        )

    def _reject(
        self,
        now: float,
        code: ErrorCode | str,
        message: str,
        check_ids: list[str],
        **kwargs: Any,
    ) -> ValidationReport:
        return self._report(
            now,
            approved=False,
            code=code.value if isinstance(code, ErrorCode) else str(code),
            violations=[message],
            check_ids=check_ids,
            **kwargs,
        )

    def _within_limits(self, name: str, position: float) -> bool:
        joint = self.profile.joint(name)
        return joint.lower_rad <= position <= joint.upper_rad

    def _start_distance(self, command: MotionCommand) -> float:
        if self.last_state is None:
            return math.inf
        if command.trajectory is not None and command.trajectory.points:
            positions = command.trajectory.points[0].positions
            names = command.trajectory.joint_names
        else:
            positions = command.target_positions
            names = command.joint_names
        if not names:
            return 0.0
        current = dict(zip(self.last_state.joint_names, self.last_state.positions))
        distances = [
            abs(position - current[name])
            for name, position in zip(names, positions)
            if name in current
        ]
        return max(distances, default=math.inf)

    def _tcp(self, position: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(position["x"]),
            float(position["y"]),
            float(position["z"]),
        )

    def _pose_checks(self, pose: dict[str, Any]) -> tuple[bool, bool, list[str]]:
        violations: list[str] = []
        position = pose.get("position", {})
        x, y, z = self._tcp(position)
        lower = self.profile.workspace["lower"]
        upper = self.profile.workspace["upper"]
        if not (lower[0] <= x <= upper[0] and lower[1] <= y <= upper[1] and lower[2] <= z <= upper[2]):
            violations.append(f"target ({x:.3f},{y:.3f},{z:.3f}) is outside the workspace")
            return False, False, violations
        normal = self.profile.table_plane["normal"]
        offset = float(self.profile.table_plane["offset"])
        clearance = float(self.profile.table_plane["clearance_m"])
        height = normal[0] * x + normal[1] * y + normal[2] * z + offset
        if height < clearance:
            return True, False, [f"table clearance {height:.3f} m < {clearance:.3f} m"]
        return True, True, []

    def _check_rates(self, command: MotionCommand, profile_name: str, violations: list[str]) -> tuple[float, float]:
        config = self.profile.profile(profile_name)
        max_velocity = float(config.get("max_velocity_rad_s", 0.0))
        max_acceleration = float(config.get("max_acceleration_rad_s2", 0.0))
        if command.trajectory is None:
            return max_velocity, max_acceleration
        points = command.trajectory.points
        max_seen_v = 0.0
        max_seen_a = 0.0
        for index, point in enumerate(points):
            if point.velocities:
                if len(point.velocities) != len(points[0].positions):
                    violations.append(f"point {index}: velocity length mismatch")
                else:
                    max_seen_v = max(max_seen_v, max(abs(value) for value in point.velocities))
            if point.accelerations:
                if len(point.accelerations) != len(points[0].positions):
                    violations.append(f"point {index}: acceleration length mismatch")
                else:
                    max_seen_a = max(max_seen_a, max(abs(value) for value in point.accelerations))
            if index == 0:
                continue
            previous = points[index - 1]
            dt = point.time_from_start - previous.time_from_start
            if dt <= 0:
                violations.append(f"point {index}: time does not increase")
                continue
            if not point.velocities and len(previous.positions) == len(point.positions):
                for old, new in zip(previous.positions, point.positions):
                    max_seen_v = max(max_seen_v, abs(new - old) / dt)
            if not point.accelerations and previous.velocities and point.velocities:
                for old, new in zip(previous.velocities, point.velocities):
                    max_seen_a = max(max_seen_a, abs(new - old) / dt)
        if max_velocity > 0 and max_seen_v > max_velocity + 1e-9:
            violations.append(
                f"velocity {max_seen_v:.3f} rad/s exceeds {profile_name} limit {max_velocity:.3f}"
            )
        if max_acceleration > 0 and max_seen_a > max_acceleration + 1e-9:
            violations.append(
                f"acceleration {max_seen_a:.3f} rad/s^2 exceeds {profile_name} limit {max_acceleration:.3f}"
            )
        return max_velocity, max_acceleration

    @_locked
    def validate(
        self,
        command: MotionCommand,
        lease_ok: bool,
        lease_code: str,
        now: float | None = None,
    ) -> ValidationReport:
        now = self.clock.now() if now is None else now
        checks = ["contract", "lease", "armed", "state_fresh", "limits", "rates"]
        mismatch = command.contract.mismatch(self.contract)
        if mismatch:
            return self._reject(now, mismatch[0], mismatch[1], checks)
        if not lease_ok:
            return self._reject(now, lease_code or ErrorCode.NO_LEASE.value, "control lease is not valid", checks)
        if self.stop_latched:
            return self._reject(now, ErrorCode.SAFETY_STOP_LATCHED, "safety stop is latched", checks)
        if self.state not in ("ARMED_IDLE", "MOTION_ACTIVE"):
            return self._reject(now, ErrorCode.SYSTEM_DISARMED, "system is not armed", checks)
        if self.last_state is None or now - self.last_state_at > self.limits.state_timeout_s:
            self.latch(ErrorCode.STATE_STALE, "measured state is missing or stale")
            return self._reject(now, ErrorCode.STATE_STALE, "measured state is missing or stale", checks)
        if command.require_measured_state and self.last_state.truth != "measured":
            return self._reject(now, ErrorCode.STATE_STALE, "measured state is required", checks)
        if command.expected_profile_hash and command.expected_profile_hash != self.profile_hash:
            return self._reject(now, ErrorCode.PROFILE_HASH_MISMATCH, "profile hash mismatch", checks)
        if command.kind not in ALLOWED_KINDS:
            return self._reject(now, ErrorCode.UNKNOWN_KIND, f"unknown kind {command.kind}", checks)
        if command.profile not in ALLOWED_KINDS[command.kind]:
            return self._reject(now, ErrorCode.UNKNOWN_PROFILE, f"profile {command.profile} not allowed for {command.kind}", checks)
        if command.kind != "BASE_PULSE" and not command.verify_bus:
            return self._reject(now, ErrorCode.INVALID_COMMAND, "servo commands require bus verification", checks)

        violations: list[str] = []
        duration = 0.0
        points = 0
        workspace_checked = False
        table_checked = False
        max_velocity = 0.0
        max_acceleration = 0.0

        if command.kind == "JOINT_TRAJECTORY":
            trajectory = command.trajectory
            if trajectory is None or not trajectory.points:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "trajectory is required", checks)
            if trajectory.joint_names != self.profile.joint_names:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "joint order must match the robot profile", checks)
            points = len(trajectory.points)
            if points > self.limits.trajectory_max_points:
                return self._reject(now, ErrorCode.DURATION_LIMIT, "too many trajectory points", checks)
            duration = trajectory.points[-1].time_from_start
            if duration > self.limits.trajectory_max_duration_s:
                return self._reject(now, ErrorCode.DURATION_LIMIT, "trajectory duration exceeds limit", checks)
            for index, point in enumerate(trajectory.points):
                if len(point.positions) != len(self.profile.joint_names):
                    return self._reject(now, ErrorCode.INVALID_COMMAND, f"point {index}: position length mismatch", checks)
                if index == 0 and abs(point.time_from_start) > 1e-9:
                    return self._reject(now, ErrorCode.INVALID_COMMAND, "first point time must be 0", checks)
                if index > 0 and point.time_from_start <= trajectory.points[index - 1].time_from_start:
                    return self._reject(now, ErrorCode.INVALID_COMMAND, f"point {index}: time is not increasing", checks)
                for name, position in zip(trajectory.joint_names, point.positions):
                    if not math.isfinite(position) or not self._within_limits(name, position):
                        return self._reject(now, ErrorCode.JOINT_LIMIT, f"point {index}: {name} outside limits", checks)
            distance = self._start_distance(command)
            if distance > self.limits.start_state_max_distance_rad:
                return self._reject(now, ErrorCode.START_STATE, f"start distance {distance:.3f} rad", checks)
            max_velocity, max_acceleration = self._check_rates(command, command.profile, violations)
            if violations:
                return self._reject(now, ErrorCode.VELOCITY_LIMIT, violations[0], checks, duration_s=duration, point_count=points)

        elif command.kind in ("JOINT_STEP", "HOME"):
            if command.kind == "HOME":
                names = command.joint_names or self.profile.joint_names
                positions = command.target_positions or [0.0] * len(names)
            else:
                names = command.joint_names
                positions = command.target_positions
            if not names or len(names) != len(positions):
                return self._reject(now, ErrorCode.INVALID_COMMAND, "joint names and targets must align", checks)
            if command.profile == "step" and len(names) != 1:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "step requires exactly one joint", checks)
            if len(set(names)) != len(names):
                return self._reject(now, ErrorCode.INVALID_COMMAND, "duplicate joint", checks)
            for name, position in zip(names, positions):
                if name not in self.profile.joints:
                    return self._reject(now, ErrorCode.INVALID_COMMAND, f"unknown joint {name}", checks)
                if not math.isfinite(position) or not self._within_limits(name, position):
                    return self._reject(now, ErrorCode.JOINT_LIMIT, f"{name} outside limits", checks)
            if command.duration_s is None or command.duration_s <= 0:
                return self._reject(now, ErrorCode.DURATION_LIMIT, "duration_s is required", checks)
            profile_config = self.profile.profile(command.profile)
            if command.profile == "step":
                if not (float(profile_config["min_duration_s"]) <= command.duration_s <= float(profile_config["max_duration_s"])):
                    return self._reject(now, ErrorCode.DURATION_LIMIT, "duration outside step profile", checks)
                step = max(abs(position - self.last_state.positions[self.profile.joint_order.index(name)]) for name, position in zip(names, positions))
                if step > float(profile_config["max_step_rad"]) + 1e-9:
                    return self._reject(now, ErrorCode.STEP_LIMIT, f"step {step:.3f} rad exceeds limit", checks)
            else:
                limit = float(profile_config.get("max_velocity_rad_s", 0.0)) * command.duration_s
                step = max(abs(position - self.last_state.positions[self.profile.joint_order.index(name)]) for name, position in zip(names, positions))
                if limit > 0 and step > limit + 1e-9:
                    return self._reject(now, ErrorCode.STEP_LIMIT, f"step {step:.3f} rad exceeds profile", checks)
            distance = self._start_distance(command)
            if distance > self.limits.start_state_max_distance_rad:
                return self._reject(now, ErrorCode.START_STATE, f"start distance {distance:.3f} rad", checks)
            duration = float(command.duration_s)
            max_velocity = float(command.max_velocity_rad_s or self.profile.profile(command.profile).get("max_velocity_rad_s", 0.0))
            max_acceleration = float(command.max_acceleration_rad_s2 or 0.0)

        elif command.kind == "POSE":
            if command.target_pose is None:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "target_pose is required", checks)
            if not command.tip_link:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "tip_link is required", checks)
            if command.frame_id and command.frame_id != "base_link":
                return self._reject(now, ErrorCode.INVALID_COMMAND, "frame_id must be base_link", checks)
            workspace_checked, table_checked, pose_violations = self._pose_checks(command.target_pose)
            if pose_violations:
                code = ErrorCode.WORKSPACE if not workspace_checked else ErrorCode.TABLE_CLEARANCE
                return self._reject(now, code, pose_violations[0], checks, workspace_checked=workspace_checked, table_checked=table_checked)

        elif command.kind == "BASE_PULSE":
            pulse = command.base_pulse
            if pulse is None:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "base_pulse is required", checks)
            if len(pulse.duty) != 4:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "base pulse requires four channels", checks)
            limit = float(self.profile.profile("base")["max_abs_duty_normalized"])
            if any(not math.isfinite(value) or abs(value) > limit + 1e-9 for value in pulse.duty):
                return self._reject(now, ErrorCode.INVALID_COMMAND, "base duty outside profile", checks)
            if not (0 < pulse.duration_s <= float(self.profile.profile("base")["max_duration_s"])):
                return self._reject(now, ErrorCode.DURATION_LIMIT, "base pulse duration outside profile", checks)
            duration = pulse.duration_s

        elif command.kind == "GRIPPER":
            gripper = command.gripper
            if gripper is None:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "gripper command is required", checks)
            config = self.profile.profiles.get("gripper", {})
            minimum = float(config.get("min_rad", self.profile.gripper_closed_rad))
            maximum = float(config.get("max_rad", self.profile.gripper_open_rad))
            if not (minimum - 1e-9 <= gripper.position_rad <= maximum + 1e-9):
                return self._reject(now, ErrorCode.JOINT_LIMIT, "gripper position outside limits", checks)
            max_velocity = float(config.get("max_velocity_rad_s", 0.0))
            if max_velocity > 0 and gripper.max_velocity_rad_s > max_velocity + 1e-9:
                return self._reject(now, ErrorCode.VELOCITY_LIMIT, "gripper velocity outside profile", checks)
            if gripper.duration_s <= 0:
                return self._reject(now, ErrorCode.DURATION_LIMIT, "gripper duration must be positive", checks)
            if not gripper.verify_bus:
                return self._reject(now, ErrorCode.INVALID_COMMAND, "gripper requires bus verification", checks)
            duration = gripper.duration_s

        return self._report(
            now,
            approved=True,
            code=ErrorCode.OK.value,
            violations=[],
            check_ids=checks,
            duration_s=duration,
            point_count=points,
            workspace_checked=workspace_checked,
            table_checked=table_checked,
            max_velocity=max_velocity,
            max_acceleration=max_acceleration,
        )
