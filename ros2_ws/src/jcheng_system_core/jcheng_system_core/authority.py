"""Lease-based control authority with holder and deadman enforcement.

A lease is the only control authority the hardware paths accept. Every check
fails closed: unknown, expired, revoked, held by another holder, outside the
requested scope, or keeping a deadman lease alive without heartbeats is a
rejection. ``revoke`` optionally runs a stop hook so revoking control also
latches the stop path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
import threading
from typing import Protocol, runtime_checkable
import uuid

from .errors import ErrorCode, MotionRejected
from .models import ContractVersion, Lease
from .timebase import Clock

SCOPES = {"arm", "gripper", "base", "full"}
MIN_TTL_MS = 100
MAX_TTL_MS = 300_000

# Provisional deadman defaults. The 300 ms window is a design value from the
# boundary audit and has not been validated on hardware.
DEFAULT_DEADMAN_MS = 300
MIN_DEADMAN_MS = 50
MAX_DEADMAN_MS = 60_000

StopHook = Callable[[str, str], None]


def _locked(method):
    """Serialize a lease operation; the lock is reentrant."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class LeaseCheck:
    ok: bool
    code: str
    lease: Lease | None = None
    message: str = ""


def _overlaps(left: str, right: str) -> bool:
    return left == "full" or right == "full" or left == right


@runtime_checkable
class LeaseView(Protocol):
    """Read-only lease source used by every motion path.

    ``LeaseManager`` itself satisfies the protocol. A consumer must receive
    the same instance the gateway uses; it must not construct a second
    manager. ``GatewayLeaseView`` is a thin delegating wrapper for that case.
    """

    contract: ContractVersion

    def check(
        self,
        lease_id: str,
        scope: str,
        now: float | None = None,
        holder: str | None = None,
        touch: bool = False,
        require_holder: bool | None = None,
    ) -> LeaseCheck: ...


class LeaseManager:
    def __init__(
        self,
        contract: ContractVersion,
        clock: Clock,
        default_deadman_ms: int = DEFAULT_DEADMAN_MS,
        stop_hook: StopHook | None = None,
        allow_deadman_opt_out: bool = False,
        require_holder: bool = True,
    ) -> None:
        self.contract = contract
        self.clock = clock
        if not MIN_DEADMAN_MS <= int(default_deadman_ms) <= MAX_DEADMAN_MS:
            raise ValueError("default_deadman_ms outside bounds")
        self.default_deadman_ms = int(default_deadman_ms)
        # Opt-out is a deliberate test/legacy configuration flag, not a
        # default. Normal issuers require a deadman window.
        self.allow_deadman_opt_out = bool(allow_deadman_opt_out)
        # A motion check must assert the lease holder. Status/observability
        # callers can opt out per call with require_holder=False.
        self.require_holder = bool(require_holder)
        self._stop_hook = stop_hook
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._heartbeats: dict[str, float] = {}
        self._expired: set[str] = set()
        self._revoked: set[str] = set()
        self.events: list[dict[str, object]] = []

    def _emit(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, "at": self.clock.now(), **fields})

    def _check_contract(self, contract: ContractVersion) -> None:
        mismatch = contract.mismatch(self.contract)
        if mismatch:
            raise MotionRejected(mismatch[0], mismatch[1])

    def _unknown_code(self, lease_id: str) -> ErrorCode:
        if lease_id in self._revoked:
            return ErrorCode.LEASE_REVOKED
        if lease_id in self._expired:
            return ErrorCode.LEASE_EXPIRED
        return ErrorCode.NO_LEASE

    def _forget(self, lease_id: str) -> None:
        self._heartbeats.pop(lease_id, None)

    def _expire_locked(self, lease_id: str, lease: Lease) -> None:
        del self._leases[lease_id]
        self._expired.add(lease_id)
        self._forget(lease_id)
        self._emit("lease_expired", lease_id=lease_id, holder=lease.holder)

    def _deadman_window_s(self, lease: Lease) -> float:
        return lease.deadman_ms / 1000.0

    def _deadman_ok(self, lease: Lease, now: float) -> bool:
        if lease.deadman_ms <= 0:
            return True
        last = self._heartbeats.get(lease.lease_id)
        if last is None:
            return False
        return (now - last) <= self._deadman_window_s(lease)

    def set_stop_hook(self, hook: StopHook | None) -> None:
        with self._lock:
            self._stop_hook = hook

    @_locked
    def expire(self, now: float | None = None) -> list[Lease]:
        now = self.clock.now() if now is None else now
        expired: list[Lease] = []
        for lease_id, lease in list(self._leases.items()):
            if not lease.active(now):
                self._expire_locked(lease_id, lease)
                expired.append(lease)
        return expired

    @_locked
    def acquire(
        self,
        holder: str,
        scope: str,
        mode: str,
        priority: int,
        ttl_ms: int,
        deadman_required: bool | None,
        contract: ContractVersion,
        now: float | None = None,
        deadman_ms: int | None = None,
    ) -> Lease:
        self._check_contract(contract)
        now = self.clock.now() if now is None else now
        if not holder.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "holder is required")
        if scope not in SCOPES:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, f"unknown scope {scope}")
        if mode != "exclusive":
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "mode must be exclusive")
        if not 0 <= int(priority) <= 255:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "priority must be 0..255")
        if not MIN_TTL_MS <= int(ttl_ms) <= MAX_TTL_MS:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "ttl_ms outside bounds")
        # None means "use the secure default" (required). An explicit False is
        # an opt-out only when the manager was deliberately configured for it.
        required = True if deadman_required is None else bool(deadman_required)
        if deadman_ms is None:
            effective_deadman = self.default_deadman_ms if required else 0
        else:
            effective_deadman = int(deadman_ms)
            if effective_deadman != 0 and not (
                MIN_DEADMAN_MS <= effective_deadman <= MAX_DEADMAN_MS
            ):
                raise MotionRejected(
                    ErrorCode.INVALID_COMMAND, "deadman_ms outside bounds"
                )
        if effective_deadman <= 0:
            if required:
                raise MotionRejected(
                    ErrorCode.INVALID_COMMAND,
                    "deadman_ms=0 is not allowed when deadman is required",
                )
            if not self.allow_deadman_opt_out:
                raise MotionRejected(
                    ErrorCode.INVALID_COMMAND,
                    "deadman opt-out requires allow_deadman_opt_out=True",
                )

        self.expire(now)
        for existing in list(self._leases.values()):
            if not _overlaps(existing.scope, scope):
                continue
            if existing.priority >= int(priority):
                raise MotionRejected(
                    ErrorCode.LEASE_CONFLICT,
                    f"{existing.holder} holds {existing.scope} at priority {existing.priority}",
                )
            del self._leases[existing.lease_id]
            self._expired.add(existing.lease_id)
            self._forget(existing.lease_id)
            self._emit(
                "lease_preempted",
                lease_id=existing.lease_id,
                holder=existing.holder,
                by=holder,
            )

        lease = Lease(
            lease_id=f"lease-{uuid.uuid4().hex[:12]}",
            holder=holder,
            scope=scope,
            mode="exclusive",
            priority=int(priority),
            deadman_required=effective_deadman > 0,
            issued_at=now,
            expires_at=now + int(ttl_ms) / 1000.0,
            contract=contract,
            deadman_ms=int(effective_deadman),
        )
        self._leases[lease.lease_id] = lease
        if lease.deadman_ms > 0:
            # Issuance counts as the first heartbeat; a lease with a deadman
            # window is not usable forever without later heartbeats.
            self._heartbeats[lease.lease_id] = now
        self._expired.discard(lease.lease_id)
        self._revoked.discard(lease.lease_id)
        self._emit(
            "lease_acquired",
            lease_id=lease.lease_id,
            holder=holder,
            scope=scope,
            deadman_ms=lease.deadman_ms,
        )
        if lease.deadman_ms == 0:
            self._emit(
                "lease_deadman_opt_out",
                lease_id=lease.lease_id,
                holder=holder,
                operator="config",
            )
        return lease

    @_locked
    def renew(
        self,
        lease_id: str,
        ttl_ms: int,
        contract: ContractVersion,
        now: float | None = None,
        holder: str | None = None,
    ) -> Lease:
        self._check_contract(contract)
        now = self.clock.now() if now is None else now
        if not MIN_TTL_MS <= int(ttl_ms) <= MAX_TTL_MS:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "ttl_ms outside bounds")
        lease = self._leases.get(lease_id)
        if lease is None:
            raise MotionRejected(self._unknown_code(lease_id), "lease is not active")
        if holder is not None and str(holder) != lease.holder:
            raise MotionRejected(
                ErrorCode.NOT_LEASE_HOLDER,
                f"lease holder is {lease.holder}",
            )
        if not lease.active(now):
            self._expire_locked(lease_id, lease)
            raise MotionRejected(ErrorCode.LEASE_EXPIRED, "lease has expired")
        # A holder-authenticated renewal proves holder liveness and refreshes
        # the deadman for the renewed window.
        if lease.deadman_ms > 0:
            self._heartbeats[lease_id] = now
        renewed = Lease(
            lease_id=lease.lease_id,
            holder=lease.holder,
            scope=lease.scope,
            mode=lease.mode,
            priority=lease.priority,
            deadman_required=lease.deadman_required,
            issued_at=lease.issued_at,
            expires_at=now + int(ttl_ms) / 1000.0,
            contract=lease.contract,
            deadman_ms=lease.deadman_ms,
        )
        self._leases[lease_id] = renewed
        self._emit("lease_renewed", lease_id=lease_id, holder=lease.holder)
        return renewed

    @_locked
    def release(self, lease_id: str, holder: str | None = None) -> bool:
        lease = self._leases.get(lease_id)
        if lease is None:
            return False
        if holder is not None and str(holder) != lease.holder:
            raise MotionRejected(
                ErrorCode.NOT_LEASE_HOLDER,
                f"lease holder is {lease.holder}",
            )
        del self._leases[lease_id]
        self._forget(lease_id)
        self._emit("lease_released", lease_id=lease_id, holder=lease.holder)
        return True

    @_locked
    def note_heartbeat(
        self,
        lease_id: str,
        now: float | None = None,
        holder: str | None = None,
    ) -> Lease:
        """Record control-input liveness for a deadman lease.

        Unknown, expired, revoked or mismatched holders are rejected; a stale
        deadman is revived by an explicit heartbeat, which is the holder's
        assertion that it is still driving.
        """

        now = self.clock.now() if now is None else now
        if not lease_id:
            raise MotionRejected(ErrorCode.NO_LEASE, "lease_id is required")
        lease = self._leases.get(lease_id)
        if lease is None:
            raise MotionRejected(self._unknown_code(lease_id), "lease is not active")
        if holder is not None and str(holder) != lease.holder:
            raise MotionRejected(
                ErrorCode.NOT_LEASE_HOLDER,
                f"lease holder is {lease.holder}",
            )
        if not lease.active(now):
            self._expire_locked(lease_id, lease)
            raise MotionRejected(ErrorCode.LEASE_EXPIRED, "lease has expired")
        self._heartbeats[lease_id] = now
        return lease

    @_locked
    def revoke(
        self,
        lease_id: str,
        operator_id: str,
        reason: str,
        contract: ContractVersion,
    ) -> bool:
        self._check_contract(contract)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        lease = self._leases.pop(lease_id, None)
        if lease is None:
            return False
        self._revoked.add(lease_id)
        self._expired.discard(lease_id)
        self._forget(lease_id)
        self._emit(
            "lease_revoked",
            lease_id=lease_id,
            holder=lease.holder,
            operator_id=operator_id,
            reason=reason,
        )
        hook = self._stop_hook
        if hook is not None:
            try:
                hook(lease_id, reason)
            except Exception as exc:  # noqa: BLE001 - revoke must complete
                self._emit(
                    "lease_revoke_stop_failed",
                    lease_id=lease_id,
                    operator_id=operator_id,
                    error=str(exc),
                )
        return True

    @_locked
    def check(
        self,
        lease_id: str,
        scope: str,
        now: float | None = None,
        holder: str | None = None,
        touch: bool = False,
        require_holder: bool | None = None,
    ) -> LeaseCheck:
        """Validate a lease for a motion scope.

        Motion callers must assert the holder. By default (``require_holder``
        None) the manager-level ``require_holder`` flag applies; a missing
        holder is rejected with ``NOT_LEASE_HOLDER`` rather than skipping the
        check. Only an explicitly configured legacy manager
        (``require_holder=False``) or a status/observability caller that passes
        ``require_holder=False`` may omit it.

        ``touch`` refreshes the deadman after a successful check; motion paths
        set it because an accepted command is proof of holder liveness.
        """

        now = self.clock.now() if now is None else now
        if not lease_id:
            return LeaseCheck(False, ErrorCode.NO_LEASE.value, None, "lease_id is required")
        lease = self._leases.get(lease_id)
        if lease is None:
            return LeaseCheck(
                False, self._unknown_code(lease_id).value, None, "lease is not active"
            )
        if not lease.active(now):
            self._expire_locked(lease_id, lease)
            return LeaseCheck(False, ErrorCode.LEASE_EXPIRED.value, None, "lease has expired")
        effective_require = self.require_holder if require_holder is None else bool(require_holder)
        if effective_require and (holder is None or not str(holder).strip()):
            return LeaseCheck(
                False,
                ErrorCode.NOT_LEASE_HOLDER.value,
                lease,
                "holder is required for a motion check",
            )
        if holder is not None and str(holder) != lease.holder:
            return LeaseCheck(
                False,
                ErrorCode.NOT_LEASE_HOLDER.value,
                lease,
                f"lease holder is {lease.holder}",
            )
        if not self._deadman_ok(lease, now):
            return LeaseCheck(
                False,
                ErrorCode.SAFETY_HEARTBEAT_MISSING.value,
                lease,
                f"deadman heartbeat missing or older than {lease.deadman_ms} ms",
            )
        if not (lease.scope == "full" or lease.scope == scope):
            return LeaseCheck(
                False,
                ErrorCode.NOT_LEASE_HOLDER.value,
                lease,
                f"lease scope {lease.scope} does not cover {scope}",
            )
        if touch and lease.deadman_ms > 0:
            self._heartbeats[lease_id] = now
        return LeaseCheck(True, ErrorCode.OK.value, lease, "ok")

    @_locked
    def active(self, now: float | None = None) -> list[Lease]:
        now = self.clock.now() if now is None else now
        self.expire(now)
        return list(self._leases.values())

    @_locked
    def owner(self, scope: str, now: float | None = None) -> Lease | None:
        for lease in self.active(now):
            if lease.scope == "full" or lease.scope == scope:
                return lease
        return None

    @_locked
    def holder_for(self, lease_id: str) -> str | None:
        """Return the recorded holder for a lease id, without a motion check.

        This is for internal legacy adapters (for example teleop) that must
        assert the real holder on a generated command; it is not an
        authorization check.
        """

        lease = self._leases.get(lease_id)
        return lease.holder if lease is not None else None


class GatewayLeaseView:
    """Lease view over the *same* ``LeaseManager`` the gateway uses.

    Every check delegates live to the wrapped manager; the view never copies
    lease state and never creates a manager. Passing a different manager's
    lease to this view is therefore rejected as ``NO_LEASE``.
    """

    def __init__(self, manager: LeaseManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> LeaseManager:
        return self._manager

    @property
    def contract(self) -> ContractVersion:
        return self._manager.contract

    def check(
        self,
        lease_id: str,
        scope: str,
        now: float | None = None,
        holder: str | None = None,
        touch: bool = False,
        require_holder: bool | None = None,
    ) -> LeaseCheck:
        return self._manager.check(
            lease_id,
            scope,
            now=now,
            holder=holder,
            touch=touch,
            require_holder=require_holder,
        )

    def note_heartbeat(
        self,
        lease_id: str,
        now: float | None = None,
        holder: str | None = None,
    ) -> Lease:
        return self._manager.note_heartbeat(lease_id, now=now, holder=holder)

    def status(self) -> dict[str, object]:
        leases = self._manager.active()
        return {
            "source": "gateway",
            "contract": self._manager.contract.to_dict(),
            "active_leases": [lease.lease_id for lease in leases],
        }


class RejectAllLeaseView:
    """Fail-closed lease view when no shared lease source is configured.

    It intentionally cannot issue, adopt or read any local lease. The node
    uses it for ``lease_source=none`` and when a configured gateway source is
    unavailable in the process.
    """

    def __init__(
        self,
        contract: ContractVersion,
        reason: str = "no shared lease source is configured",
    ) -> None:
        self.contract = contract
        self.reason = str(reason)

    def check(
        self,
        lease_id: str,
        scope: str,
        now: float | None = None,
        holder: str | None = None,
        touch: bool = False,
        require_holder: bool | None = None,
    ) -> LeaseCheck:
        return LeaseCheck(False, ErrorCode.NO_LEASE.value, None, self.reason)

    def note_heartbeat(
        self,
        lease_id: str,
        now: float | None = None,
        holder: str | None = None,
    ) -> Lease:
        raise MotionRejected(ErrorCode.NO_LEASE, self.reason)

    def status(self) -> dict[str, object]:
        return {
            "source": "reject-all",
            "reason": self.reason,
            "contract": self.contract.to_dict(),
        }
