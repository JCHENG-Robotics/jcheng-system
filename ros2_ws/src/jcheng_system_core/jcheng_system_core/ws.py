"""Minimal RFC 6455 WebSocket support for the JCHENG HTTP gateways.

The gateways are stdlib ``ThreadingHTTPServer`` processes. Adding a separate
WebSocket library/server would mean a second listener and a second auth path,
so this module performs the upgrade in the existing handler and streams the
read-only ``state``/``safety`` envelopes defined by
``jcheng-interfaces/schemas/ws/jcheng-ws.v1.json``.

The stream is server-to-client text only. Client ping is answered with pong;
client close ends the stream; other client data is ignored. There is no
subscription to motion/lease events and no write path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import select
import struct
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any, Protocol

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_STATE_PATH = "/v1/ws/state"
MAX_FRAME_BYTES = 64 * 1024

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(RuntimeError):
    """A malformed or closed WebSocket connection."""


class StreamApp(Protocol):
    def ws_hello(self) -> dict[str, Any]: ...

    def ws_state(self) -> dict[str, Any]: ...

    def ws_safety(self) -> dict[str, Any]: ...


def iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the v1 WebSocket envelope."""
    return {
        "v": 1,
        "type": kind,
        "id": f"ws-{uuid.uuid4().hex[:12]}",
        "ts": iso_now(),
        "payload": payload,
    }


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _recv_exact(sock: Any, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise WebSocketError("peer closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)


class WebSocketConnection:
    """A server-side connection after a successful upgrade."""

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self.handler = handler
        self.sock = handler.connection

    @classmethod
    def accept(cls, handler: BaseHTTPRequestHandler) -> "WebSocketConnection | None":
        """Complete the handshake; return None after sending an HTTP error.

        The caller has already checked the path and authentication.
        """
        headers = {key.lower(): value for key, value in handler.headers.items()}
        upgrade = headers.get("upgrade", "").lower()
        key = headers.get("sec-websocket-key", "")
        version = headers.get("sec-websocket-version", "")
        if upgrade != "websocket":
            handler.send_response(426, "Upgrade Required")
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Upgrade", "websocket")
            handler.send_header("Sec-WebSocket-Version", "13")
            body = json.dumps(
                {
                    "error": {
                        "code": "UPGRADE_REQUIRED",
                        "message": "this path requires a WebSocket upgrade",
                    }
                }
            ).encode("utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            handler.close_connection = True
            return None
        if not key or version != "13":
            handler.send_response(400, "Bad Request")
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps(
                {
                    "error": {
                        "code": "INVALID_UPGRADE",
                        "message": "Sec-WebSocket-Key and version 13 are required",
                    }
                }
            ).encode("utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            handler.close_connection = True
            return None

        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept_key(key))
        handler.end_headers()
        handler.close_connection = True
        return cls(handler)

    # ---------------------------------------------------------------- sending
    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        self.sock.sendall(bytes(header) + payload)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def send_pong(self, payload: bytes) -> None:
        self._send_frame(OP_PONG, payload)

    def send_close(self, code: int = 1000, reason: str = "") -> None:
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode("utf-8"))
        except OSError:
            pass

    # ---------------------------------------------------------------- receiving
    def recv_frame(self, timeout: float = 0.0) -> tuple[bool, int, bytes] | None:
        """Return ``(fin, opcode, payload)`` or None when no frame is ready."""
        readable, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        if not readable:
            return None
        first, second = _recv_exact(self.sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(self.sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(self.sock, 8))[0]
        if length > MAX_FRAME_BYTES:
            raise WebSocketError("client frame is too large")
        mask = _recv_exact(self.sock, 4) if masked else b""
        payload = _recv_exact(self.sock, length) if length else b""
        if masked:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return fin, opcode, payload

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def serve_state_stream(
    connection: WebSocketConnection,
    app: StreamApp,
    *,
    interval_s: float = 0.1,
) -> None:
    """Send hello once, then state+safety at ``1/interval_s``; handle control frames."""
    try:
        connection.send_json(envelope("hello", app.ws_hello()))
        while True:
            connection.send_json(envelope("state", app.ws_state()))
            connection.send_json(envelope("safety", app.ws_safety()))
            frame = connection.recv_frame(timeout=max(0.01, interval_s))
            if frame is None:
                continue
            _fin, opcode, payload = frame
            if opcode == OP_CLOSE:
                connection.send_close()
                break
            if opcode == OP_PING:
                connection.send_pong(payload)
            # text/binary/pong from the client are ignored: this is a read-only
            # server-to-client stream.
    except (OSError, WebSocketError):
        pass
    finally:
        connection.close()
