"""Lease holder, deadman and revocation-stop enforcement.

Fault-injection coverage: wrong holder/scope, unknown/invalid/expired/revoked
leases, deadman loss, revocation stop hook, HTTP DELETE/renew holder checks,
and a stop that stays reachable after the motion rate limit or lease expiry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import load_contract
from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.gateway import GatewayApp
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "lease-token"


def _manager() -> tuple[LeaseManager, object]:
    contract = load_contract(ROOT / "configs" / "contract.yaml")
    clock = ManualClock()
    return LeaseManager(contract.version, clock), contract.version


def _acquire(manager: LeaseManager, contract, **overrides):
    values = {
        "holder": "alice",
        "scope": "arm",
        "mode": "exclusive",
        "priority": 50,
        "ttl_ms": 5000,
        "deadman_required": False,
        "contract": contract,
    }
    values.update(overrides)
    return manager.acquire(**values)


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}


def _json(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _acquire_http(app: GatewayApp, stack: MockStack, holder: str = "alice") -> str:
    status, payload = app.handle(
        "POST",
        "/v1/leases",
        _headers(),
        _json(
            {
                "holder": holder,
                "scope": "arm",
                "mode": "exclusive",
                "priority": 50,
                "ttl_ms": 5000,
                "deadman_required": False,
                "contract": stack.version.to_dict(),
            }
        ),
    )
    assert status == 200, payload
    return payload["lease_id"]


def test_renew_requires_the_matching_holder() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract)
    with pytest.raises(MotionRejected) as exc:
        manager.renew(lease.lease_id, 6000, contract, holder="mallory")
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value
    renewed = manager.renew(lease.lease_id, 6000, contract, holder="alice")
    assert renewed.expires_at > lease.expires_at
    # Legacy callers that only carry a lease id are still accepted.
    assert manager.renew(lease.lease_id, 7000, contract).expires_at > renewed.expires_at


def test_release_requires_the_matching_holder() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract)
    with pytest.raises(MotionRejected) as exc:
        manager.release(lease.lease_id, holder="mallory")
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert manager.release(lease.lease_id, holder="alice") is True
    assert manager.release(lease.lease_id, holder="alice") is False


def test_unknown_invalid_expired_and_revoked_leases_are_rejected() -> None:
    manager, contract = _manager()
    assert manager.check("", "arm").code == ErrorCode.NO_LEASE.value
    assert manager.check("lease-unknown", "arm").code == ErrorCode.NO_LEASE.value

    short = _acquire(manager, contract, ttl_ms=100)
    manager.clock.advance(0.2)
    expired = manager.check(short.lease_id, "arm")
    assert expired.code == ErrorCode.LEASE_EXPIRED.value
    with pytest.raises(MotionRejected) as exc:
        manager.note_heartbeat(short.lease_id)
    assert exc.value.code == ErrorCode.LEASE_EXPIRED.value

    revoked = _acquire(manager, contract, holder="bob", priority=60)
    assert manager.revoke(revoked.lease_id, "operator", "test", contract) is True
    assert manager.check(revoked.lease_id, "arm").code == ErrorCode.LEASE_REVOKED.value
    with pytest.raises(MotionRejected) as exc:
        manager.renew(revoked.lease_id, 5000, contract)
    assert exc.value.code == ErrorCode.LEASE_REVOKED.value


def test_scope_mismatch_is_rejected() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract, scope="arm")
    check = manager.check(lease.lease_id, "base")
    assert check.ok is False
    assert check.code == ErrorCode.NOT_LEASE_HOLDER.value
    full = _acquire(manager, contract, holder="carol", scope="full", priority=80)
    assert manager.check(full.lease_id, "base").ok is True


def test_deadman_missing_and_revived_by_heartbeat() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract, deadman_required=True)
    assert lease.deadman_ms == 300
    assert manager.check(lease.lease_id, "arm", now=0.0).ok is True
    stale = manager.check(lease.lease_id, "arm", now=0.301)
    assert stale.ok is False
    assert stale.code == ErrorCode.SAFETY_HEARTBEAT_MISSING.value
    revived = manager.note_heartbeat(lease.lease_id, now=0.301, holder="alice")
    assert revived.lease_id == lease.lease_id
    assert manager.check(lease.lease_id, "arm", now=0.302).ok is True
    assert manager.check(lease.lease_id, "arm", now=0.602).ok is False


def test_deadman_heartbeat_holder_and_unknown_are_rejected() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract, deadman_required=True)
    with pytest.raises(MotionRejected) as exc:
        manager.note_heartbeat(lease.lease_id, holder="mallory")
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value
    with pytest.raises(MotionRejected) as exc:
        manager.note_heartbeat("lease-unknown")
    assert exc.value.code == ErrorCode.NO_LEASE.value
    with pytest.raises(MotionRejected) as exc:
        _acquire(manager, contract, holder="bob", priority=70, deadman_required=True, deadman_ms=0)
    assert exc.value.code == ErrorCode.INVALID_COMMAND.value
    with pytest.raises(MotionRejected) as exc:
        _acquire(manager, contract, holder="bob", priority=70, deadman_required=True, deadman_ms=10)
    assert exc.value.code == ErrorCode.INVALID_COMMAND.value


def test_revoke_runs_the_stop_hook_exactly_once() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract)
    seen: list[tuple[str, str]] = []
    manager.set_stop_hook(lambda lease_id, reason: seen.append((lease_id, reason)))
    assert manager.revoke(lease.lease_id, "operator", "revoked by test", contract) is True
    assert seen == [(lease.lease_id, "revoked by test")]
    assert manager.revoke(lease.lease_id, "operator", "again", contract) is False
    assert len(seen) == 1


def test_revoke_stop_hook_failure_does_not_undo_the_revocation() -> None:
    manager, contract = _manager()
    lease = _acquire(manager, contract)

    def boom(lease_id: str, reason: str) -> None:
        raise RuntimeError("stop path unavailable")

    manager.set_stop_hook(boom)
    assert manager.revoke(lease.lease_id, "operator", "test", contract) is True
    events = {item["event"] for item in manager.events}
    assert "lease_revoke_stop_failed" in events
    assert manager.check(lease.lease_id, "arm").code == ErrorCode.LEASE_REVOKED.value


def test_gateway_delete_and_renew_require_the_holder() -> None:
    stack = MockStack.build(ROOT, clock=ManualClock())
    app = GatewayApp(stack, TOKEN, max_requests_per_second=100)

    lease_id = _acquire_http(app, stack)
    status, payload = app.handle("DELETE", f"/v1/leases/{lease_id}", _headers())
    assert status == 403
    assert payload["error"]["code"] == ErrorCode.FORBIDDEN.value

    status, payload = app.handle(
        "DELETE", f"/v1/leases/{lease_id}", _headers(), _json({"holder": "mallory"})
    )
    assert status == 403
    assert payload["error"]["code"] == ErrorCode.NOT_LEASE_HOLDER.value

    status, payload = app.handle(
        "DELETE", f"/v1/leases/{lease_id}", _headers(), _json({"holder": "alice"})
    )
    assert status == 200
    assert payload["released"] is True

    lease_id = _acquire_http(app, stack)
    renew = {
        "ttl_ms": 6000,
        "contract": stack.version.to_dict(),
    }
    status, payload = app.handle(
        "POST", f"/v1/leases/{lease_id}/renew", _headers(), _json(renew)
    )
    assert status == 403
    assert payload["error"]["code"] == ErrorCode.FORBIDDEN.value

    renew["holder"] = "mallory"
    status, payload = app.handle(
        "POST", f"/v1/leases/{lease_id}/renew", _headers(), _json(renew)
    )
    assert status == 403
    assert payload["error"]["code"] == ErrorCode.NOT_LEASE_HOLDER.value

    renew["holder"] = "alice"
    status, payload = app.handle(
        "POST", f"/v1/leases/{lease_id}/renew", _headers(), _json(renew)
    )
    assert status == 200
    assert payload["lease_id"] == lease_id


def test_stop_is_reachable_after_rate_limit_and_lease_expiry() -> None:
    stack = MockStack.build(ROOT, clock=ManualClock())
    app = GatewayApp(stack, TOKEN, max_requests_per_second=2)
    lease_id = _acquire_http(app, stack)

    assert app.handle("GET", "/v1/state", _headers())[0] == 200
    assert app.handle("GET", "/v1/state", _headers())[0] == 429
    # The stop path is exempt from the motion rate limit.
    status, payload = app.handle(
        "POST",
        "/v1/motion/stop",
        _headers(),
        _json({"operator_id": "operator", "reason": "rate-limited stop"}),
    )
    assert status == 200, payload
    assert stack.safety.stop_latched is True

    stack2 = MockStack.build(ROOT, clock=ManualClock())
    app2 = GatewayApp(stack2, TOKEN, max_requests_per_second=100)
    lease_id = _acquire_http(app2, stack2)
    stack2.clock.advance(6.0)
    assert stack2.authority.check(lease_id, "arm").code == ErrorCode.LEASE_EXPIRED.value
    status, payload = app2.handle(
        "POST",
        "/v1/motion/stop",
        _headers(),
        _json({"operator_id": "operator", "reason": "expired-lease stop"}),
    )
    assert status == 200, payload
    assert stack2.safety.stop_latched is True
