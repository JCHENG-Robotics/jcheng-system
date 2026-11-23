"""Motion arbiter and mock executor."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import time

from .authority import LeaseManager
from .config import RobotProfile
from .errors import BusReadFailed, ErrorCode, MotionRejected
from .evidence import EvidenceStore
from .guard import PublisherGuard
from .mock_hardware import MockHardwareBridge
from .models import (
    ContractVersion,
    MotionCommand,
    MotionResult,
    StopResult,
    ValidationReport,
)
from .safety import SafetySupervisor
from .timebase import Clock


@dataclass
class ActiveMotion:
    command: MotionCommand
    report: ValidationReport
    started_at: float


class MotionArbiter:
    def __init__(
        self,
        profile: RobotProfile,
        contract: ContractVersion,
        clock: Clock,
        safety: SafetySupervisor,
        authority: LeaseManager,
        hardware: MockHardwareBridge,
        guard: PublisherGuard,
        evidence: EvidenceStore | None = None,
        max_commands_per_second: int = 20,
    ) -> None:
        self.profile = profile
        self.contract = contract
        self.clock = clock
        self.safety = safety
        self.authority = authority
        self.hardware = hardware
        self.guard = guard
        self.evidence = evidence or EvidenceStore(clock)
        self.max_commands_per_second = max(1, int(max_commands_per_second))
        self._active: ActiveMotion | None = None
        self._idempotency: dict[str, tuple[str, MotionResult]] = {}
        self._rates: dict[str, deque[float]] = defaultdict(deque)

    # ---- helpers ----

    @staticmethod
    def scope_for_kind(kind: str) -> str:
        if kind == "GRIPPER":
            return "gripper"
        if kind == "BASE_PULSE":
            return "base"
        return "arm"

    def _now(self, now: float | None) -> float:
        return self.clock.now() if now is None else now

    def _rate_limit(self, source: str, now: float) -> None:
        window = self._rates[source]
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self.max_commands_per_second:
            raise MotionRejected(ErrorCode.RATE_LIMITED, "command rate limit exceeded")
        window.append(now)

    def _rejected(self, command: MotionCommand, code: str, message: str, now: float) -> MotionResult:
        state = self.hardware.state(now)
        return MotionResult(
            intent_id=command.intent_id,
            outcome="REJECTED",
            reason_code=code,
            message=message,
            safety=self.safety.current_state(),
            measured_positions=list(state.positions),
            bus_verified=False,
            calibration_invalidated=False,
            completed_at=now,
        )

    def _complete(
        self,
        active: ActiveMotion,
        now: float,
        *,
        outcome: str,
        code: str,
        message: str,
        bus_verified: bool,
        calibration_invalidated: bool,
        measured_positions: list[float],
    ) -> MotionResult:
        result = MotionResult(
            intent_id=active.command.intent_id,
            outcome=outcome,
            reason_code=code,
            message=message,
            safety=self.safety.current_state(),
            measured_positions=list(measured_positions),
            bus_verified=bus_verified,
            calibration_invalidated=calibration_invalidated,
            completed_at=now,
        )
        result.evidence_id = self.evidence.record_motion(active.command, active.report, result)
        self._idempotency[active.command.idempotency_key] = (
            active.command.payload_hash(),
            result,
        )
        return result

    # ---- validation ----

    def validate(self, command: MotionCommand, now: float | None = None) -> ValidationReport:
        now = self._now(now)
        guard = self.guard.enforce(self.safety, self.hardware.publishers)
        lease = self.authority.check(command.lease_id, self.scope_for_kind(command.kind), now)
        if not guard.ok:
            return self.safety.validate(command, False, ErrorCode.SAFETY_FAULT.value, now)
        return self.safety.validate(command, lease.ok, lease.code, now)

    # ---- execution ----

    def start(self, command: MotionCommand, now: float | None = None) -> MotionResult | None:
        """Start a motion. Returns a terminal result, or None if active."""
        now = self._now(now)
        cached = self._idempotency.get(command.idempotency_key)
        if cached is not None:
            cached_hash, result = cached
            if cached_hash != command.payload_hash():
                return self._rejected(command, ErrorCode.IDEMPOTENCY_CONFLICT.value, "idempotency key reused with a different payload", now)
            return result
        if self._active is not None:
            return self._rejected(command, ErrorCode.MOTION_BUSY.value, "another motion is active", now)
        try:
            self._rate_limit(command.source, now)
        except MotionRejected as exc:
            return self._rejected(command, exc.code, exc.message, now)
        report = self.validate(command, now)
        if not report.approved:
            return self._rejected(command, report.reason_code, report.violations[0] if report.violations else "validation failed", now)
        try:
            self.safety.begin_motion()
        except MotionRejected as exc:
            return self._rejected(command, exc.code, exc.message, now)
        active = ActiveMotion(command=command, report=report, started_at=now)
        self._active = active
        try:
            self.hardware.publish(command, now)
        except BusReadFailed as exc:
            self.safety.latch(ErrorCode.MOTION_OUTCOME_UNKNOWN, str(exc))
            self._active = None
            result = self._complete(
                active,
                now,
                outcome="UNKNOWN",
                code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                message=str(exc),
                bus_verified=False,
                calibration_invalidated=False,
                measured_positions=self.hardware.state(now).positions,
            )
            return result
        return None

    def finish(self, now: float | None = None) -> MotionResult | None:
        now = self._now(now)
        active = self._active
        if active is None:
            return None
        command = active.command
        if command.kind == "BASE_PULSE":
            self.hardware.zero_base(now)
            self.safety.end_motion()
            self._active = None
            return self._complete(
                active,
                now,
                outcome="COMPLETED",
                code=ErrorCode.OK.value,
                message="base pulse completed (open loop)",
                bus_verified=False,
                calibration_invalidated=True,
                measured_positions=self.hardware.state(now).positions,
            )
        try:
            measured = self.hardware.bus_read(now)
        except BusReadFailed as exc:
            self.safety.latch(ErrorCode.MOTION_OUTCOME_UNKNOWN, str(exc))
            self._active = None
            return self._complete(
                active,
                now,
                outcome="UNKNOWN",
                code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                message=str(exc),
                bus_verified=False,
                calibration_invalidated=False,
                measured_positions=self.hardware.state(now).positions,
            )
        self.safety.end_motion()
        self._active = None
        return self._complete(
            active,
            now,
            outcome="COMPLETED",
            code=ErrorCode.OK.value,
            message="motion completed with bus verification",
            bus_verified=True,
            calibration_invalidated=False,
            measured_positions=measured.positions,
        )

    def execute(self, command: MotionCommand, now: float | None = None) -> MotionResult:
        started = self.start(command, now)
        if started is not None:
            return started
        result = self.finish(now)
        assert result is not None
        return result

    # ---- lifecycle ----

    def tick(self, now: float | None = None) -> list[MotionResult]:
        now = self._now(now)
        self.authority.expire(now)
        self.hardware.tick(now)
        self.safety.tick(now)
        results: list[MotionResult] = []
        active = self._active
        if active is None:
            return results
        lease = self.authority.check(
            active.command.lease_id,
            self.scope_for_kind(active.command.kind),
            now,
        )
        if not lease.ok:
            self.safety.latch(lease.code, lease.message or "lease expired or revoked")
            self.hardware.hold(now)
            self.hardware.zero_base(now)
            self._active = None
            results.append(
                self._complete(
                    active,
                    now,
                    outcome="ABORTED",
                    code=lease.code,
                    message=lease.message,
                    bus_verified=False,
                    calibration_invalidated=False,
                    measured_positions=self.hardware.state(now).positions,
                )
            )
            return results
        if self.safety.stop_latched:
            self.hardware.hold(now)
            self.hardware.zero_base(now)
            self._active = None
            results.append(
                self._complete(
                    active,
                    now,
                    outcome="ABORTED",
                    code=ErrorCode.SAFETY_STOP_LATCHED.value,
                    message="safety stop latched during motion",
                    bus_verified=False,
                    calibration_invalidated=False,
                    measured_positions=self.hardware.state(now).positions,
                )
            )
        return results

    def stop(self, operator_id: str, reason: str, now: float | None = None) -> StopResult:
        now = self._now(now)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        self.safety.latch(ErrorCode.SAFETY_STOP_LATCHED, f"software stop by {operator_id}: {reason}")
        self.hardware.hold(now)
        self.hardware.zero_base(now)
        if self._active is not None:
            active = self._active
            self._active = None
            self._complete(
                active,
                now,
                outcome="ABORTED",
                code=ErrorCode.SAFETY_STOP_LATCHED.value,
                message="motion aborted by software stop",
                bus_verified=False,
                calibration_invalidated=False,
                measured_positions=self.hardware.state(now).positions,
            )
        return StopResult(
            accepted=True,
            safety=self.safety.current_state(),
            message="software stop requested; not an emergency stop",
            requested_at=now,
        )
