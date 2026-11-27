"""Deterministic mock vendor bridge and bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import RobotProfile
from .errors import BusReadFailed
from .models import MotionCommand, RobotState
from .timebase import Clock


@dataclass
class MockHardwareBridge:
    profile: RobotProfile
    clock: Clock

    def __post_init__(self) -> None:
        self.positions = [0.0 for _ in self.profile.joint_names]
        self.velocities = [0.0 for _ in self.profile.joint_names]
        self.gripper_position = 0.0
        self.base_duty = [0.0, 0.0, 0.0, 0.0]
        self.battery_voltage = 7.4
        self.truth = "measured"
        self.verification = "bus"
        self.fresh = True
        self.stamp = self.clock.now()
        self.publishers: set[str] = {"jcheng_hardware"}
        self.commands: list[dict[str, Any]] = []
        self.bus_reads: list[dict[str, Any]] = []
        self.bus_failed = False
        self.base_until = 0.0
        self.last_command: MotionCommand | None = None

    def add_publisher(self, name: str) -> None:
        self.publishers.add(name)

    def remove_publisher(self, name: str) -> None:
        self.publishers.discard(name)

    def set_bus_failure(self, failed: bool = True) -> None:
        self.bus_failed = failed

    def state(self, now: float | None = None) -> RobotState:
        now = self.clock.now() if now is None else now
        return RobotState(
            joint_names=list(self.profile.joint_names),
            positions=list(self.positions),
            velocities=list(self.velocities),
            gripper_position=float(self.gripper_position),
            base_duty=list(self.base_duty),
            battery_voltage=float(self.battery_voltage),
            profile_hash=self.profile.profile_hash,
            truth=self.truth,
            verification=self.verification,
            fresh=self.fresh,
            stamp=self.stamp if self.stamp else now,
        )

    def publish(self, command: MotionCommand, now: float | None = None) -> None:
        now = self.clock.now() if now is None else now
        self.last_command = command
        self.commands.append(
            {
                "kind": command.kind,
                "intent_id": command.intent_id,
                "at": now,
                "publishers": sorted(self.publishers),
            }
        )
        if self.bus_failed:
            raise BusReadFailed("mock bus read failed on publish")
        if command.kind in ("JOINT_TRAJECTORY",):
            trajectory = command.trajectory
            if trajectory is not None and trajectory.points:
                self._apply_joint_targets(trajectory.joint_names, trajectory.points[-1].positions)
        elif command.kind in ("JOINT_STEP", "HOME"):
            names = command.joint_names or list(self.profile.joint_names)
            targets = command.target_positions or [0.0] * len(names)
            self._apply_joint_targets(names, targets)
        elif command.kind == "POSE":
            # Mock IK is intentionally trivial: keep the measured posture and
            # let the completion read confirm the mock bus.
            pass
        elif command.kind == "BASE_PULSE" and command.base_pulse is not None:
            self.base_duty = list(command.base_pulse.duty)
            self.base_until = now + command.base_pulse.duration_s
        elif command.kind == "GRIPPER" and command.gripper is not None:
            self.gripper_position = float(command.gripper.position_rad)
        self.stamp = now
        self.fresh = True

    def publish_motor_speeds(self, duty: list[float], now: float | None = None) -> None:
        """Mock base speed write used by ``HardwareAuthority.submit_base``."""

        now = self.clock.now() if now is None else now
        if len(duty) != 4:
            raise ValueError("mock motor speeds require four channels")
        self.commands.append(
            {
                "kind": "SET_MOTOR_SPEEDS",
                "duty": [float(value) for value in duty],
                "at": now,
                "publishers": sorted(self.publishers),
            }
        )
        if self.bus_failed:
            raise BusReadFailed("mock bus read failed on motor speed write")
        self.base_duty = [float(value) for value in duty]
        self.base_until = 0.0
        self.stamp = now
        self.fresh = True

    def _apply_joint_targets(self, names: list[str], targets: list[float]) -> None:
        index = {name: position for position, name in enumerate(self.profile.joint_names)}
        for name, target in zip(names, targets):
            if name in index:
                self.positions[index[name]] = float(target)

    def bus_read(self, now: float | None = None) -> RobotState:
        now = self.clock.now() if now is None else now
        if self.bus_failed:
            raise BusReadFailed("mock bus read failed")
        self.bus_reads.append({"at": now})
        self.stamp = now
        self.fresh = True
        return self.state(now)

    def hold(self, now: float | None = None) -> None:
        now = self.clock.now() if now is None else now
        self.base_duty = [0.0, 0.0, 0.0, 0.0]
        self.base_until = 0.0
        self.stamp = now
        self.fresh = True

    def zero_base(self, now: float | None = None) -> None:
        self.base_duty = [0.0, 0.0, 0.0, 0.0]
        self.base_until = 0.0
        self.stamp = self.clock.now() if now is None else now

    def tick(self, now: float | None = None) -> None:
        now = self.clock.now() if now is None else now
        if self.base_until and now >= self.base_until:
            self.zero_base(now)
