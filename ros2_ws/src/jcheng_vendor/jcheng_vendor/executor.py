"""Supervised single-joint and gripper executor for M5.2.

The executor is deliberately small: it validates through the existing
lease/safety core, sends exactly one step or gripper target through a bridge,
waits for the vendor interpolation to settle, and verifies the target with a
fresh bus read. A stop request latches safety and asks the bridge to hold the
last measured pulses without waiting for the action thread.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import threading
import time
from typing import Any

from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import RobotProfile
from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.evidence import EvidenceStore
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.hardware_authority import HardwareAuthority
from jcheng_system_core.models import (
    ContractVersion,
    MotionCommand,
    MotionResult,
    RobotState,
    StopResult,
)
from jcheng_system_core.safety import SafetySupervisor
from jcheng_system_core.timebase import Clock


@dataclass(frozen=True)
class BusSample:
    state: RobotState
    pulses: dict[str, int]


class HardwareBridge:
    """Bridge interface used by the executor and a fake test bridge."""

    publishers: set[str]

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        raise NotImplementedError

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        raise NotImplementedError

    def hold(self) -> None:
        raise NotImplementedError

    def read_bus(self, timeout_s: float = 1.0) -> BusSample:
        raise NotImplementedError

    def state(self) -> RobotState:
        raise NotImplementedError

    def pulse_snapshot(self) -> dict[str, int]:
        raise NotImplementedError


class HardwareExecutor:
    def __init__(
        self,
        profile: RobotProfile,
        contract: ContractVersion,
        clock: Clock,
        safety: SafetySupervisor,
        authority: LeaseManager,
        guard: PublisherGuard,
        evidence: EvidenceStore,
        bridge: HardwareBridge,
        tolerance_pulses: int = 8,
        min_gripper_delta_pulses: int = 5,
        settle_s: float = 0.4,
        max_commands_per_second: int = 5,
        hardware_authority: HardwareAuthority | None = None,
    ) -> None:
        self.profile = profile
        self.contract = contract
        self.clock = clock
        self.safety = safety
        self.authority = authority
        self.guard = guard
        self.evidence = evidence
        self.bridge = bridge
        # The bridge is only reachable through the single hardware authority.
        self.hardware_authority = hardware_authority or HardwareAuthority(
            contract=contract,
            authority=authority,
            safety=safety,
            clock=clock,
            bridge=bridge,
            guard=guard,
            max_base_duty=float(profile.profiles.get("base", {}).get("max_abs_duty_normalized", 0.10)),
        )
        self.tolerance_pulses = int(tolerance_pulses)
        self.min_gripper_delta_pulses = int(min_gripper_delta_pulses)
        self.settle_s = float(settle_s)
        self.max_commands_per_second = max(1, int(max_commands_per_second))
        self._state_lock = threading.RLock()
        self._active_lock = threading.Lock()
        self._active: MotionCommand | None = None
        self._stop_event = threading.Event()
        self._idempotency: dict[str, tuple[str, MotionResult]] = {}
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._evidence_lock = threading.Lock()
        self.last_stop_latency_ms: float | None = None

    # ---- helpers ----

    @staticmethod
    def scope_for_kind(kind: str) -> str:
        return "gripper" if kind == "GRIPPER" else "arm"

    def _now(self, now: float | None = None) -> float:
        return self.clock.now() if now is None else float(now)

    def _rate_limit(self, source: str, now: float) -> None:
        window = self._rates[source]
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= self.max_commands_per_second:
            raise MotionRejected(ErrorCode.RATE_LIMITED, "command rate limit exceeded")
        window.append(now)

    def _state_dict(self, now: float) -> dict[str, Any]:
        state = self.bridge.state()
        return {
            "joint_names": list(state.joint_names),
            "positions": [float(value) for value in state.positions],
            "gripper_position": float(state.gripper_position),
            "fresh": bool(state.fresh),
            "stamp": now,
        }

    def _rejected(self, command: MotionCommand, code: str, message: str, now: float) -> MotionResult:
        state = self.bridge.state()
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
        command: MotionCommand,
        report: Any,
        now: float,
        *,
        outcome: str,
        code: str,
        message: str,
        bus_verified: bool,
        measured_positions: list[float],
    ) -> MotionResult:
        result = MotionResult(
            intent_id=command.intent_id,
            outcome=outcome,
            reason_code=code,
            message=message,
            safety=self.safety.current_state(),
            measured_positions=list(measured_positions),
            bus_verified=bus_verified,
            calibration_invalidated=False,
            completed_at=now,
        )
        with self._evidence_lock:
            result.evidence_id = self.evidence.record_motion(
                command, report, result, operator="operator"
            )
        self._idempotency[command.idempotency_key] = (command.payload_hash(), result)
        return result

    # ---- validation ----

    def validate(self, command: MotionCommand, now: float | None = None):
        # Validation and the write share one authority so holder/deadman/stop
        # checks cannot diverge between the two steps.
        return self.hardware_authority.validate_arm(command, now)

    def _check_kind(self, command: MotionCommand) -> None:
        if command.kind == "JOINT_STEP":
            if command.profile != "step":
                raise MotionRejected(ErrorCode.UNKNOWN_PROFILE, "M5.2 step requires profile=step")
            if len(command.joint_names) != 1 or len(command.target_positions) != 1:
                raise MotionRejected(ErrorCode.INVALID_COMMAND, "M5.2 step requires one joint")
            return
        if command.kind == "GRIPPER":
            if command.profile != "gripper" or command.gripper is None:
                raise MotionRejected(ErrorCode.INVALID_COMMAND, "M5.2 gripper command is incomplete")
            return
        raise MotionRejected(ErrorCode.UNKNOWN_KIND, "M5.2 only supports JOINT_STEP and GRIPPER")

    # ---- execution ----

    def execute(self, command: MotionCommand, now: float | None = None) -> MotionResult:
        now = self._now(now)
        cached = self._idempotency.get(command.idempotency_key)
        if cached is not None:
            cached_hash, result = cached
            if cached_hash != command.payload_hash():
                return self._rejected(
                    command,
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    "idempotency key reused with a different payload",
                    now,
                )
            return result
        with self._active_lock:
            if self._active is not None:
                return self._rejected(command, ErrorCode.MOTION_BUSY.value, "another motion is active", now)
            try:
                self._rate_limit(command.source, now)
            except MotionRejected as exc:
                return self._rejected(command, exc.code, exc.message, now)
            report = self.validate(command, now)
            if not report.approved:
                return self._rejected(
                    command,
                    report.reason_code,
                    report.violations[0] if report.violations else "validation failed",
                    now,
                )
            try:
                self._check_kind(command)
            except MotionRejected as exc:
                return self._rejected(command, exc.code, exc.message, now)
            try:
                self.safety.begin_motion()
            except MotionRejected as exc:
                return self._rejected(command, exc.code, exc.message, now)
            self._active = command
            self._stop_event.clear()

        try:
            duration = float(command.duration_s or 2.0)
            start_pulses = self.bridge.pulse_snapshot()
            # The stop path sets the stop event and publishes the hold under
            # this same lock. A stop that lands before this send makes the
            # check below win and no command is published; a stop that lands
            # after the send orders its hold after the command write.
            with self._active_lock:
                if self._stop_event.is_set():
                    self.hardware_authority.hold_arm()
                    self.safety.end_motion()
                    return self._complete(
                        command,
                        report,
                        self.clock.now(),
                        outcome="ABORTED",
                        code=ErrorCode.SAFETY_STOP_LATCHED.value,
                        message="motion aborted before the bridge send",
                        bus_verified=False,
                        measured_positions=list(self.bridge.state().positions),
                    )
                try:
                    target_pulse = self.hardware_authority.execute_arm(command)
                except MotionRejected as exc:
                    # The authority re-checked contract/lease/holder/deadman,
                    # stop latch and limits immediately before the write and
                    # refused it; latch so no later frame slips through.
                    self.safety.latch(exc.code, exc.message)
                    self.hardware_authority.hold_arm()
                    self.safety.end_motion()
                    return self._complete(
                        command,
                        report,
                        self.clock.now(),
                        outcome="ABORTED",
                        code=exc.code,
                        message=exc.message,
                        bus_verified=False,
                        measured_positions=list(self.bridge.state().positions),
                    )
                if command.kind == "GRIPPER" and command.gripper is not None:
                    duration = float(command.gripper.duration_s or duration)
            deadline = self.clock.now() + duration + self.settle_s
            while self.clock.now() < deadline:
                if self._stop_event.is_set():
                    break
                time.sleep(min(0.01, max(0.0, deadline - self.clock.now())))
            if self._stop_event.is_set():
                self.hardware_authority.hold_arm()
                self.safety.end_motion()
                return self._complete(
                    command,
                    report,
                    self.clock.now(),
                    outcome="ABORTED",
                    code=ErrorCode.SAFETY_STOP_LATCHED.value,
                    message="motion aborted by software stop",
                    bus_verified=False,
                    measured_positions=list(self.bridge.state().positions),
                )
            try:
                sample = self.bridge.read_bus(timeout_s=1.0)
            except Exception as exc:  # noqa: BLE001 - bridge boundary
                self.safety.latch(ErrorCode.MOTION_OUTCOME_UNKNOWN, str(exc))
                self.hardware_authority.hold_arm()
                self.safety.end_motion()
                return self._complete(
                    command,
                    report,
                    self.clock.now(),
                    outcome="UNKNOWN",
                    code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                    message=f"bus read failed after motion: {exc}",
                    bus_verified=False,
                    measured_positions=list(self.bridge.state().positions),
                )
            measured = sample.pulses.get(
                self._target_joint_name(command),
            )
            if measured is None:
                self.safety.latch(ErrorCode.MOTION_OUTCOME_UNKNOWN, "target joint missing from bus read")
                self.hardware_authority.hold_arm()
                self.safety.end_motion()
                return self._complete(
                    command,
                    report,
                    self.clock.now(),
                    outcome="UNKNOWN",
                    code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                    message="target joint missing from bus read",
                    bus_verified=False,
                    measured_positions=list(sample.state.positions),
                )
            if command.kind == "GRIPPER":
                # The vendor gripper is contact/stall-limited. The baseline
                # treats a gripper-only command as timed completion and uses
                # the bus read as evidence; it does not require exact target
                # equality. Require a real movement in the commanded direction.
                start = start_pulses.get("r_joint")
                expected_delta = 0 if start is None else int(target_pulse) - int(start)
                actual_delta = 0 if start is None else int(measured) - int(start)
                if (
                    start is None
                    or expected_delta == 0
                    or abs(actual_delta) < self.min_gripper_delta_pulses
                    or (actual_delta > 0) != (expected_delta > 0)
                ):
                    message = (
                        "gripper did not move in the commanded direction: "
                        f"start={start} target={target_pulse} measured={measured}"
                    )
                    self.safety.latch(ErrorCode.MOTION_OUTCOME_UNKNOWN, message)
                    self.hardware_authority.hold_arm()
                    self.safety.end_motion()
                    return self._complete(
                        command,
                        report,
                        self.clock.now(),
                        outcome="UNKNOWN",
                        code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                        message=message,
                        bus_verified=False,
                        measured_positions=list(sample.state.positions),
                    )
                completion_message = (
                    "gripper timed completion with bus evidence: "
                    f"start={start} target={target_pulse} measured={measured}"
                )
            else:
                if abs(int(measured) - int(target_pulse)) > self.tolerance_pulses:
                    self.safety.latch(
                        ErrorCode.MOTION_OUTCOME_UNKNOWN,
                        f"bus target {measured} != commanded {target_pulse}",
                    )
                    self.hardware_authority.hold_arm()
                    self.safety.end_motion()
                    return self._complete(
                        command,
                        report,
                        self.clock.now(),
                        outcome="UNKNOWN",
                        code=ErrorCode.MOTION_OUTCOME_UNKNOWN.value,
                        message=f"bus target {measured} != commanded {target_pulse}",
                        bus_verified=False,
                        measured_positions=list(sample.state.positions),
                    )
                completion_message = f"bus verified target {target_pulse}"
            self.safety.end_motion()
            return self._complete(
                command,
                report,
                self.clock.now(),
                outcome="COMPLETED",
                code=ErrorCode.OK.value,
                message=completion_message,
                bus_verified=True,
                measured_positions=list(sample.state.positions),
            )
        finally:
            with self._active_lock:
                self._active = None

    def _target_joint_name(self, command: MotionCommand) -> str:
        if command.kind == "JOINT_STEP":
            return command.joint_names[0]
        return "r_joint"

    @property
    def active(self) -> bool:
        with self._active_lock:
            return self._active is not None

    # ---- stop/lifecycle ----

    def stop(self, operator_id: str, reason: str, now: float | None = None) -> StopResult:
        now = self._now(now)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        # Same lock as the send. The stop either lands before the send check
        # (and the command is not published) or after the command write (and
        # this hold follows it).
        with self._active_lock:
            self._stop_event.set()
            result = self.hardware_authority.stop(operator_id, reason, now)
        self.last_stop_latency_ms = self.hardware_authority.last_stop_latency_ms
        with self._evidence_lock:
            self.evidence.record_event(
                "software_stop",
                operator_id=operator_id,
                reason=reason,
                hold_latency_ms=(
                    round(self.last_stop_latency_ms, 3)
                    if self.last_stop_latency_ms is not None
                    else None
                ),
            )
        # stop() returns after the hold is published; the worker records the
        # terminal ABORTED later. Do not claim S2 from this call.
        return result

    def tick(self, now: float | None = None) -> None:
        now = self._now(now)
        self.authority.expire(now)
        self.safety.tick(now)
        with self._active_lock:
            active = self._active
        if active is None:
            return
        lease = self.authority.check(
            active.lease_id,
            self.scope_for_kind(active.kind),
            now,
            holder=active.holder or None,
        )
        if not lease.ok:
            with self._active_lock:
                self._stop_event.set()
                self.safety.latch(lease.code, lease.message or "lease expired or revoked")
                self.hardware_authority.hold_arm()
