from __future__ import annotations

import math

import pytest

from jcheng_vendor.piper_readonly import (
    ARM_STATUS_NORMAL,
    CAN_ID_GRIPPER,
    CAN_ID_JOINT_12,
    CAN_ID_JOINT_34,
    CAN_ID_JOINT_56,
    CAN_ID_STATUS,
    CTRL_MODE_CAN,
    CTRL_MODE_TEACHING,
    JOINT_NAMES,
    PiperChannel,
    PiperChannelState,
    PiperFrameError,
    PiperReadOnlyBus,
    decode_gripper,
    decode_joint_pair,
    decode_status,
    deg_to_rad,
)

# Golden frames captured from the fixed cell on 2026-11-28 (right arm = leader
# on can_leader1, left arm = follower on can_follower1).
LEADER_STATUS = bytes.fromhex("02 00 00 01 00 00 00 00")
FOLLOWER_STATUS = bytes.fromhex("01 00 01 02 00 00 00 00")
LEADER_JOINTS = {
    CAN_ID_JOINT_12: bytes.fromhex("FF FF F0 D3 FF FF F8 9D"),
    CAN_ID_JOINT_34: bytes.fromhex("00 00 09 D4 00 00 39 4B"),
    CAN_ID_JOINT_56: bytes.fromhex("FF FF D8 A8 00 00 28 6D"),
}
FOLLOWER_JOINTS = {
    CAN_ID_JOINT_12: bytes.fromhex("FF FF E3 6C 00 00 0A E7"),
    CAN_ID_JOINT_34: bytes.fromhex("00 00 00 00 FF FF E5 8F"),
    CAN_ID_JOINT_56: bytes.fromhex("00 00 23 CE FF FF D1 27"),
}
FOLLOWER_GRIPPER = bytes.fromhex("00 00 00 64 FF BE 40 00")
LEADER_GRIPPER = bytes.fromhex("FF FF F5 D8 00 01 40 00")


def test_decode_status_leader_and_follower() -> None:
    leader = decode_status(LEADER_STATUS)
    assert leader.ctrl_mode == CTRL_MODE_TEACHING
    assert leader.arm_status == ARM_STATUS_NORMAL
    assert leader.teach_status == 0x01
    assert leader.err_code == 0
    assert not leader.is_fault

    follower = decode_status(FOLLOWER_STATUS)
    assert follower.ctrl_mode == CTRL_MODE_CAN
    assert follower.mode_feed == 0x01
    assert follower.teach_status == 0x02
    assert not follower.is_fault


def test_decode_status_fault_bits() -> None:
    status = decode_status(bytes.fromhex("01 01 01 02 00 00 00 21"))
    assert status.is_fault
    assert status.joint_limit_bits == 0x00
    assert status.joint_comm_bits == 0x21
    assert status.arm_status_name == "emergency_stop"


def test_decode_joint_pairs_match_capture() -> None:
    expected = {
        CAN_ID_JOINT_12: (-3.885, -1.891),
        CAN_ID_JOINT_34: (2.516, 14.667),
        CAN_ID_JOINT_56: (-10.072, 10.349),
    }
    for can_id, pair in LEADER_JOINTS.items():
        decoded = decode_joint_pair(pair, can_id)
        assert (decoded.first_deg, decoded.second_deg) == pytest.approx(expected[can_id])

    expected = {
        CAN_ID_JOINT_12: (-7.316, 2.791),
        CAN_ID_JOINT_34: (0.0, -6.769),
        CAN_ID_JOINT_56: (9.166, -11.993),
    }
    for can_id, pair in FOLLOWER_JOINTS.items():
        decoded = decode_joint_pair(pair, can_id)
        assert (decoded.first_deg, decoded.second_deg) == pytest.approx(expected[can_id])


def test_decode_gripper() -> None:
    gripper = decode_gripper(FOLLOWER_GRIPPER)
    assert gripper.angle_deg == pytest.approx(0.1)
    assert gripper.effort == -66
    assert gripper.status_code == 0x40

    leader = decode_gripper(LEADER_GRIPPER)
    assert leader.angle_deg == pytest.approx(-2.6)
    assert leader.effort == 1


def test_state_assembles_six_joints() -> None:
    state = PiperChannelState()
    for can_id, data in LEADER_JOINTS.items():
        assert state.apply(can_id, data)
    assert list(state.joints_deg) == list(JOINT_NAMES)
    assert state.complete is False  # no status frame yet
    assert state.apply(CAN_ID_STATUS, LEADER_STATUS)
    assert state.complete
    assert state.status is not None
    assert state.status.ctrl_mode == CTRL_MODE_TEACHING
    assert state.joints_rad["joint1"] == pytest.approx(deg_to_rad(-3.885))
    assert state.frames == 4


def test_state_ignores_unknown_ids() -> None:
    state = PiperChannelState()
    assert state.apply(0x1C0, bytes(8)) is False
    assert state.apply(0x251, bytes(8)) is False
    assert state.frames == 0


def test_malformed_frames_rejected() -> None:
    with pytest.raises(PiperFrameError):
        decode_status(b"\x00\x00")
    with pytest.raises(PiperFrameError):
        decode_joint_pair(bytes(8), 0x2A1)
    with pytest.raises(PiperFrameError):
        decode_gripper(b"\x00" * 7)


def test_bus_exposes_no_send_path() -> None:
    for name in ("send", "sendto", "sendmsg", "sendall"):
        assert not hasattr(PiperReadOnlyBus, name), name


def test_channel_validation() -> None:
    channel = PiperChannel(
        interface="can_leader1",
        role="leader",
        serial="002900324148570B20343133",
        usb_path="1-4.4.3",
    )
    assert channel.role == "leader"
    with pytest.raises(ValueError):
        PiperChannel(interface="can0", role="arm", serial="x", usb_path="1-1")
    with pytest.raises(ValueError):
        PiperChannel(interface="eth0", role="leader", serial="x", usb_path="1-1")
    with pytest.raises(ValueError):
        PiperChannel(interface="can0", role="leader", serial="", usb_path="1-1")


def test_deg_to_rad() -> None:
    assert deg_to_rad(180.0) == pytest.approx(math.pi)
