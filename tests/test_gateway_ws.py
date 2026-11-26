"""WebSocket stream tests for the mock and read-only JCHENG gateways.

The test client is a small stdlib RFC 6455 implementation: the system
repository intentionally has no WebSocket runtime dependency, and this proves
the server framing without importing a client library.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
from pathlib import Path

from jcheng_hardware.readonly import MockReadOnlyBus, ReadOnlyBridge, ReadOnlyMapping
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.gateway import GatewayApp, GatewayServer
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock
from jcheng_telemetry.core import TelemetryCache
from jcheng_telemetry.evidence import ReadOnlyEvidence
from jcheng_telemetry.gateway import ReadOnlyGatewayApp, ReadOnlyGatewayServer

WS_PATH = "/v1/ws/state"


class _Reader:
    """Buffered frame reader; keeps bytes that arrive with the 101 response."""

    def __init__(self, sock: socket.socket, initial: bytes = b"") -> None:
        self.sock = sock
        self.buffer = bytearray(initial)

    def exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("connection closed while reading")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def frame(self) -> tuple[int, bytes]:
        first, second = self.exact(2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.exact(8))[0]
        assert not masked, "server frames must not be masked"
        return opcode, self.exact(length)

    def envelope(self) -> dict:
        opcode, payload = self.frame()
        assert opcode == 0x1, f"expected a text frame, got opcode {opcode}"
        return json.loads(payload.decode("utf-8"))


def _handshake(
    port: int,
    token: str | None = None,
    *,
    path: str = WS_PATH,
    upgrade: bool = True,
) -> tuple[socket.socket, str, bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        "Host: 127.0.0.1",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if upgrade:
        lines.extend(["Upgrade: websocket", "Connection: Upgrade"])
    if token:
        lines.append(f"Authorization: Bearer {token}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    head, _, leftover = bytes(response).partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    return sock, status, leftover


def _client_frame(opcode: int, payload: bytes = b"") -> bytes:
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + mask + masked


def _readonly_app(root: Path) -> ReadOnlyGatewayApp:
    profile = RobotProfile.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    )
    contract = load_contract(root / "configs" / "contract.yaml")
    mapping = ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )
    clock = ManualClock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    bus = MockReadOnlyBus({joint.servo_id: 500 for joint in mapping.joints})
    bus.set_pulse(mapping.gripper_joint.servo_id, 170)
    cache.update_measured(ReadOnlyBridge(mapping, bus.read_positions).read(0.2))
    cache.update_joint_state(
        profile.joint_names, [0.0] * len(profile.joint_names), at=0.2
    )
    cache.update_battery_mv(7400, at=0.2)
    cache.update_topic("/depth_cam/rgb/image_raw", at=0.2)
    cache.update_topic("/tf", at=0.2)
    return ReadOnlyGatewayApp(
        cache=cache,
        contract=contract.version.to_dict(),
        profile_hash=profile.profile_hash,
        config_hash=profile.config_hash,
        domain_id=0,
        evidence=ReadOnlyEvidence(clock),
        token="token",
        max_requests_per_second=100,
    )


def test_core_ws_streams_hello_state_and_safety(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(
        GatewayApp(stack, "token", max_requests_per_second=100)
    ) as server:
        sock, status, leftover = _handshake(server.port, "token")
        assert status.startswith("HTTP/1.1 101"), status
        reader = _Reader(sock, leftover)
        try:
            hello = reader.envelope()
            state = reader.envelope()
            safety = reader.envelope()
        finally:
            sock.close()

    assert hello["v"] == 1
    assert hello["type"] == "hello"
    assert hello["payload"]["contract"]["contract_hash"]
    assert hello["payload"]["read_only"] is True

    assert state["v"] == 1
    assert state["type"] == "state"
    assert state["payload"]["truth"] == "measured"
    assert state["payload"]["verification"] == "bus"

    assert safety["v"] == 1
    assert safety["type"] == "safety"
    assert safety["payload"]["stop_latched"] is False


def test_core_ws_requires_bearer(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(
        GatewayApp(stack, "token", max_requests_per_second=100)
    ) as server:
        sock, status, _ = _handshake(server.port)
        sock.close()
    assert status.startswith("HTTP/1.1 401"), status


def test_core_ws_answers_ping(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(
        GatewayApp(stack, "token", max_requests_per_second=100)
    ) as server:
        sock, status, leftover = _handshake(server.port, "token")
        assert status.startswith("HTTP/1.1 101"), status
        reader = _Reader(sock, leftover)
        try:
            hello = reader.envelope()
            assert hello["type"] == "hello"
            sock.sendall(_client_frame(0x9, b"ping"))
            pong = None
            for _ in range(8):
                opcode, payload = reader.frame()
                if opcode == 0xA:
                    pong = payload
                    break
            assert pong == b"ping"
        finally:
            sock.close()


def test_ws_path_without_upgrade_is_rejected(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(
        GatewayApp(stack, "token", max_requests_per_second=100)
    ) as server:
        sock, status, _ = _handshake(server.port, "token", upgrade=False)
        sock.close()
    assert status.startswith("HTTP/1.1 426"), status


def test_readonly_ws_streams_state_and_safety(root: Path) -> None:
    with ReadOnlyGatewayServer(_readonly_app(root)) as server:
        sock, status, leftover = _handshake(server.port, "token")
        assert status.startswith("HTTP/1.1 101"), status
        reader = _Reader(sock, leftover)
        try:
            hello = reader.envelope()
            state = reader.envelope()
            safety = reader.envelope()
        finally:
            sock.close()

    assert hello["type"] == "hello"
    assert hello["payload"]["mode"] == "readonly"
    assert hello["payload"]["read_only"] is True
    assert state["type"] == "state"
    assert state["payload"]["truth"] == "measured"
    assert safety["type"] == "safety"
    assert safety["payload"]["state"] == "DISARMED"
    assert safety["payload"]["stop_latched"] is False


def test_readonly_ws_requires_bearer(root: Path) -> None:
    with ReadOnlyGatewayServer(_readonly_app(root)) as server:
        sock, status, _ = _handshake(server.port)
        sock.close()
    assert status.startswith("HTTP/1.1 401"), status
