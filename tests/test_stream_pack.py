"""M6 atomic stream packing tests (ROS-free)."""

from __future__ import annotations

import pytest

from jcheng_hardware.readonly import ReadOnlyMapping
from jcheng_hardware.streaming import StreamPackError, pack_arm_stream


@pytest.fixture(scope="module")
def mapping(root) -> ReadOnlyMapping:
    return ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )


def _targets(mapping: ReadOnlyMapping, value: float = 0.0) -> dict[str, float]:
    return {joint.name: value for joint in mapping.arm_joints}


def test_pack_covers_every_arm_joint_in_mapping_order(mapping: ReadOnlyMapping) -> None:
    packed = pack_arm_stream(mapping, _targets(mapping), 0.1)
    assert [entry.name for entry in packed.entries] == [
        joint.name for joint in mapping.arm_joints
    ]
    assert [entry.servo_id for entry in packed.entries] == [
        joint.servo_id for joint in mapping.arm_joints
    ]
    assert all(entry.pulse == joint.init_raw for entry, joint in zip(packed.entries, mapping.arm_joints))
    assert packed.duration_s == pytest.approx(0.1)


def test_pack_converts_radians_to_pulses(mapping: ReadOnlyMapping) -> None:
    joint = mapping.arm_joints[0]
    packed = pack_arm_stream(mapping, _targets(mapping) | {joint.name: 0.1}, 0.05)
    entry = next(item for item in packed.entries if item.name == joint.name)
    expected = round(
        joint.init_raw + joint.direction * 0.1 / mapping.radians_per_tick
    )
    assert entry.pulse == expected
    assert entry.target_rad == pytest.approx(0.1)


def test_partial_frame_is_refused(mapping: ReadOnlyMapping) -> None:
    targets = _targets(mapping)
    targets.pop(next(iter(targets)))
    with pytest.raises(StreamPackError, match="missing="):
        pack_arm_stream(mapping, targets, 0.1)


def test_extra_joint_is_refused(mapping: ReadOnlyMapping) -> None:
    targets = _targets(mapping)
    targets["gripper"] = 0.0
    with pytest.raises(StreamPackError, match="extra="):
        pack_arm_stream(mapping, targets, 0.1)


def test_non_finite_target_is_refused(mapping: ReadOnlyMapping) -> None:
    targets = _targets(mapping)
    targets[next(iter(targets))] = float("nan")
    with pytest.raises(StreamPackError, match="finite"):
        pack_arm_stream(mapping, targets, 0.1)


@pytest.mark.parametrize("duration", [0.0, 0.01, 0.6, 5.0])
def test_duration_outside_the_stream_window_is_refused(
    mapping: ReadOnlyMapping, duration: float
) -> None:
    with pytest.raises(StreamPackError, match="duration_s"):
        pack_arm_stream(mapping, _targets(mapping), duration)


def test_out_of_range_pulse_is_refused(mapping: ReadOnlyMapping) -> None:
    joint = mapping.arm_joints[0]
    targets = _targets(mapping) | {joint.name: 100.0}
    with pytest.raises(StreamPackError, match="outside"):
        pack_arm_stream(mapping, targets, 0.1)
