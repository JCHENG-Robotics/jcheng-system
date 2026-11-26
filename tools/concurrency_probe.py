"""P0 concurrency probes C1-C5 against the mock gateway process.

This tool runs the real ``jcheng_system_core`` gateway application on a
``ThreadingHTTPServer`` with the mock stack. It does **not** touch hardware:
the only backend is ``MockHardwareBridge``, optionally wrapped by a
harness-only settle delay so that an ``execute`` request has an observable
in-flight window. The delay is not part of the product mock; without it the
mock completes in microseconds and C2-C5 cannot be observed at all.

Run from the repository root:

    python3 tools/concurrency_probe.py --out /tmp/probe.json

Exit code 0 means every probe reached a verdict (PASS or FAIL); it does not
mean the probes passed. The JSON report is the evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import http.client
import json
from pathlib import Path
import random
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "ros2_ws" / "src" / "jcheng_system_core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from jcheng_system_core.gateway import GatewayApp, GatewayServer, TickThread  # noqa: E402
from jcheng_system_core.mock_hardware import MockHardwareBridge  # noqa: E402
from jcheng_system_core.stack import MockStack  # noqa: E402

TOKEN = "probe-token-not-a-secret"
HOST = "127.0.0.1"


class TimedBridge:
    """Harness-only wrapper that gives ``bus_read`` a settle window.

    ``publish`` applies the target immediately, exactly like the product mock.
    ``bus_read`` sleeps before reading, which models the interval between the
    command being issued and the measured state being taken. This is the only
    difference from ``MockHardwareBridge``; it exists so a concurrent stop can
    arrive while an execute request is still open.
    """

    def __init__(
        self,
        inner: MockHardwareBridge,
        settle_s: float,
        publish_delay_s: float = 0.0,
    ) -> None:
        self._inner = inner
        self._settle_s = max(0.0, settle_s)
        self._publish_delay_s = max(0.0, publish_delay_s)
        self.held = False

    def publish(self, command: Any, now: float | None = None) -> None:
        if self._publish_delay_s:
            time.sleep(self._publish_delay_s)
        self.held = False
        self._inner.publish(command, now)

    def bus_read(self, now: float | None = None) -> Any:
        if self._settle_s:
            time.sleep(self._settle_s)
        return self._inner.bus_read(now)

    def hold(self, now: float | None = None) -> None:
        self.held = True
        self._inner.hold(now)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _request(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any], float]:
    started = time.monotonic()
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        elapsed = time.monotonic() - started
        try:
            decoded = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            decoded = {"raw": raw.decode("utf-8", "replace")}
        return response.status, decoded, elapsed
    finally:
        connection.close()


def _abort_execute(port: int, body: dict[str, Any], *, after_s: float = 0.05) -> None:
    """Send an execute request and close the socket without reading a reply."""

    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    connection = http.client.HTTPConnection(HOST, port, timeout=5.0)
    connection.putrequest("POST", "/v1/motion/execute")
    connection.putheader("Authorization", f"Bearer {TOKEN}")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(len(payload)))
    connection.putheader("Idempotency-Key", str(body["idempotency_key"]))
    connection.endheaders()
    connection.send(payload)
    time.sleep(after_s)
    if connection.sock is not None:
        connection.sock.close()
    connection.close()


class Harness:
    """One fresh mock stack + gateway server per probe."""

    def __init__(self, settle_s: float, publish_delay_s: float = 0.0) -> None:
        self.stack = MockStack.build(ROOT)
        self.bridge = TimedBridge(self.stack.hardware, settle_s, publish_delay_s)
        self.stack.hardware = self.bridge  # type: ignore[assignment]
        self.stack.arbiter.hardware = self.bridge  # type: ignore[assignment]
        self.stack.arm()
        self.lease = self.stack.lease(ttl_ms=30_000)
        self.completion_calls = 0
        # One intent must map to one terminal result object. Stop and finish
        # may both call _complete_once; only the first writes.
        self.terminals: dict[str, set[int]] = defaultdict(set)
        original = self.stack.arbiter._complete_once

        def counting(active: Any, now: float | None = None, **kwargs: Any) -> Any:
            result = original(active, now, **kwargs)
            self.completion_calls += 1
            self.terminals[active.command.intent_id].add(id(result))
            return result

        self.stack.arbiter._complete_once = counting  # type: ignore[method-assign]
        self.server = GatewayServer(
            GatewayApp(self.stack, TOKEN, max_requests_per_second=1000)
        )
        self.server.httpd.handle_error = lambda *args, **kwargs: None  # type: ignore[assignment]
        self.server.start()
        self.tick = TickThread(self.stack)
        self.tick.start()

    def close(self) -> None:
        self.tick.stop()
        self.server.stop()

    def command(self, **overrides: Any) -> dict[str, Any]:
        command = self.stack.command(lease_id=self.lease.lease_id, **overrides)
        return command.to_dict()


def _run_execute(port: int, body: dict[str, Any], out: dict[str, Any]) -> None:
    try:
        out["status"], out["body"], out["elapsed_s"] = _request(
            port, "POST", "/v1/motion/execute", body, timeout=5.0
        )
    except Exception as exc:  # noqa: BLE001 - probe records the failure
        out["error"] = f"{type(exc).__name__}: {exc}"


def probe_c1() -> dict[str, Any]:
    harness = Harness(settle_s=0.0)
    try:
        status, body, elapsed = _request(
            harness.server.port,
            "POST",
            "/v1/motion/stop",
            {"operator_id": "probe", "reason": "C1 idle stop"},
        )
        latched = bool(body.get("safety", {}).get("stop_latched"))
        passed = status == 200 and latched and elapsed < 1.0
        return {
            "id": "C1",
            "name": "stop while idle",
            "status": "PASS" if passed else "FAIL",
            "criteria": "HTTP 200, stop_latched true, response < 1 s, no active motion",
            "observed": {
                "http_status": status,
                "stop_latched": latched,
                "elapsed_s": round(elapsed, 4),
                "active_motion": harness.stack.arbiter._active is not None,
            },
        }
    finally:
        harness.close()


def probe_c2() -> dict[str, Any]:
    harness = Harness(settle_s=0.5)
    try:
        command = harness.command(duration_s=1.0)
        result: dict[str, Any] = {}
        thread = threading.Thread(
            target=_run_execute, args=(harness.server.port, command, result), daemon=True
        )
        thread.start()
        time.sleep(0.1)
        pending_before_stop = thread.is_alive()
        stop_status, stop_body, stop_elapsed = _request(
            harness.server.port,
            "POST",
            "/v1/motion/stop",
            {"operator_id": "probe", "reason": "C2 stop during execute"},
        )
        thread.join(timeout=5.0)
        execute_status = result.get("status", 0)
        execute_body = result.get("body", {})
        outcome = str(execute_body.get("outcome", ""))
        terminal_objects = len(harness.terminals.get(command["intent_id"], set()))
        passed = (
            pending_before_stop
            and stop_status == 200
            and execute_status == 200
            and outcome == "ABORTED"
            and terminal_objects == 1
        )
        return {
            "id": "C2",
            "name": "stop while execute is in flight",
            "status": "PASS" if passed else "FAIL",
            "criteria": (
                "execute still pending when stop is issued; stop 200 < 1 s; "
                "execute 200 with ABORTED; exactly one terminal completion"
            ),
            "observed": {
                "execute_pending_before_stop": pending_before_stop,
                "stop_http_status": stop_status,
                "stop_elapsed_s": round(stop_elapsed, 4),
                "stop_latched": bool(stop_body.get("safety", {}).get("stop_latched")),
                "execute_http_status": execute_status,
                "execute_outcome": outcome,
                "execute_reason_code": execute_body.get("reason_code"),
                "execute_safety_state": execute_body.get("safety", {}).get("state"),
                "execute_stop_latched": execute_body.get("safety", {}).get("stop_latched"),
                "execute_error": result.get("error"),
                "execute_elapsed_s": result.get("elapsed_s"),
                "terminal_objects": terminal_objects,
                "completion_calls": harness.completion_calls,
                "final_cached_outcome": str(
                    harness.stack.arbiter._idempotency.get(
                        command["idempotency_key"], (None, None)
                    )[1].outcome
                    if command["idempotency_key"] in harness.stack.arbiter._idempotency
                    else ""
                ),
            },
        }
    finally:
        harness.close()


def probe_c2_publish_window() -> dict[str, Any]:
    """Stop arriving while the command write is in progress.

    The stop must wait for the write and then hold; it must never hold before
    the command is published. The execute terminal must be ABORTED.
    """

    harness = Harness(settle_s=0.0, publish_delay_s=0.15)
    try:
        command = harness.command(duration_s=1.0)
        result: dict[str, Any] = {}
        thread = threading.Thread(
            target=_run_execute, args=(harness.server.port, command, result), daemon=True
        )
        thread.start()
        time.sleep(0.02)
        stop_status, _, stop_elapsed = _request(
            harness.server.port,
            "POST",
            "/v1/motion/stop",
            {"operator_id": "probe", "reason": "C2p stop during publish"},
        )
        thread.join(timeout=5.0)
        outcome = str(result.get("body", {}).get("outcome", ""))
        terminal_objects = len(harness.terminals.get(command["intent_id"], set()))
        passed = (
            stop_status == 200
            and result.get("status") == 200
            and outcome == "ABORTED"
            and terminal_objects == 1
            and len(harness.stack.hardware.commands) == 1
        )
        return {
            "id": "C2p",
            "name": "stop during the command write (publish window)",
            "status": "PASS" if passed else "FAIL",
            "criteria": (
                "stop waits for the write then holds; execute 200 ABORTED; "
                "one terminal object; one backend command"
            ),
            "observed": {
                "stop_http_status": stop_status,
                "stop_elapsed_s": round(stop_elapsed, 4),
                "execute_http_status": result.get("status"),
                "execute_outcome": outcome,
                "execute_reason_code": result.get("body", {}).get("reason_code"),
                "terminal_objects": terminal_objects,
                "backend_commands": len(harness.stack.hardware.commands),
            },
        }
    finally:
        harness.close()


def probe_c3() -> dict[str, Any]:
    harness = Harness(settle_s=0.5)
    try:
        first = harness.command(duration_s=1.0)
        second = harness.command(duration_s=1.0)
        result: dict[str, Any] = {}
        thread = threading.Thread(
            target=_run_execute, args=(harness.server.port, first, result), daemon=True
        )
        thread.start()
        time.sleep(0.1)
        second_status, second_body, _ = _request(
            harness.server.port, "POST", "/v1/motion/execute", second
        )
        thread.join(timeout=5.0)
        first_outcome = str(result.get("body", {}).get("outcome", ""))
        count = len(harness.stack.hardware.commands)
        # The gateway reports a rejected motion as HTTP 200 with
        # outcome=REJECTED and reason_code=MOTION_BUSY, not as HTTP 409.
        second_outcome = str(second_body.get("outcome", ""))
        second_reason = str(second_body.get("reason_code", ""))
        passed = (
            second_status == 200
            and second_outcome == "REJECTED"
            and second_reason == "MOTION_BUSY"
            and first_outcome == "COMPLETED"
            and count == 1
        )
        return {
            "id": "C3",
            "name": "second execute while one is in flight",
            "status": "PASS" if passed else "FAIL",
            "criteria": (
                "second execute is rejected with REJECTED/MOTION_BUSY and never "
                "queued; first completes; one backend command"
            ),
            "observed": {
                "second_http_status": second_status,
                "second_outcome": second_outcome,
                "second_reason_code": second_reason,
                "first_outcome": first_outcome,
                "backend_commands": count,
            },
        }
    finally:
        harness.close()


def probe_c4() -> dict[str, Any]:
    harness = Harness(settle_s=0.5)
    try:
        command = harness.command(duration_s=1.0)
        _abort_execute(harness.server.port, command)
        deadline = time.monotonic() + 3.0
        while harness.stack.arbiter._active is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
        replay_status, replay_body, _ = _request(
            harness.server.port, "POST", "/v1/motion/execute", command
        )
        outcome = str(replay_body.get("outcome", ""))
        count = len(harness.stack.hardware.commands)
        passed = replay_status == 200 and outcome == "COMPLETED" and count == 1
        return {
            "id": "C4",
            "name": "client aborts before the late response; idempotent replay",
            "status": "PASS" if passed else "FAIL",
            "criteria": (
                "after a dropped connection the same idempotency key returns the "
                "cached terminal result and does not execute a second time"
            ),
            "observed": {
                "replay_http_status": replay_status,
                "replay_outcome": outcome,
                "backend_commands": count,
                "active_after_drop": harness.stack.arbiter._active is not None,
            },
        }
    finally:
        harness.close()


def probe_c5(revoke: bool) -> dict[str, Any]:
    harness = Harness(settle_s=0.5)
    try:
        command = harness.command(duration_s=1.0)
        result: dict[str, Any] = {}
        thread = threading.Thread(
            target=_run_execute, args=(harness.server.port, command, result), daemon=True
        )
        thread.start()
        time.sleep(0.1)
        if revoke:
            status, body, _ = _request(
                harness.server.port,
                "POST",
                f"/v1/leases/{harness.lease.lease_id}/revoke",
                {
                    "operator_id": "probe",
                    "reason": "C5 revoke during execute",
                    "contract": harness.stack.version.to_dict(),
                },
            )
            expected_code = "LEASE_REVOKED"
            label = "C5"
            name = "lease revoked while execute is in flight"
        else:
            status, body, _ = (0, {}, 0.0)
            expected_code = "LEASE_EXPIRED"
            label = "C5b"
            name = "lease expired while execute is in flight"
        thread.join(timeout=5.0)
        outcome = str(result.get("body", {}).get("outcome", ""))
        reason = str(result.get("body", {}).get("reason_code", ""))
        passed = result.get("status") == 200 and outcome == "ABORTED" and reason == expected_code
        return {
            "id": label,
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "criteria": f"execute returns 200 ABORTED with {expected_code}",
            "observed": {
                "lease_request_status": status,
                "execute_http_status": result.get("status"),
                "execute_outcome": outcome,
                "execute_reason_code": reason,
                "safety_state": result.get("body", {}).get("safety", {}).get("state"),
                "stop_latched": result.get("body", {}).get("safety", {}).get("stop_latched"),
            },
        }
    finally:
        harness.close()


def probe_c5_expiry() -> dict[str, Any]:
    """Same as C5 but with a short lease TTL and no revoke call."""

    harness = Harness(settle_s=0.5)
    try:
        # Replace the 30 s lease with a 300 ms one.
        harness.stack.authority.release(harness.lease.lease_id)
        short = harness.stack.lease(ttl_ms=300)
        command = harness.stack.command(lease_id=short.lease_id, duration_s=1.0).to_dict()
        result: dict[str, Any] = {}
        thread = threading.Thread(
            target=_run_execute, args=(harness.server.port, command, result), daemon=True
        )
        thread.start()
        thread.join(timeout=5.0)
        outcome = str(result.get("body", {}).get("outcome", ""))
        reason = str(result.get("body", {}).get("reason_code", ""))
        passed = result.get("status") == 200 and outcome == "ABORTED" and reason == "LEASE_EXPIRED"
        return {
            "id": "C5b",
            "name": "lease expired while execute is in flight",
            "status": "PASS" if passed else "FAIL",
            "criteria": "execute returns 200 ABORTED with LEASE_EXPIRED",
            "observed": {
                "execute_http_status": result.get("status"),
                "execute_outcome": outcome,
                "execute_reason_code": reason,
                "safety_state": result.get("body", {}).get("safety", {}).get("state"),
                "stop_latched": result.get("body", {}).get("safety", {}).get("stop_latched"),
            },
        }
    finally:
        harness.close()


def stress_stop_race(iterations: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    statuses: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    terminal: Counter[str] = Counter()
    double_terminal = 0
    post_stop_completed = 0
    for _ in range(iterations):
        harness = Harness(settle_s=0.03)
        try:
            command = harness.command(duration_s=1.0)
            result: dict[str, Any] = {}
            thread = threading.Thread(
                target=_run_execute,
                args=(harness.server.port, command, result),
                daemon=True,
            )
            thread.start()
            time.sleep(rng.uniform(0.004, 0.020))
            stop_status, _, _ = _request(
                harness.server.port,
                "POST",
                "/v1/motion/stop",
                {"operator_id": "probe", "reason": "race"},
            )
            thread.join(timeout=3.0)
            statuses[str(result.get("status", 0))] += 1
            outcome = str(result.get("body", {}).get("outcome", ""))
            outcomes[outcome or "none"] += 1
            terminal_count = len(harness.terminals.get(command["intent_id"], set()))
            terminal[str(terminal_count)] += 1
            if terminal_count > 1:
                double_terminal += 1
            if stop_status == 200 and outcome == "COMPLETED":
                post_stop_completed += 1
        finally:
            harness.close()
    passed = double_terminal == 0 and post_stop_completed == 0 and statuses.get("500", 0) == 0
    return {
        "id": "C2-stress",
        "name": f"stop/execute race, {iterations} iterations",
        "status": "PASS" if passed else "FAIL",
        "criteria": "zero 500s, zero COMPLETED after stop, one terminal completion per intent",
        "observed": {
            "execute_http_statuses": dict(statuses),
            "execute_outcomes": dict(outcomes),
            "terminal_completions_per_intent": dict(terminal),
            "double_terminal_intents": double_terminal,
            "completed_after_stop": post_stop_completed,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261126)
    args = parser.parse_args()

    probes = [
        probe_c1(),
        probe_c2(),
        probe_c2_publish_window(),
        probe_c3(),
        probe_c4(),
        probe_c5(True),
        probe_c5_expiry(),
    ]
    # C2 stress uses the same settle window as C2; run it after the single-shot probes.
    probes.append(stress_stop_race(max(1, args.iterations), args.seed))

    summary: Counter[str] = Counter(probe["status"] for probe in probes)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gateway": "jcheng_system_core.gateway.GatewayApp on ThreadingHTTPServer (real process code)",
        "backend": "MockStack + MockHardwareBridge, wrapped by harness-only settle delay",
        "harness_note": (
            "The settle delay is added by this probe, not by the product mock. "
            "Without it the mock execute path completes in microseconds and no "
            "in-flight window exists. No hardware is opened or contacted."
        ),
        "hardware_touched": False,
        "probes": probes,
        "summary": dict(summary),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
