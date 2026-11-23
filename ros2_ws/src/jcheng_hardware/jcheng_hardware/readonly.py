"""Read-only vendor bus adapter.

This module deliberately has no publish, write, torque, EEPROM, or raw
register API. It accepts an injected ``read_positions`` callable, validates
that the caller asks for the known servo IDs in strictly increasing order,
and converts only position pulses to radians.

The real ROS service wrapper lives in ``jcheng_telemetry`` so this module stays
pure Python and testable without a robot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
import hashlib
import math

import yaml

PULSE_MIN = 0
PULSE_MAX = 1000
DEFAULT_RADIANS_PER_TICK = 240.0 / 360.0 * (2.0 * math.pi) / 1000.0


class ReadOnlyViolation(ValueError):
    """The read-only boundary was violated or the sample was invalid."""


@dataclass(frozen=True)
class ReadOnlyJoint:
    name: str
    kind: str
    servo_id: int
    init_raw: int
    direction: int
    pulse_min: int = PULSE_MIN
    pulse_max: int = PULSE_MAX

    def pulse_to_rad(self, pulse: int, radians_per_tick: float) -> float:
        pulse = validate_pulse(pulse)
        return (pulse - self.init_raw) * self.direction * radians_per_tick


@dataclass(frozen=True)
class ReadOnlyMapping:
    path: Path
    joints: tuple[ReadOnlyJoint, ...]
    read_order: tuple[int, ...]
    radians_per_tick: float
    source_sha256: str
    mapping_hash: str

    @classmethod
    def from_file(cls, path: str | Path) -> "ReadOnlyMapping":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ReadOnlyViolation("readonly mapping must be a mapping")
        joints: list[ReadOnlyJoint] = []
        for item in raw.get("joints", []):
            try:
                joint = ReadOnlyJoint(
                    name=str(item["name"]),
                    kind=str(item["kind"]),
                    servo_id=int(item["servo_id"]),
                    init_raw=int(item["init_raw"]),
                    direction=int(item["direction"]),
                    pulse_min=int(item.get("pulse_min", PULSE_MIN)),
                    pulse_max=int(item.get("pulse_max", PULSE_MAX)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ReadOnlyViolation(f"invalid readonly joint: {item!r}") from exc
            if joint.kind not in ("arm", "gripper"):
                raise ReadOnlyViolation(f"{joint.name}: kind must be arm or gripper")
            if joint.direction not in (-1, 1):
                raise ReadOnlyViolation(f"{joint.name}: direction must be -1 or 1")
            if not PULSE_MIN <= joint.init_raw <= PULSE_MAX:
                raise ReadOnlyViolation(f"{joint.name}: init_raw outside 0..1000")
            if not PULSE_MIN <= joint.pulse_min <= joint.pulse_max <= PULSE_MAX:
                raise ReadOnlyViolation(f"{joint.name}: invalid pulse range")
            joints.append(joint)
        if not joints:
            raise ReadOnlyViolation("readonly mapping has no joints")
        names = [joint.name for joint in joints]
        ids = [joint.servo_id for joint in joints]
        if len(set(names)) != len(names):
            raise ReadOnlyViolation("duplicate joint name")
        if len(set(ids)) != len(ids):
            raise ReadOnlyViolation("duplicate servo id")
        if not any(joint.kind == "arm" for joint in joints):
            raise ReadOnlyViolation("at least one arm joint is required")
        if sum(1 for joint in joints if joint.kind == "gripper") != 1:
            raise ReadOnlyViolation("exactly one gripper joint is required")
        read_order = tuple(int(value) for value in raw.get("read_order", []))
        if not read_order:
            raise ReadOnlyViolation("read_order is required")
        if list(read_order) != sorted(read_order) or len(set(read_order)) != len(read_order):
            raise ReadOnlyViolation("read_order must be strictly increasing")
        if set(read_order) != set(ids):
            raise ReadOnlyViolation("read_order must list every servo exactly once")
        radians_per_tick = float(raw.get("radians_per_tick", DEFAULT_RADIANS_PER_TICK))
        if not math.isfinite(radians_per_tick) or radians_per_tick <= 0:
            raise ReadOnlyViolation("radians_per_tick must be positive and finite")
        return cls(
            path=path,
            joints=tuple(joints),
            read_order=read_order,
            radians_per_tick=radians_per_tick,
            source_sha256=str(raw.get("source_sha256", "")),
            mapping_hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    @property
    def arm_joints(self) -> tuple[ReadOnlyJoint, ...]:
        return tuple(joint for joint in self.joints if joint.kind == "arm")

    @property
    def gripper_joint(self) -> ReadOnlyJoint:
        for joint in self.joints:
            if joint.kind == "gripper":
                return joint
        raise ReadOnlyViolation("mapping has no gripper")

    def joint_by_servo(self, servo_id: int) -> ReadOnlyJoint:
        for joint in self.joints:
            if joint.servo_id == int(servo_id):
                return joint
        raise ReadOnlyViolation(f"unknown servo id {servo_id}")


@dataclass(frozen=True)
class ReadOnlySample:
    stamp: float
    pulses: Mapping[int, int]
    arm_positions_rad: Mapping[str, float]
    gripper_position_rad: float
    read_order: tuple[int, ...]
    source: str = "bus"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stamp": self.stamp,
            "source": self.source,
            "pulses": {str(key): value for key, value in self.pulses.items()},
            "arm_positions_rad": dict(self.arm_positions_rad),
            "gripper_position_rad": self.gripper_position_rad,
            "read_order": list(self.read_order),
        }


def validate_pulse(pulse: Any) -> int:
    if isinstance(pulse, bool) or not isinstance(pulse, int):
        raise ReadOnlyViolation(f"pulse must be an integer, got {type(pulse).__name__}")
    if not PULSE_MIN <= pulse <= PULSE_MAX:
        raise ReadOnlyViolation(f"pulse {pulse} outside [{PULSE_MIN}, {PULSE_MAX}]")
    return pulse


class ReadOnlyBridge:
    """Read positions through an injected callable; never publish or write."""

    def __init__(
        self,
        mapping: ReadOnlyMapping,
        read_positions: Callable[[list[int]], Mapping[int, int]],
    ) -> None:
        if not callable(read_positions):
            raise TypeError("read_positions must be callable")
        self.mapping = mapping
        self._read_positions = read_positions
        self.reads = 0

    def read(self, now: float) -> ReadOnlySample:
        ids = list(self.mapping.read_order)
        raw = self._read_positions(ids)
        if not isinstance(raw, Mapping):
            raise ReadOnlyViolation("read_positions must return a mapping")
        if set(raw.keys()) != set(ids):
            missing = sorted(set(ids) - set(raw.keys()))
            extra = sorted(set(raw.keys()) - set(ids))
            raise ReadOnlyViolation(f"bus read ids mismatch: missing={missing} extra={extra}")
        pulses: dict[int, int] = {}
        for servo_id in ids:
            pulses[servo_id] = validate_pulse(raw[servo_id])
        arm_positions = {
            joint.name: joint.pulse_to_rad(pulses[joint.servo_id], self.mapping.radians_per_tick)
            for joint in self.mapping.arm_joints
        }
        gripper = self.mapping.gripper_joint
        self.reads += 1
        return ReadOnlySample(
            stamp=float(now),
            pulses=pulses,
            arm_positions_rad=arm_positions,
            gripper_position_rad=gripper.pulse_to_rad(
                pulses[gripper.servo_id], self.mapping.radians_per_tick
            ),
            read_order=tuple(ids),
        )

    def attempt_write(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("read-only bridge has no write or publish path")

    def health(self) -> dict[str, Any]:
        return {
            "mode": "readonly",
            "mapping_hash": self.mapping.mapping_hash,
            "read_order": list(self.mapping.read_order),
            "reads": self.reads,
            "writes": 0,
        }


class MockReadOnlyBus:
    """Deterministic bus used by tests and the short CI probe."""

    def __init__(self, pulses: Mapping[int, int] | None = None, fail: bool = False) -> None:
        self.pulses = {int(key): validate_pulse(value) for key, value in (pulses or {}).items()}
        self.fail = fail
        self.calls: list[list[int]] = []

    def read_positions(self, ids: list[int]) -> dict[int, int]:
        self.calls.append(list(ids))
        if self.fail:
            raise ReadOnlyViolation("mock bus read failed")
        return {int(servo_id): int(self.pulses.get(int(servo_id), 500)) for servo_id in ids}

    def set_pulse(self, servo_id: int, pulse: int) -> None:
        self.pulses[int(servo_id)] = validate_pulse(pulse)


def load_mapping(path: str | Path) -> ReadOnlyMapping:
    return ReadOnlyMapping.from_file(path)
