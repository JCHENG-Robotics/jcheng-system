"""ROS-free packing for one atomic multi-joint vendor stream frame (M6).

The vendor SDK accepts all servo id/position pairs in one packet
(`ServosPosition`). This module converts a joint-name -> radians target map
into that ordered pulse list without importing rclpy or publishing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from .readonly import ReadOnlyMapping, validate_pulse

MIN_STREAM_DURATION_S = 0.02
MAX_STREAM_DURATION_S = 0.5


class StreamPackError(ValueError):
    pass


@dataclass(frozen=True)
class PackedStreamEntry:
    name: str
    servo_id: int
    target_rad: float
    pulse: int


@dataclass(frozen=True)
class PackedArmStream:
    duration_s: float
    entries: tuple[PackedStreamEntry, ...]

    def pulses(self) -> dict[str, int]:
        return {entry.name: entry.pulse for entry in self.entries}


def pack_arm_stream(
    mapping: ReadOnlyMapping,
    targets: Mapping[str, float],
    duration_s: float,
    *,
    min_duration_s: float = MIN_STREAM_DURATION_S,
    max_duration_s: float = MAX_STREAM_DURATION_S,
) -> PackedArmStream:
    """Pack every arm joint into one ordered stream frame.

    The frame must cover exactly the mapping's arm joints; a partial frame would
    leave an uncommanded joint between targets. Gripper is a separate scope and
    is not packed here.
    """
    duration = float(duration_s)
    if not math.isfinite(duration):
        raise StreamPackError("duration_s must be finite")
    if not min_duration_s <= duration <= max_duration_s:
        raise StreamPackError(
            f"duration_s must be in [{min_duration_s}, {max_duration_s}]"
        )
    if mapping.radians_per_tick <= 0:
        raise StreamPackError("mapping.radians_per_tick must be positive")
    arm = mapping.arm_joints
    names = {joint.name for joint in arm}
    if set(targets) != names:
        missing = sorted(names - set(targets))
        extra = sorted(set(targets) - names)
        raise StreamPackError(
            f"stream targets must cover exactly the arm joints; missing={missing}, extra={extra}"
        )
    entries = []
    for joint in arm:
        target = float(targets[joint.name])
        if not math.isfinite(target):
            raise StreamPackError(f"{joint.name}: target must be finite")
        pulse = int(
            round(
                joint.init_raw
                + joint.direction * target / mapping.radians_per_tick
            )
        )
        try:
            validated = validate_pulse(pulse)
        except ValueError as exc:
            raise StreamPackError(f"{joint.name}: {exc}") from exc
        entries.append(
            PackedStreamEntry(
                name=joint.name,
                servo_id=int(joint.servo_id),
                target_rad=target,
                pulse=validated,
            )
        )
    return PackedArmStream(duration_s=duration, entries=tuple(entries))
