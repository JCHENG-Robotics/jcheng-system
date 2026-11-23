"""Lease-based control authority."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from .errors import ErrorCode, MotionRejected
from .models import ContractVersion, Lease
from .timebase import Clock

SCOPES = {"arm", "gripper", "base", "full"}
MIN_TTL_MS = 100
MAX_TTL_MS = 300_000


@dataclass(frozen=True)
class LeaseCheck:
    ok: bool
    code: str
    lease: Lease | None = None
    message: str = ""


def _overlaps(left: str, right: str) -> bool:
    return left == "full" or right == "full" or left == right


class LeaseManager:
    def __init__(self, contract: ContractVersion, clock: Clock) -> None:
        self.contract = contract
        self.clock = clock
        self._leases: dict[str, Lease] = {}
        self._expired: set[str] = set()
        self.events: list[dict[str, object]] = []

    def _emit(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, "at": self.clock.now(), **fields})

    def _check_contract(self, contract: ContractVersion) -> None:
        mismatch = contract.mismatch(self.contract)
        if mismatch:
            raise MotionRejected(mismatch[0], mismatch[1])

    def expire(self, now: float | None = None) -> list[Lease]:
        now = self.clock.now() if now is None else now
        expired: list[Lease] = []
        for lease_id, lease in list(self._leases.items()):
            if not lease.active(now):
                del self._leases[lease_id]
                self._expired.add(lease_id)
                expired.append(lease)
                self._emit("lease_expired", lease_id=lease_id, holder=lease.holder)
        return expired

    def acquire(
        self,
        holder: str,
        scope: str,
        mode: str,
        priority: int,
        ttl_ms: int,
        deadman_required: bool,
        contract: ContractVersion,
        now: float | None = None,
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
            deadman_required=bool(deadman_required),
            issued_at=now,
            expires_at=now + int(ttl_ms) / 1000.0,
            contract=contract,
        )
        self._leases[lease.lease_id] = lease
        self._expired.discard(lease.lease_id)
        self._emit("lease_acquired", lease_id=lease.lease_id, holder=holder, scope=scope)
        return lease

    def renew(
        self,
        lease_id: str,
        ttl_ms: int,
        contract: ContractVersion,
        now: float | None = None,
    ) -> Lease:
        self._check_contract(contract)
        now = self.clock.now() if now is None else now
        if not MIN_TTL_MS <= int(ttl_ms) <= MAX_TTL_MS:
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "ttl_ms outside bounds")
        lease = self._leases.get(lease_id)
        if lease is None:
            code = ErrorCode.LEASE_EXPIRED if lease_id in self._expired else ErrorCode.NO_LEASE
            raise MotionRejected(code, "lease is not active")
        if not lease.active(now):
            del self._leases[lease_id]
            self._expired.add(lease_id)
            self._emit("lease_expired", lease_id=lease_id, holder=lease.holder)
            raise MotionRejected(ErrorCode.LEASE_EXPIRED, "lease has expired")
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
        )
        self._leases[lease_id] = renewed
        self._emit("lease_renewed", lease_id=lease_id, holder=lease.holder)
        return renewed

    def release(self, lease_id: str) -> bool:
        lease = self._leases.pop(lease_id, None)
        if lease is None:
            return False
        self._emit("lease_released", lease_id=lease_id, holder=lease.holder)
        return True

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
        self._expired.add(lease_id)
        self._emit(
            "lease_revoked",
            lease_id=lease_id,
            holder=lease.holder,
            operator_id=operator_id,
            reason=reason,
        )
        return True

    def check(self, lease_id: str, scope: str, now: float | None = None) -> LeaseCheck:
        now = self.clock.now() if now is None else now
        if not lease_id:
            return LeaseCheck(False, ErrorCode.NO_LEASE.value, None, "lease_id is required")
        lease = self._leases.get(lease_id)
        if lease is None:
            code = ErrorCode.LEASE_EXPIRED if lease_id in self._expired else ErrorCode.NO_LEASE
            return LeaseCheck(False, code.value, None, "lease is not active")
        if not lease.active(now):
            del self._leases[lease_id]
            self._expired.add(lease_id)
            self._emit("lease_expired", lease_id=lease_id, holder=lease.holder)
            return LeaseCheck(False, ErrorCode.LEASE_EXPIRED.value, None, "lease has expired")
        if not (lease.scope == "full" or lease.scope == scope):
            return LeaseCheck(
                False,
                ErrorCode.NOT_LEASE_HOLDER.value,
                lease,
                f"lease scope {lease.scope} does not cover {scope}",
            )
        return LeaseCheck(True, ErrorCode.OK.value, lease, "ok")

    def active(self, now: float | None = None) -> list[Lease]:
        now = self.clock.now() if now is None else now
        self.expire(now)
        return list(self._leases.values())

    def owner(self, scope: str, now: float | None = None) -> Lease | None:
        for lease in self.active(now):
            if lease.scope == "full" or lease.scope == scope:
                return lease
        return None
