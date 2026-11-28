"""Read-only PiPER X backend for the fixed cell (P2).

This module speaks the vendor protocol v2 feedback frames over SocketCAN. It
opens ``CAN_RAW`` sockets only to receive: there is no send method, no torque
enable and no motion path in this file. Decoding is pure Python, so the wire
format is testable without a robot.

Wire format (``piper_sdk`` 0.6.2, protocol v2, big-endian):

===== ============================================================
0x2A1 status: ctrl_mode, arm_status, mode_feed, teach_status,
      motion_status, trajectory_num, err_code (uint16)
0x2A5 joints 1-2, 0x2A6 joints 3-4, 0x2A7 joints 5-6; two int32 per
      frame, unit 0.001 degree
0x2A8 gripper: angle int32 (0.001 degree), effort int16, status uint8
===== ============================================================

The offsets are the vendor defaults; a unit whose feedback offset was
reconfigured reports different IDs and is ignored until its channel profile is
updated.

Calibration note: the feedback zero is the vendor controller zero, not the
URDF zero. Do not apply URDF joint limits to these readings without a
per-unit calibration record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import select
import socket
import struct

CAN_ID_STATUS = 0x2A1
CAN_ID_JOINT_12 = 0x2A5
CAN_ID_JOINT_34 = 0x2A6
CAN_ID_JOINT_56 = 0x2A7
CAN_ID_GRIPPER = 0x2A8

KNOWN_IDS = (
    CAN_ID_STATUS,
    CAN_ID_JOINT_12,
    CAN_ID_JOINT_34,
    CAN_ID_JOINT_56,
    CAN_ID_GRIPPER,
)

JOINT_NAMES = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6")
JOINT_ANGLE_UNIT_DEG = 0.001

CTRL_MODE_STANDBY = 0x00
CTRL_MODE_CAN = 0x01
CTRL_MODE_TEACHING = 0x02
CTRL_MODE_LINKAGE_TEACH_INPUT = 0x06

ARM_STATUS_NORMAL = 0x00
ARM_STATUS_NAMES = {
    0x00: "normal",
    0x01: "emergency_stop",
    0x02: "no_solution",
    0x03: "singularity",
    0x04: "target_angle_limit",
    0x05: "joint_communication",
    0x06: "joint_brake_closed",
    0x07: "collision",
    0x08: "teaching_overspeed",
    0x09: "joint_state",
    0x0A: "other",
    0x0B: "teaching_record",
    0x0C: "teaching_execute",
    0x0D: "teaching_pause",
    0x0E: "controller_ntc_overtemp",
    0x0F: "release_resistor_ntc_overtemp",
}

# Classic CAN frame: canid_t + dlc + 3 pad bytes + 8 data bytes.
_CAN_FRAME = struct.Struct("=IB3x8s")
_CAN_EFF_FLAG = 0x80000000
_CAN_RTR_FLAG = 0x40000000
_CAN_SFF_MASK = 0x000007FF


class PiperFrameError(ValueError):
    """A feedback frame is malformed or carries an unknown CAN ID."""


def _require_payload(data: bytes, can_id: int) -> bytes:
    payload = bytes(data)
    if len(payload) != 8:
        raise PiperFrameError(f"0x{can_id:03X}: expected 8 data bytes, got {len(payload)}")
    return payload


def _signed(payload: bytes, first: int, second: int) -> int:
    return int.from_bytes(payload[first:second], "big", signed=True)


@dataclass(frozen=True)
class PiperStatus:
    """Decoded 0x2A1 arm status frame."""

    ctrl_mode: int
    arm_status: int
    mode_feed: int
    teach_status: int
    motion_status: int
    trajectory_num: int
    err_code: int

    @property
    def is_fault(self) -> bool:
        return self.arm_status != ARM_STATUS_NORMAL or self.err_code != 0

    @property
    def arm_status_name(self) -> str:
        return ARM_STATUS_NAMES.get(self.arm_status, "unknown")

    @property
    def joint_limit_bits(self) -> int:
        # Byte 6 of the frame (high byte of err_code): one bit per joint.
        return (self.err_code >> 8) & 0x003F

    @property
    def joint_comm_bits(self) -> int:
        # Byte 7 of the frame (low byte of err_code): one bit per joint.
        return self.err_code & 0x003F


@dataclass(frozen=True)
class PiperGripper:
    """Decoded 0x2A8 gripper frame. ``angle_deg`` is the vendor unit."""

    angle_deg: float
    effort: int
    status_code: int


@dataclass(frozen=True)
class PiperJointPair:
    """Decoded 0x2A5/0x2A6/0x2A7 pair, angular degrees."""

    first_deg: float
    second_deg: float


def decode_status(data: bytes) -> PiperStatus:
    payload = _require_payload(data, CAN_ID_STATUS)
    return PiperStatus(
        ctrl_mode=payload[0],
        arm_status=payload[1],
        mode_feed=payload[2],
        teach_status=payload[3],
        motion_status=payload[4],
        trajectory_num=payload[5],
        err_code=int.from_bytes(payload[6:8], "big", signed=False),
    )


def decode_joint_pair(data: bytes, can_id: int) -> PiperJointPair:
    if can_id not in (CAN_ID_JOINT_12, CAN_ID_JOINT_34, CAN_ID_JOINT_56):
        raise PiperFrameError(f"0x{can_id:03X}: not a joint feedback frame")
    payload = _require_payload(data, can_id)
    return PiperJointPair(
        first_deg=_signed(payload, 0, 4) * JOINT_ANGLE_UNIT_DEG,
        second_deg=_signed(payload, 4, 8) * JOINT_ANGLE_UNIT_DEG,
    )


def decode_gripper(data: bytes) -> PiperGripper:
    payload = _require_payload(data, CAN_ID_GRIPPER)
    return PiperGripper(
        angle_deg=_signed(payload, 0, 4) * JOINT_ANGLE_UNIT_DEG,
        effort=_signed(payload, 4, 6),
        status_code=payload[6],
    )


def deg_to_rad(angle_deg: float) -> float:
    return math.radians(angle_deg)


@dataclass
class PiperChannelState:
    """Latest decoded state of one channel, assembled from feedback frames."""

    joints_deg: dict[str, float] = field(default_factory=dict)
    status: PiperStatus | None = None
    gripper: PiperGripper | None = None
    frames: int = 0

    @property
    def joints_rad(self) -> dict[str, float]:
        return {name: deg_to_rad(value) for name, value in self.joints_deg.items()}

    @property
    def complete(self) -> bool:
        return len(self.joints_deg) == len(JOINT_NAMES) and self.status is not None

    def apply(self, can_id: int, data: bytes) -> bool:
        """Decode one frame. Returns False for CAN IDs this backend ignores."""
        if can_id == CAN_ID_STATUS:
            self.status = decode_status(data)
        elif can_id in (CAN_ID_JOINT_12, CAN_ID_JOINT_34, CAN_ID_JOINT_56):
            pair = decode_joint_pair(data, can_id)
            first = {CAN_ID_JOINT_12: 0, CAN_ID_JOINT_34: 2, CAN_ID_JOINT_56: 4}[can_id]
            self.joints_deg[JOINT_NAMES[first]] = pair.first_deg
            self.joints_deg[JOINT_NAMES[first + 1]] = pair.second_deg
        elif can_id == CAN_ID_GRIPPER:
            self.gripper = decode_gripper(data)
        else:
            return False
        self.frames += 1
        return True


@dataclass(frozen=True)
class PiperChannel:
    """Deployment identity of one candleLight channel.

    ``role`` is ``leader`` (teach input, read-only by construction) or
    ``follower`` (motion output). The serial pins the udev name.
    """

    interface: str
    role: str
    serial: str
    usb_path: str

    def __post_init__(self) -> None:
        if self.role not in ("leader", "follower"):
            raise ValueError(f"role must be leader or follower, got {self.role!r}")
        if not self.interface.startswith("can"):
            raise ValueError(f"interface must be a can name, got {self.interface!r}")
        if not self.serial:
            raise ValueError("serial is required to pin the channel")


class PiperReadOnlyBus:
    """Receive-only SocketCAN endpoint for one PiPER channel.

    The class deliberately exposes no send path. The underlying socket is
    created non-blocking and bound to a single interface; frames from any other
    interface cannot arrive on it.
    """

    def __init__(self, interface: str) -> None:
        if not interface.startswith("can"):
            raise ValueError(f"interface must be a can name, got {interface!r}")
        self.interface = interface
        self._socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._socket.bind((interface,))
        self._socket.setblocking(False)

    def fileno(self) -> int:
        return self._socket.fileno()

    def close(self) -> None:
        self._socket.close()

    def __enter__(self) -> "PiperReadOnlyBus":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def receive(self, timeout: float | None = 0.0) -> list[tuple[int, bytes]]:
        """Return pending ``(can_id, data)`` frames, optionally waiting."""
        if timeout is None:
            ready, _, _ = select.select([self._socket], [], [], None)
        elif timeout > 0:
            ready, _, _ = select.select([self._socket], [], [], timeout)
        else:
            ready = [self._socket]
        frames: list[tuple[int, bytes]] = []
        while ready:
            try:
                raw = self._socket.recv(_CAN_FRAME.size)
            except BlockingIOError:
                break
            if len(raw) < _CAN_FRAME.size:
                break
            word, dlc, data = _CAN_FRAME.unpack(raw)
            if word & (_CAN_EFF_FLAG | _CAN_RTR_FLAG):
                continue
            can_id = word & _CAN_SFF_MASK
            frames.append((can_id, data[:dlc]))
            ready, _, _ = select.select([self._socket], [], [], 0)
        return frames
