"""HTTP gateway for the M3 mock system."""

from __future__ import annotations

from collections import defaultdict, deque
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse
import uuid

from .errors import BusReadFailed, ErrorCode, MotionRejected
from .models import ContractVersion, MotionCommand
from .stack import MockStack


def _status_for(code: str) -> int:
    if code in {ErrorCode.UNAUTHORIZED.value}:
        return 401
    if code in {ErrorCode.FORBIDDEN.value, ErrorCode.NOT_LEASE_HOLDER.value}:
        return 403
    if code in {ErrorCode.NOT_FOUND.value}:
        return 404
    if code in {ErrorCode.RATE_LIMITED.value}:
        return 429
    if code in {
        ErrorCode.SAFETY_STOP_LATCHED.value,
        ErrorCode.SAFETY_HEARTBEAT_MISSING.value,
        ErrorCode.SAFETY_FAULT.value,
        ErrorCode.SYSTEM_DISARMED.value,
        ErrorCode.MOTION_BUSY.value,
        ErrorCode.CONFLICT.value,
        ErrorCode.NO_LEASE.value,
        ErrorCode.LEASE_EXPIRED.value,
        ErrorCode.LEASE_PREEMPTED.value,
        ErrorCode.LEASE_CONFLICT.value,
        ErrorCode.LEASE_REVOKED.value,
        ErrorCode.IDEMPOTENCY_CONFLICT.value,
        ErrorCode.VALIDATION_REQUIRED.value,
        ErrorCode.VALIDATION_EXPIRED.value,
        ErrorCode.VALIDATION_MISMATCH.value,
        ErrorCode.VALIDATION_REUSED.value,
        ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
    }:
        return 409
    if code in {
        ErrorCode.INVALID_COMMAND.value,
        ErrorCode.UNKNOWN_KIND.value,
        ErrorCode.UNKNOWN_PROFILE.value,
        ErrorCode.JOINT_LIMIT.value,
        ErrorCode.VELOCITY_LIMIT.value,
        ErrorCode.ACCELERATION_LIMIT.value,
        ErrorCode.STEP_LIMIT.value,
        ErrorCode.START_STATE.value,
        ErrorCode.DURATION_LIMIT.value,
        ErrorCode.WORKSPACE.value,
        ErrorCode.TABLE_CLEARANCE.value,
        ErrorCode.LOW_POSE_YAW.value,
        ErrorCode.CONFIG_INVALID.value,
    }:
        return 422
    if code in {
        ErrorCode.HARDWARE_UNAVAILABLE.value,
        ErrorCode.BUS_READ_FAILED.value,
        ErrorCode.PERCEPTION_UNAVAILABLE.value,
        ErrorCode.PERCEPTION_STALE.value,
    }:
        return 503
    return 400


class GatewayApp:
    def __init__(
        self,
        stack: MockStack,
        token: str,
        max_requests_per_second: int = 20,
    ) -> None:
        if not token:
            raise ValueError("gateway token is required")
        self.stack = stack
        self.token = token
        self.max_requests_per_second = max(1, int(max_requests_per_second))
        self._rates: dict[str, deque[float]] = defaultdict(deque)

    # ---- helpers ----

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
        token = value[7:] if value.lower().startswith("bearer ") else headers.get("x-armpi-token", "")
        return bool(token) and hmac.compare_digest(token, self.token)

    def _rate(self, headers: dict[str, str]) -> None:
        key = headers.get("authorization", "") or headers.get("x-armpi-token", "")
        now = time.monotonic()
        window = self._rates[key]
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self.max_requests_per_second:
            raise MotionRejected(ErrorCode.RATE_LIMITED, "gateway request rate limit exceeded")
        window.append(now)

    def _body(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        if len(body) > 256 * 1024:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "request body too large")
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "request body must be an object")
        return value

    def _contract(self, value: Any) -> ContractVersion:
        if not isinstance(value, dict):
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "contract is required")
        return ContractVersion.from_dict(value)

    # ---- routes ----

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
            return 200, {
                "status": "ok",
                "contract": self.stack.version.to_dict(),
                "safety": self.stack.safety.current_state().to_dict(self.stack.clock),
                "capabilities": ["mock"],
            }
        if not self._auth(headers):
            return 401, self._error(ErrorCode.UNAUTHORIZED.value, "missing or invalid token")
        try:
            self._rate(headers)
            return self._route(method, path, query, headers, body)
        except MotionRejected as exc:
            return _status_for(exc.code), self._error(exc.code, exc.message, {"violations": exc.violations})
        except BusReadFailed as exc:
            return 503, self._error(ErrorCode.BUS_READ_FAILED.value, str(exc))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return 422, self._error(ErrorCode.INVALID_COMMAND.value, str(exc))
        except Exception as exc:  # noqa: BLE001 - gateway boundary
            return 500, self._error(ErrorCode.INTERNAL_ERROR.value, str(exc))

    def _route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        stack = self.stack
        now = stack.clock.now()
        if method == "GET" and path == "/v1/contract":
            return 200, {
                "contract": stack.version.to_dict(),
                "profile_hash": stack.profile.profile_hash,
                "system_version": "0.1.0",
            }
        if method == "GET" and path == "/v1/state":
            return 200, stack.hardware.state(now).to_dict(stack.clock)
        if method == "GET" and path == "/v1/safety":
            return 200, stack.safety.current_state().to_dict(stack.clock)
        if method == "POST" and path == "/v1/safety/arm":
            payload = self._body(body)
            state = stack.safety.arm(
                operator_id=str(payload["operator_id"]),
                checklist_hash=str(payload["checklist_hash"]),
                confirm_phrase=str(payload["confirm_phrase"]),
                contract=self._contract(payload["contract"]),
                profile_hash=str(payload.get("profile_hash", "")),
                now=now,
            )
            return 200, {"armed": True, "safety": state.to_dict(stack.clock)}
        if method == "POST" and path == "/v1/safety/disarm":
            payload = self._body(body)
            state = stack.safety.disarm(str(payload["operator_id"]), str(payload.get("reason", "")))
            return 200, {"disarmed": True, "safety": state.to_dict(stack.clock)}
        if method == "POST" and path == "/v1/safety/reset":
            payload = self._body(body)
            state = stack.safety.reset(
                operator_id=str(payload["operator_id"]),
                reason=str(payload.get("reason", "")),
                confirm_phrase=str(payload["confirm_phrase"]),
                contract=self._contract(payload["contract"]),
                now=now,
                bus_ok=not stack.hardware.bus_failed,
            )
            return 200, {"accepted": True, "safety": state.to_dict(stack.clock)}
        if method == "POST" and path == "/v1/leases":
            payload = self._body(body)
            lease = stack.authority.acquire(
                holder=str(payload["holder"]),
                scope=str(payload["scope"]),
                mode=str(payload["mode"]),
                priority=int(payload["priority"]),
                ttl_ms=int(payload["ttl_ms"]),
                deadman_required=bool(payload.get("deadman_required", False)),
                contract=self._contract(payload["contract"]),
                now=now,
            )
            return 200, lease.to_dict(stack.clock)
        if path.startswith("/v1/leases/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                lease_id = parts[2]
                if method == "DELETE" and len(parts) == 3:
                    return 200, {"released": stack.authority.release(lease_id)}
                if method == "POST" and len(parts) == 4 and parts[3] == "renew":
                    payload = self._body(body)
                    lease = stack.authority.renew(
                        lease_id,
                        int(payload["ttl_ms"]),
                        self._contract(payload["contract"]),
                        now,
                    )
                    return 200, lease.to_dict(stack.clock)
                if method == "POST" and len(parts) == 4 and parts[3] == "revoke":
                    payload = self._body(body)
                    revoked = stack.authority.revoke(
                        lease_id,
                        str(payload["operator_id"]),
                        str(payload.get("reason", "")),
                        self._contract(payload["contract"]),
                    )
                    return 200, {"revoked": revoked}
        if method == "POST" and path == "/v1/motion/validate":
            command = MotionCommand.from_dict(self._body(body))
            report = stack.arbiter.validate(command, now)
            validation_id = f"val-{uuid.uuid4().hex[:12]}"
            return 200, {
                "approved": report.approved,
                "reason_code": report.reason_code,
                "violations": report.violations,
                "report": report.to_dict(stack.clock),
                "validation_id": validation_id,
                "expires_at": stack.clock.iso(now + 30.0),
            }
        if method == "POST" and path == "/v1/motion/execute":
            payload = self._body(body)
            header_key = headers.get("idempotency-key", "")
            body_key = str(payload.get("idempotency_key", ""))
            if not header_key and not body_key:
                raise MotionRejected(ErrorCode.INVALID_COMMAND, "Idempotency-Key is required")
            if header_key and body_key and header_key != body_key:
                raise MotionRejected(ErrorCode.INVALID_COMMAND, "Idempotency-Key does not match body")
            payload["idempotency_key"] = header_key or body_key
            command = MotionCommand.from_dict(payload)
            result = stack.arbiter.execute(command, now)
            return 200, result.to_dict(stack.clock)
        if method == "POST" and path == "/v1/motion/stop":
            payload = self._body(body)
            result = stack.arbiter.stop(
                str(payload["operator_id"]),
                str(payload.get("reason", "")),
                now,
            )
            return 200, result.to_dict(stack.clock)
        if method == "GET" and path == "/v1/capabilities":
            return 200, {
                "capabilities": [
                    {
                        "schema_version": "1",
                        "name": "jcheng_mock_operator",
                        "version": "0.1.0",
                        "contract": stack.version.to_dict(),
                        "scopes": ["full"],
                        "hardware_access": False,
                        "publishes": [],
                        "subscribes": [],
                        "services": [],
                        "max_rate_hz": 20,
                        "deadman_ms": 300,
                    }
                ],
                "supported_contracts": [stack.version.to_dict()],
            }
        if method == "GET" and path == "/v1/events":
            after = int(query.get("after", ["0"])[0])
            limit = int(query.get("limit", ["100"])[0])
            return 200, stack.evidence.page(after=after, limit=limit)
        return 404, self._error(ErrorCode.NOT_FOUND.value, f"no route for {method} {path}")


class _Handler(BaseHTTPRequestHandler):
    server_version = "JCHENG-Mock-Gateway/0.1"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 256 * 1024:
            self._send(413, {"error": {"code": "INVALID_COMMAND", "message": "body too large"}})
            return
        body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, payload = self.server.app.handle(self.command, self.path, headers, body)  # type: ignore[attr-defined]
        self._send(status, payload)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


class GatewayServer:
    def __init__(self, app: GatewayApp, host: str = "127.0.0.1", port: int = 0) -> None:
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

    def __enter__(self) -> "GatewayServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="JCHENG M3 mock gateway")
    parser.add_argument("--root", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token", default=os.environ.get("JCHENG_GATEWAY_TOKEN", ""))
    parser.add_argument("--evidence", default=None)
    args = parser.parse_args()
    if not args.token:
        print("ERROR: --token or JCHENG_GATEWAY_TOKEN is required", flush=True)
        return 2
    from .config import find_repo_root

    root = Path(args.root) if args.root else find_repo_root()
    stack = MockStack.build(root, evidence_path=args.evidence)
    app = GatewayApp(stack, args.token)
    server = GatewayServer(app, host=args.host, port=args.port)
    server.start()
    print(f"mock gateway listening on http://{args.host}:{server.port}", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
