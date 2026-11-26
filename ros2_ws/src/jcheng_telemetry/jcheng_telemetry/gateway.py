"""Read-only HTTP gateway.

Only GET routes exist. The gateway has no motion, lease, arm, reset, or
command handler; a POST to any path is rejected before routing.
"""

from __future__ import annotations

from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from threading import Thread
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid

from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.ws import (
    WS_STATE_PATH,
    WebSocketConnection,
    serve_state_stream,
)

from .core import TelemetryCache, safety_state_dict
from .evidence import ReadOnlyEvidence


class ReadOnlyGatewayApp:
    def __init__(
        self,
        cache: TelemetryCache,
        contract: dict[str, Any],
        profile_hash: str,
        config_hash: str,
        domain_id: int,
        evidence: ReadOnlyEvidence,
        token: str,
        max_requests_per_second: int = 20,
        system_version: str = "0.2.0-dev",
    ) -> None:
        if not token:
            raise ValueError("gateway token is required")
        self.cache = cache
        self.contract = dict(contract)
        self.profile_hash = profile_hash
        self.config_hash = config_hash
        self.domain_id = int(domain_id)
        self.evidence = evidence
        self.token = token
        self.max_requests_per_second = max(1, int(max_requests_per_second))
        self.system_version = system_version
        self._rates: dict[str, deque[float]] = defaultdict(deque)

    def _error(self, code: str, message: str, details: Any = None) -> dict[str, Any]:
        return {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": f"req-{uuid.uuid4().hex[:10]}",
            }
        }

    def _auth(self, headers: dict[str, str]) -> bool:
        value = headers.get("authorization", "")
        token = value[7:] if value.lower().startswith("bearer ") else headers.get("x-jcheng-token", "")
        return bool(token) and hmac.compare_digest(token, self.token)

    def _rate(self, headers: dict[str, str]) -> None:
        key = headers.get("authorization", "") or headers.get("x-jcheng-token", "")
        now = time.monotonic()
        window = self._rates[key]
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self.max_requests_per_second:
            raise PermissionError("rate limited")
        window.append(now)

    def _safety(self) -> dict[str, Any]:
        view = self.cache.safety_view(self.config_hash)
        return {
            "source": "jcheng_telemetry",
            "authoritative": False,
            "safety": safety_state_dict(view, self.cache.clock),
        }

    # ---- read-only WebSocket stream ----

    def ws_hello(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "profile_hash": self.profile_hash,
            "config_hash": self.config_hash,
            "system_version": self.system_version,
            "mode": "readonly",
            "read_only": True,
        }

    def ws_state(self) -> dict[str, Any]:
        return self.cache.snapshot().to_dict(self.cache.clock)

    def ws_safety(self) -> dict[str, Any]:
        return safety_state_dict(
            self.cache.safety_view(self.config_hash), self.cache.clock
        )

    def handle(
        self,
        method: str,
        raw_path: str,
        headers: dict[str, str],
        body: bytes = b"",
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/v1/health":
            health = self.cache.health()
            safety = self._safety()
            return 200, {
                "status": "ok" if health["measured_fresh"] and health["battery_fresh"] else "degraded",
                "mode": "readonly",
                "writes": 0,
                "domain_id": self.domain_id,
                "contract": self.contract,
                "profile_hash": self.profile_hash,
                "state": health,
                "safety": safety["safety"],
            }
        if not self._auth(headers):
            return 401, self._error(ErrorCode.UNAUTHORIZED.value, "missing or invalid token")
        if method != "GET":
            return 403, self._error(
                ErrorCode.FORBIDDEN.value,
                "read-only gateway: all mutation methods are rejected",
                {"path": path, "method": method, "writes": 0},
            )
        try:
            self._rate(headers)
            return self._route(path, query)
        except PermissionError:
            return 429, self._error(ErrorCode.RATE_LIMITED.value, "gateway request rate limit exceeded")
        except (KeyError, TypeError, ValueError) as exc:
            return 422, self._error(ErrorCode.INVALID_COMMAND.value, str(exc))
        except Exception as exc:  # noqa: BLE001 - gateway boundary
            return 500, self._error(ErrorCode.INTERNAL_ERROR.value, str(exc))

    def _route(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        if path == "/v1/contract":
            return 200, {
                "contract": self.contract,
                "profile_hash": self.profile_hash,
                "config_hash": self.config_hash,
                "system_version": self.system_version,
                "mode": "readonly",
            }
        if path == "/v1/state":
            return 200, self.cache.snapshot().to_dict(self.cache.clock)
        if path == "/v1/battery":
            return 200, self.cache.battery()
        if path == "/v1/safety":
            return 200, self._safety()
        if path == "/v1/topics":
            return 200, {"topics": self.cache.health()["topics"], "writes": 0}
        if path == "/v1/cameras":
            topics = {
                name: item
                for name, item in self.cache.health()["topics"].items()
                if "image" in name or "camera" in name
            }
            return 200, {"cameras": topics, "mode": "metadata_only"}
        if path == "/v1/tf":
            topics = {
                name: item
                for name, item in self.cache.health()["topics"].items()
                if name in ("/tf", "/tf_static")
            }
            return 200, {"tf": topics, "mode": "metadata_only"}
        if path == "/v1/events":
            after = int(query.get("after", ["0"])[0])
            limit = int(query.get("limit", ["100"])[0])
            return 200, self.evidence.page(after=after, limit=limit)
        if path.startswith("/v1/motion") or path.startswith("/v1/leases") or path.startswith("/v1/safety/"):
            return 403, self._error(
                ErrorCode.FORBIDDEN.value,
                "read-only gateway has no motion, lease, arm, or reset route",
                {"path": path, "writes": 0},
            )
        return 404, self._error(ErrorCode.NOT_FOUND.value, f"no read-only route for {path}")


class _Handler(BaseHTTPRequestHandler):
    server_version = "JCHENG-ReadOnly-Gateway/0.2"
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        if self.command == "GET" and urlparse(self.path).path == WS_STATE_PATH:
            self._handle_ws()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 256 * 1024:
            self._send(413, {"error": {"code": "INVALID_COMMAND", "message": "body too large"}})
            return
        body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, payload = self.server.app.handle(self.command, self.path, headers, body)  # type: ignore[attr-defined]
        self._send(status, payload)

    def _handle_ws(self) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        headers = {key.lower(): value for key, value in self.headers.items()}
        if not app._auth(headers):
            self._send(401, app._error("UNAUTHORIZED", "missing or invalid token"))
            return
        connection = WebSocketConnection.accept(self)
        if connection is None:
            return
        serve_state_stream(connection, app)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


class ReadOnlyGatewayServer:
    def __init__(self, app: ReadOnlyGatewayApp, host: str = "127.0.0.1", port: int = 0) -> None:
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.app = app  # type: ignore[attr-defined]
        self._thread: Thread | None = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> "ReadOnlyGatewayServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
