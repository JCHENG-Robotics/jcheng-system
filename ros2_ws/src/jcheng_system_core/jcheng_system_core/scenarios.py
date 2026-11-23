"""S1-S17 mock acceptance scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

from .errors import ErrorCode, MotionRejected
from .gateway import GatewayApp, GatewayServer
from .models import MotionResult
from .stack import MockStack
from .timebase import ManualClock

REQUIRED = {f"S{index}" for index in range(1, 13)} | {"S14", "S15", "S16"}


@dataclass
class ScenarioResult:
    id: str
    name: str
    status: str
    detail: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence_ids": self.evidence_ids,
        }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _armed(root: Path) -> MockStack:
    stack = MockStack.build(root, clock=ManualClock())
    stack.arm()
    return stack


def _step(stack: MockStack, lease_id: str, **overrides):
    payload = {
        "kind": "JOINT_STEP",
        "profile": "step",
        "joint_names": ["joint1"],
        "target_positions": [0.05],
        "duration_s": 1.0,
    }
    payload.update(overrides)
    return stack.command(lease_id=lease_id, **payload)


def _pose(stack: MockStack, lease_id: str, x: float, y: float, z: float):
    return stack.command(
        lease_id=lease_id,
        kind="POSE",
        profile="pose",
        target_pose={
            "position": {"x": x, "y": y, "z": z},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
        tip_link="end_effector_link",
        frame_id="base_link",
        joint_names=[],
        target_positions=[],
        duration_s=1.0,
    )


def _base(stack: MockStack, lease_id: str):
    return stack.command(
        lease_id=lease_id,
        kind="BASE_PULSE",
        profile="base",
        base_pulse={"duty": [0.05, 0.0, 0.0, 0.0], "duration_s": 1.0, "require_stopped_after": True},
        joint_names=[],
        target_positions=[],
    )


def _gripper(stack: MockStack, lease_id: str):
    return stack.command(
        lease_id=lease_id,
        kind="GRIPPER",
        profile="gripper",
        gripper={"position_rad": 1.0, "max_velocity_rad_s": 0.5, "duration_s": 1.0, "verify_bus": True},
        joint_names=[],
        target_positions=[],
    )


# ---- scenarios ----


def s1_contract_and_step(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    report = stack.arbiter.validate(command)
    _assert(report.approved, f"expected approval, got {report.reason_code}")
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "COMPLETED", f"unexpected outcome {result.outcome}")
    _assert(result.bus_verified, "step completion must be bus verified")
    _assert(stack.hardware.publishers == {"jcheng_hardware"}, "unexpected publisher")
    return ScenarioResult("S1", "contract handshake and step", "pass", "approved and bus-verified", [result.evidence_id])


def s2_contract_mismatch(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(
        stack,
        lease.lease_id,
        contract={
            "major": 0,
            "minor": 1,
            "patch": 0,
            "contract_hash": "sha256:" + "1" * 64,
        },
    )
    report = stack.arbiter.validate(command)
    _assert(not report.approved, "mismatched contract must be rejected")
    _assert(report.reason_code == ErrorCode.CONTRACT_HASH_MISMATCH.value, report.reason_code)
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "REJECTED", result.outcome)
    return ScenarioResult("S2", "contract mismatch", "pass", "hash mismatch rejected before motion")


def s3_lease_lifecycle(root: Path) -> ScenarioResult:
    stack = MockStack.build(root, clock=ManualClock())
    lease = stack.lease(holder="alice", scope="arm", priority=60, ttl_ms=1000)
    renewed = stack.authority.renew(lease.lease_id, 2000, stack.version)
    _assert(renewed.expires_at > lease.expires_at, "renew did not extend the lease")
    _assert(stack.authority.check(lease.lease_id, "arm", stack.clock.now()).ok, "lease should be valid")
    _assert(stack.authority.release(lease.lease_id), "release failed")
    check = stack.authority.check(lease.lease_id, "arm", stack.clock.now())
    _assert(not check.ok, "released lease must not be valid")
    events = {item["event"] for item in stack.authority.events}
    _assert({"lease_acquired", "lease_renewed", "lease_released"} <= events, str(events))
    return ScenarioResult("S3", "lease lifecycle", "pass", "acquire, renew, release audited")


def s4_lease_contention(root: Path) -> ScenarioResult:
    stack = MockStack.build(root, clock=ManualClock())
    low = stack.lease(holder="low", scope="arm", priority=40, ttl_ms=5000)
    _assert(stack.authority.check(low.lease_id, "arm", stack.clock.now()).ok, "low lease should start valid")
    high = stack.lease(holder="high", scope="arm", priority=60, ttl_ms=5000)
    _assert(not stack.authority.check(low.lease_id, "arm", stack.clock.now()).ok, "low lease should be preempted")
    _assert(stack.authority.check(high.lease_id, "arm", stack.clock.now()).ok, "high lease should be valid")
    events = {item["event"] for item in stack.authority.events}
    _assert("lease_preempted" in events, str(events))
    return ScenarioResult("S4", "lease contention and preemption", "pass", "higher priority preempted lower")


def s5_lease_expiry_in_flight(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease(holder="short", scope="arm", priority=60, ttl_ms=100)
    command = _step(stack, lease.lease_id)
    _assert(stack.arbiter.start(command) is None, "motion should start")
    stack.clock.advance(0.2)
    results = stack.arbiter.tick()
    _assert(results, "expiry should abort the active motion")
    _assert(results[0].outcome == "ABORTED", results[0].outcome)
    _assert(results[0].reason_code == ErrorCode.LEASE_EXPIRED.value, results[0].reason_code)
    _assert(stack.safety.stop_latched, "expiry must latch a stop")
    return ScenarioResult("S5", "lease expiry in flight", "pass", "active motion aborted and latched", [results[0].evidence_id])


def s6_safety_stop_and_reset(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    stop = stack.arbiter.stop("operator", "test stop")
    _assert(stop.accepted, "stop not accepted")
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "REJECTED", result.outcome)
    _assert(result.reason_code == ErrorCode.SAFETY_STOP_LATCHED.value, result.reason_code)
    stack.safety.reset(
        "operator",
        "test reset",
        stack.safety.limits.reset_confirm_phrase,
        stack.version,
        bus_ok=True,
    )
    _assert(not stack.safety.stop_latched, "reset did not clear the latch")
    stack.arm()
    _assert(stack.safety.state == "ARMED_IDLE", stack.safety.state)
    return ScenarioResult("S6", "safety stop and reset", "pass", "stop latched, reset required re-arm")


def s7_stale_state(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    now = stack.clock.now()
    stack.safety.update_state(stack.hardware.state(now), now - 1.0)
    report = stack.arbiter.validate(command)
    _assert(not report.approved, "stale state must be rejected")
    _assert(report.reason_code == ErrorCode.STATE_STALE.value, report.reason_code)
    _assert(stack.safety.stop_latched, "stale state must latch")
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "REJECTED", result.outcome)
    return ScenarioResult("S7", "stale state", "pass", "stale measured state rejected and latched")


def s8_limits_workspace_table(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()

    joint = _step(stack, lease.lease_id, joint_names=["joint2"], target_positions=[2.0])
    joint_report = stack.arbiter.validate(joint)
    _assert(joint_report.reason_code == ErrorCode.JOINT_LIMIT.value, joint_report.reason_code)

    workspace = _pose(stack, lease.lease_id, 1.0, 0.0, 0.2)
    workspace_report = stack.arbiter.validate(workspace)
    _assert(workspace_report.reason_code == ErrorCode.WORKSPACE.value, workspace_report.reason_code)

    table = _pose(stack, lease.lease_id, 0.2, 0.0, -0.09)
    table_report = stack.arbiter.validate(table)
    _assert(table_report.reason_code == ErrorCode.TABLE_CLEARANCE.value, table_report.reason_code)

    return ScenarioResult("S8", "limits, workspace and table", "pass", "joint limit, workspace and table vetoes")


def s9_guard_violation(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    stack.hardware.add_publisher("rogue")
    report = stack.arbiter.validate(command)
    _assert(not report.approved, "guard violation must reject")
    _assert(ErrorCode.SAFETY_FAULT.value in stack.safety.violation_codes, str(stack.safety.violation_codes))
    _assert(stack.safety.stop_latched, "guard violation must latch")
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "REJECTED", result.outcome)
    stack.hardware.remove_publisher("rogue")
    return ScenarioResult("S9", "unauthorized publisher", "pass", "guard latched on rogue publisher")


def s10_no_lease(root: Path) -> ScenarioResult:
    stack = _armed(root)
    command = _step(stack, "lease-missing")
    report = stack.arbiter.validate(command)
    _assert(report.reason_code == ErrorCode.NO_LEASE.value, report.reason_code)
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "REJECTED", result.outcome)
    _assert(not stack.hardware.commands, "no-lease command must not reach hardware")
    _assert(stack.hardware.publishers == {"jcheng_hardware"}, "unexpected publisher")
    return ScenarioResult("S10", "no lease", "pass", "no lease rejected before hardware")


def s11_idempotency(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    command.idempotency_key = "idem-fixed"
    first = stack.arbiter.execute(command)
    second = stack.arbiter.execute(command)
    _assert(first.outcome == "COMPLETED", first.outcome)
    _assert(second.evidence_id == first.evidence_id, "idempotent replay returned a different evidence id")
    conflict = _step(stack, lease.lease_id, target_positions=[0.06])
    conflict.idempotency_key = "idem-fixed"
    result = stack.arbiter.execute(conflict)
    _assert(result.reason_code == ErrorCode.IDEMPOTENCY_CONFLICT.value, result.reason_code)
    return ScenarioResult("S11", "idempotency", "pass", "same key returns same result; payload conflict rejected", [first.evidence_id])


def s12_bus_failure_unknown(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    command = _step(stack, lease.lease_id)
    stack.hardware.set_bus_failure(True)
    result = stack.arbiter.execute(command)
    _assert(result.outcome == "UNKNOWN", result.outcome)
    _assert(stack.safety.stop_latched, "bus failure must latch")
    try:
        stack.safety.reset(
            "operator",
            "unknown reset attempt",
            stack.safety.limits.reset_confirm_phrase,
            stack.version,
            bus_ok=False,
        )
    except MotionRejected as exc:
        _assert(exc.code == ErrorCode.MOTION_OUTCOME_UNKNOWN.value, exc.code)
    else:
        raise AssertionError("reset without a bus check should fail")
    stack.hardware.set_bus_failure(False)
    stack.safety.update_state(stack.hardware.state(stack.clock.now()), stack.clock.now())
    stack.safety.reset(
        "operator",
        "bus verified",
        stack.safety.limits.reset_confirm_phrase,
        stack.version,
        bus_ok=True,
    )
    _assert(not stack.safety.stop_latched, "reset with bus check failed")
    return ScenarioResult("S12", "bus failure unknown", "pass", "unknown latched until bus check", [result.evidence_id])


def s13_replay(root: Path) -> ScenarioResult:
    return ScenarioResult("S13", "v1.0.0 trajectory replay", "skipped", "baseline replay is M6; not part of M3")


def s14_base_pulse(root: Path) -> ScenarioResult:
    stack = _armed(root)
    lease = stack.lease()
    result = stack.arbiter.execute(_base(stack, lease.lease_id))
    _assert(result.outcome == "COMPLETED", result.outcome)
    _assert(not result.bus_verified, "base pulse must be open loop")
    _assert(result.calibration_invalidated, "base pulse must invalidate calibration")
    _assert(stack.hardware.base_duty == [0.0, 0.0, 0.0, 0.0], "base duty must be zero after the pulse")
    return ScenarioResult("S14", "base pulse and calibration", "pass", "open-loop pulse completed and calibration invalidated", [result.evidence_id])


def _http(port: int, path: str, token: str | None = None, method: str = "GET") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def s15_gateway_auth_rate_reconnect(root: Path) -> ScenarioResult:
    stack = MockStack.build(root, clock=ManualClock())
    token = "m3-test-token"
    app = GatewayApp(stack, token, max_requests_per_second=3)
    server = GatewayServer(app)
    server.start()
    try:
        status, _ = _http(server.port, "/v1/state")
        _assert(status == 401, f"missing token should be 401, got {status}")
        status, _ = _http(server.port, "/v1/state", token)
        _assert(status == 200, f"valid token should be 200, got {status}")
        statuses = [_http(server.port, "/v1/state", token)[0] for _ in range(4)]
        _assert(429 in statuses, f"rate limit not hit: {statuses}")
    finally:
        server.stop()

    app2 = GatewayApp(stack, token, max_requests_per_second=10)
    server2 = GatewayServer(app2)
    server2.start()
    try:
        status, _ = _http(server2.port, "/v1/state", token)
        _assert(status == 200, f"reconnect should read state, got {status}")
    finally:
        server2.stop()
    return ScenarioResult("S15", "gateway auth, rate limit and reconnect", "pass", "401/429 enforced; reconnect read-only")


def s16_profile_mismatch(root: Path) -> ScenarioResult:
    stack = MockStack.build(root, clock=ManualClock())
    stack.sync_state()
    try:
        stack.safety.arm(
            operator_id="operator",
            checklist_hash="mock-checklist",
            confirm_phrase=stack.safety.limits.arm_confirm_phrase,
            contract=stack.version,
            profile_hash="sha256:" + "2" * 64,
        )
    except MotionRejected as exc:
        _assert(exc.code == ErrorCode.PROFILE_HASH_MISMATCH.value, exc.code)
    else:
        raise AssertionError("profile mismatch should reject arming")
    _assert(stack.safety.state == "DISARMED", stack.safety.state)
    return ScenarioResult("S16", "profile hash mismatch", "pass", "arming rejected on profile hash mismatch")


def s17_moveit_plan_only(root: Path) -> ScenarioResult:
    return ScenarioResult("S17", "MoveIt plan-only", "skipped", "MoveIt integration is M4/M6; mock core has no MoveIt")


SCENARIOS = {
    "S1": s1_contract_and_step,
    "S2": s2_contract_mismatch,
    "S3": s3_lease_lifecycle,
    "S4": s4_lease_contention,
    "S5": s5_lease_expiry_in_flight,
    "S6": s6_safety_stop_and_reset,
    "S7": s7_stale_state,
    "S8": s8_limits_workspace_table,
    "S9": s9_guard_violation,
    "S10": s10_no_lease,
    "S11": s11_idempotency,
    "S12": s12_bus_failure_unknown,
    "S13": s13_replay,
    "S14": s14_base_pulse,
    "S15": s15_gateway_auth_rate_reconnect,
    "S16": s16_profile_mismatch,
    "S17": s17_moveit_plan_only,
}


def run_all(root: str | Path) -> dict[str, object]:
    root = Path(root)
    results: list[ScenarioResult] = []
    for scenario_id, function in SCENARIOS.items():
        try:
            results.append(function(root))
        except Exception as exc:  # noqa: BLE001 - scenario runner reports failures
            results.append(
                ScenarioResult(
                    scenario_id,
                    scenario_id,
                    "fail",
                    f"{type(exc).__name__}: {exc}",
                )
            )
    required = [item for item in results if item.id in REQUIRED]
    failed = [item for item in required if item.status != "pass"]
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "required": sorted(REQUIRED),
        "scenarios": [item.to_dict() for item in results],
        "summary": {
            "total": len(results),
            "passed": sum(1 for item in results if item.status == "pass"),
            "skipped": sum(1 for item in results if item.status == "skipped"),
            "failed": sum(1 for item in results if item.status == "fail"),
            "required_failed": len(failed),
        },
    }
    return report


def write_report(root: str | Path, output: str | Path | None = None) -> Path:
    root = Path(root)
    path = Path(output) if output else root / "e2e" / "reports" / "m3-mock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = run_all(root)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run M3 mock scenarios")
    parser.add_argument("--root", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    from .config import find_repo_root

    root = Path(args.root) if args.root else find_repo_root()
    report = run_all(root)
    for item in report["scenarios"]:
        print(f"{item['id']:>3} {item['status']:<7} {item['name']}: {item['detail']}")
    summary = report["summary"]
    print(
        f"summary: {summary['passed']} passed, {summary['skipped']} skipped, "
        f"{summary['failed']} failed; required failures: {summary['required_failed']}"
    )
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if summary["required_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
