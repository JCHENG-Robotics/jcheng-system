"""Read-only telemetry cache and fail-closed safety view.

The cache never promotes a commanded state (``/controller_manager/joint_states``)
to ``measured``. Only a fresh bus sample from ``jcheng_hardware`` can do that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading
from typing import Any, Mapping

from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.models import RobotState, SafetyState

from jcheng_hardware.readonly import ReadOnlySample


@dataclass
class TopicHealth:
    name: str
    last_at: float | None = None
    count: int = 0

    def age(self, now: float) -> float | None:
        if self.last_at is None:
            return None
        return max(0.0, float(now) - self.last_at)

    def to_dict(self, now: float) -> dict[str, Any]:
        age = self.age(now)
        return {
            "name": self.name,
            "last_received_at": self.last_at,
            "age_s": age,
            "fresh": age is not None and age <= 2.0,
            "count": self.count,
        }


@dataclass
class ReadOnlySafetyView:
    state: str
    stop_latched: bool
    violation_codes: tuple[str, ...]
    violations: tuple[str, ...]
    config_hash: str
    profile_hash: str
    last_valid_state_at: float
    seq: int


class TelemetryCache:
    def __init__(
        self,
        joint_names: list[str],
        profile_hash: str,
        clock: Any,
        state_timeout_s: float = 0.5,
        battery_timeout_s: float = 5.0,
    ) -> None:
        if not joint_names:
            raise ValueError("joint_names is required")
        self.joint_names = list(joint_names)
        self.profile_hash = profile_hash
        self.clock = clock
        self.state_timeout_s = float(state_timeout_s)
        self.battery_timeout_s = float(battery_timeout_s)
        self._lock = threading.RLock()
        self._commanded = {name: 0.0 for name in self.joint_names}
        self._commanded_at: float | None = None
        self._measured: ReadOnlySample | None = None
        self._battery_voltage = 0.0
        self._battery_at: float | None = None
        self._topics: dict[str, TopicHealth] = {}
        self._safety_seq = 0

    # ---- updates ----

    def update_joint_state(
        self,
        names: list[str] | tuple[str, ...],
        positions: list[float] | tuple[float, ...],
        at: float | None = None,
    ) -> None:
        if len(names) != len(positions):
            raise ValueError("joint names and positions must have the same length")
        updates: dict[str, float] = {}
        for name, position in zip(names, positions):
            if name not in self._commanded:
                continue
            value = float(position)
            if not math.isfinite(value):
                raise ValueError(f"non-finite position for {name}")
            updates[str(name)] = value
        if not updates:
            return
        with self._lock:
            self._commanded.update(updates)
            self._commanded_at = self.clock.now() if at is None else float(at)
            self._topic("joint_states", self._commanded_at)

    def update_measured(self, sample: ReadOnlySample) -> None:
        keys = set(sample.arm_positions_rad.keys())
        if keys != set(self.joint_names):
            raise ValueError(
                f"measured joints do not match profile: {sorted(keys)} != {sorted(self.joint_names)}"
            )
        for name, value in sample.arm_positions_rad.items():
            if not math.isfinite(float(value)):
                raise ValueError(f"non-finite measured position for {name}")
        if not math.isfinite(float(sample.gripper_position_rad)):
            raise ValueError("non-finite measured gripper position")
        with self._lock:
            self._measured = sample
            self._topic("bus", sample.stamp)

    def update_battery_mv(self, value_mv: int | float, at: float | None = None) -> None:
        millivolts = float(value_mv)
        if not math.isfinite(millivolts) or millivolts < 0:
            raise ValueError("battery millivolts must be finite and non-negative")
        with self._lock:
            self._battery_voltage = millivolts / 1000.0
            self._battery_at = self.clock.now() if at is None else float(at)
            self._topic("battery", self._battery_at)

    def update_topic(self, name: str, at: float | None = None) -> None:
        with self._lock:
            self._topic(name, self.clock.now() if at is None else float(at))

    def _topic(self, name: str, at: float) -> None:
        health = self._topics.get(name)
        if health is None:
            health = TopicHealth(name=name)
            self._topics[name] = health
        health.last_at = float(at)
        health.count += 1

    # ---- views ----

    def measured_age(self, now: float) -> float | None:
        with self._lock:
            if self._measured is None:
                return None
            return max(0.0, float(now) - self._measured.stamp)

    def commanded_age(self, now: float) -> float | None:
        with self._lock:
            if self._commanded_at is None:
                return None
            return max(0.0, float(now) - self._commanded_at)

    def battery_age(self, now: float) -> float | None:
        with self._lock:
            if self._battery_at is None:
                return None
            return max(0.0, float(now) - self._battery_at)

    def snapshot(self, now: float | None = None) -> RobotState:
        now = self.clock.now() if now is None else float(now)
        with self._lock:
            measured_age = None if self._measured is None else max(0.0, now - self._measured.stamp)
            commanded_age = (
                None if self._commanded_at is None else max(0.0, now - self._commanded_at)
            )
            measured_fresh = measured_age is not None and measured_age <= self.state_timeout_s
            commanded_fresh = commanded_age is not None and commanded_age <= self.state_timeout_s
            if measured_fresh and self._measured is not None:
                positions = [
                    float(self._measured.arm_positions_rad[name]) for name in self.joint_names
                ]
                gripper = float(self._measured.gripper_position_rad)
                truth = "measured"
                verification = "bus"
                stamp = float(self._measured.stamp)
                fresh = True
            else:
                positions = [float(self._commanded[name]) for name in self.joint_names]
                gripper = 0.0
                truth = "commanded"
                verification = "timed"
                stamp = self._commanded_at if self._commanded_at is not None else now
                fresh = bool(commanded_fresh)
            return RobotState(
                joint_names=list(self.joint_names),
                positions=positions,
                velocities=[0.0 for _ in self.joint_names],
                gripper_position=gripper,
                base_duty=[0.0, 0.0, 0.0, 0.0],
                battery_voltage=float(self._battery_voltage),
                profile_hash=self.profile_hash,
                truth=truth,
                verification=verification,
                fresh=fresh,
                stamp=stamp,
            )

    def health(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock.now() if now is None else float(now)
        with self._lock:
            measured_age = None if self._measured is None else max(0.0, now - self._measured.stamp)
            commanded_age = (
                None if self._commanded_at is None else max(0.0, now - self._commanded_at)
            )
            battery_age = None if self._battery_at is None else max(0.0, now - self._battery_at)
            measured_fresh = measured_age is not None and measured_age <= self.state_timeout_s
            commanded_fresh = commanded_age is not None and commanded_age <= self.state_timeout_s
            battery_fresh = battery_age is not None and battery_age <= self.battery_timeout_s
            return {
                "mode": "readonly",
                "writes": 0,
                "profile_hash": self.profile_hash,
                "truth": "measured" if measured_fresh else "commanded",
                "verification": "bus" if measured_fresh else "timed",
                "fresh": bool(measured_fresh or commanded_fresh),
                "measured_age_s": measured_age,
                "commanded_age_s": commanded_age,
                "battery_age_s": battery_age,
                "measured_fresh": bool(measured_fresh),
                "commanded_fresh": bool(commanded_fresh),
                "battery_fresh": bool(battery_fresh),
                "topics": {name: item.to_dict(now) for name, item in sorted(self._topics.items())},
            }

    def battery(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock.now() if now is None else float(now)
        with self._lock:
            age = None if self._battery_at is None else max(0.0, now - self._battery_at)
            return {
                "voltage_v": float(self._battery_voltage),
                "age_s": age,
                "fresh": age is not None and age <= self.battery_timeout_s,
            }

    def safety_view(
        self,
        config_hash: str,
        now: float | None = None,
    ) -> ReadOnlySafetyView:
        now = self.clock.now() if now is None else float(now)
        health = self.health(now)
        violations: list[str] = []
        codes: list[str] = []
        if not health["measured_fresh"]:
            violations.append("no fresh measured bus sample; commanded state is not completion proof")
            codes.append(ErrorCode.STATE_STALE.value)
        if not health["battery_fresh"]:
            violations.append("battery telemetry is missing or stale")
            codes.append(ErrorCode.HARDWARE_UNAVAILABLE.value)
        ok = not violations
        with self._lock:
            self._safety_seq += 1
            seq = self._safety_seq
            last_valid = (
                float(self._measured.stamp)
                if self._measured is not None and health["measured_fresh"]
                else now
            )
        return ReadOnlySafetyView(
            state="DISARMED" if ok else "STOP_LATCHED",
            stop_latched=not ok,
            violation_codes=tuple(codes),
            violations=tuple(violations),
            config_hash=config_hash,
            profile_hash=self.profile_hash,
            last_valid_state_at=last_valid,
            seq=seq,
        )


def safety_state_dict(view: ReadOnlySafetyView, clock: Any) -> dict[str, Any]:
    return SafetyState(
        state=view.state,
        stop_latched=view.stop_latched,
        violation_codes=view.violation_codes,
        violations=view.violations,
        config_hash=view.config_hash,
        profile_hash=view.profile_hash,
        last_valid_state_at=view.last_valid_state_at,
        seq=view.seq,
    ).to_dict(clock)
