"""ROS-free base (chassis) intent state machine.

``BaseAuthority`` turns a stream of :class:`BaseIntent` messages into bounded
:class:`BaseCommand` duty frames. It is deliberately independent of ROS and of
the arm executor. The only physical output is through the injected ``sink``;
an all-zero ``BaseCommand`` means "stop".

States::

    DISABLED  intake is off (default); any submit is rejected and output is 0
    IDLE      enabled, no accepted intent since restart/link recovery
    ARMED     enabled and a valid intent/lease was seen, but output is 0
    MOVING    a valid, fresh intent is being emitted
    STOP_LATCHED  a stop/lease/deadman failure latched; reset is required
    FAULT     unknown state, bad configuration or output failure

Fault behaviour (fail-closed):

* no lease, wrong holder/scope, expired/revoked lease, failed deadman, link
  loss, explicit stop, invalid contract or unknown state -> zero output;
* a stream timeout -> zero output and ``ARMED`` (the next valid intent may
  resume, nothing is silently replayed);
* duplicate/stale ``seq`` or command id -> rejected without a new output;
* a malformed/unknown intent -> zero output;
* restart -> zero output and no replay of the previous intent.

The 200 ms intent timeout is a provisional design value. It is configurable
and has not been validated as a safe stop latency on hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Any

from .authority import LeaseView
from .errors import ErrorCode, MotionRejected
from .models import ContractVersion
from .safety import SafetySupervisor
from .timebase import Clock

BASE_DUTY_CHANNELS = 4

DISABLED = "DISABLED"
IDLE = "IDLE"
ARMED = "ARMED"
MOVING = "MOVING"
STOP_LATCHED = "STOP_LATCHED"
FAULT = "FAULT"
KNOWN_STATES = {DISABLED, IDLE, ARMED, MOVING, STOP_LATCHED, FAULT}
# Mirrors the safety supervisor states; an unknown value is treated as a
# fault and latches the base on the next tick.
KNOWN_SAFETY_STATES = {"DISARMED", "ARMED_IDLE", "MOTION_ACTIVE", "STOP_LATCHED"}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass(frozen=True)
class BaseIntent:
    """One chassis intent frame (proposed ``BaseIntent`` contract shape)."""

    contract: ContractVersion
    lease_id: str
    holder: str
    command_id: str
    seq: int
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    duty_limit: float = 1.0
    deadman_ms: int = 0
    source: str = ""
    stamp_s: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BaseIntent":
        return cls(
            contract=ContractVersion.from_dict(value["contract"]),
            lease_id=str(value["lease_id"]),
            holder=str(value["holder"]),
            command_id=str(value["command_id"]),
            seq=int(value["seq"]),
            linear_x=float(value.get("linear_x", 0.0)),
            linear_y=float(value.get("linear_y", 0.0)),
            angular_z=float(value.get("angular_z", 0.0)),
            duty_limit=float(value.get("duty_limit", 1.0)),
            deadman_ms=int(value.get("deadman_ms", 0)),
            source=str(value.get("source", "")),
            stamp_s=float(value.get("stamp_s", 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract.to_dict(),
            "lease_id": self.lease_id,
            "holder": self.holder,
            "command_id": self.command_id,
            "seq": self.seq,
            "linear_x": self.linear_x,
            "linear_y": self.linear_y,
            "angular_z": self.angular_z,
            "duty_limit": self.duty_limit,
            "deadman_ms": self.deadman_ms,
            "source": self.source,
            "stamp_s": self.stamp_s,
        }


@dataclass(frozen=True)
class BaseCommand:
    """Four normalized motor duties; all-zero means stop."""

    duty: tuple[float, float, float, float]
    duration_s: float = 0.05
    command_id: str = ""
    seq: int = -1
    source: str = ""

    @classmethod
    def zero(cls) -> "BaseCommand":
        return cls(duty=(0.0, 0.0, 0.0, 0.0), duration_s=0.05, command_id="", seq=-1)

    @property
    def is_zero(self) -> bool:
        return all(value == 0.0 for value in self.duty)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duty": list(self.duty),
            "duration_s": self.duration_s,
            "command_id": self.command_id,
            "seq": self.seq,
            "source": self.source,
        }


@dataclass(frozen=True)
class BaseConfig:
    enabled: bool = False
    intent_timeout_ms: float = 200.0  # provisional; not verified on hardware
    max_duty_normalized: float = 0.10
    speed_scale_mps: float = 0.5
    track_half_width_m: float = 0.15
    command_period_s: float = 0.05

    def __post_init__(self) -> None:
        if self.intent_timeout_ms <= 0:
            raise ValueError("intent_timeout_ms must be positive")
        if not 0 < self.max_duty_normalized <= 1.0:
            raise ValueError("max_duty_normalized must be in (0, 1]")
        if self.speed_scale_mps <= 0:
            raise ValueError("speed_scale_mps must be positive")
        if self.track_half_width_m < 0:
            raise ValueError("track_half_width_m must be non-negative")
        if self.command_period_s <= 0:
            raise ValueError("command_period_s must be positive")


@dataclass(frozen=True)
class BaseDecision:
    accepted: bool
    code: str
    state: str
    message: str = ""
    command: BaseCommand = field(default_factory=BaseCommand.zero)


@dataclass(frozen=True)
class BaseStopOutcome:
    accepted: bool
    state: str
    reason: str
    requested_at: float
    command: BaseCommand = field(default_factory=BaseCommand.zero)


Sink = Callable[[BaseIntent | None, BaseCommand], None]


class BaseAuthority:
    def __init__(
        self,
        authority: LeaseView,
        clock: Clock,
        sink: Sink | None = None,
        config: BaseConfig | None = None,
        safety: SafetySupervisor | None = None,
        contract: ContractVersion | None = None,
    ) -> None:
        self.authority = authority
        self.clock = clock
        self.sink = sink
        self.config = config or BaseConfig()
        self.safety = safety
        self.contract = contract or authority.contract
        self._lock = threading.RLock()
        self.state = IDLE if self.config.enabled else DISABLED
        self.last_seq = -1
        self.last_command_id = ""
        self.last_intent_at: float | None = None
        self.last_reason = ""
        self.last_intent: BaseIntent | None = None
        self.last_command = BaseCommand.zero()
        self._zero_written = True
        self.events: list[dict[str, Any]] = []

    # ---- helpers ----

    def _now(self, now: float | None) -> float:
        return self.clock.now() if now is None else float(now)

    def _emit(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, "at": self.clock.now(), **fields})

    def _set_state(self, state: str) -> None:
        if state not in KNOWN_STATES:
            self.state = FAULT
            self.last_reason = "unknown_state"
            self._write(None, BaseCommand.zero(), "unknown_state")
            self._emit("base_fault", reason="unknown_state", requested=state)
            return
        self.state = state

    def _write(
        self, intent: BaseIntent | None, command: BaseCommand, reason: str
    ) -> None:
        self.last_command = command
        self._zero_written = command.is_zero
        if self.sink is None:
            return
        try:
            self.sink(intent, command)
        except Exception as exc:  # noqa: BLE001 - output boundary
            self.state = FAULT
            self.last_reason = f"output_failed:{reason}"
            if self.safety is not None and not self.safety.stop_latched:
                self.safety.latch(
                    ErrorCode.HARDWARE_UNAVAILABLE,
                    f"base output failed during {reason}: {exc}",
                )
            self._emit("base_output_failed", reason=reason, error=str(exc))
            raise MotionRejected(
                ErrorCode.HARDWARE_UNAVAILABLE, f"base output failed: {exc}"
            ) from exc

    def _zero(self, reason: str, intent: BaseIntent | None = None) -> None:
        # Always emit the zero frame on a stop/fault transition. Re-writing an
        # already-zero frame is harmless and makes the invariant observable.
        self._write(intent, BaseCommand.zero(), reason)

    def _latch(self, code: str, message: str) -> None:
        self.state = STOP_LATCHED
        self.last_reason = code
        # Latch the shared safety state before attempting the zero output: a
        # failing output must not leave the stop unlatched.
        if self.safety is not None and not self.safety.stop_latched:
            self.safety.latch(code, message)
        self._zero(code)
        self._emit("base_stop_latched", code=code, message=message)

    def _fault(self, code: str, message: str) -> None:
        self.state = FAULT
        self.last_reason = code
        self._zero(code)
        self._emit("base_fault", code=code, message=message)

    def _validate_intent(self, intent: BaseIntent) -> str:
        if not isinstance(intent, BaseIntent):
            return "intent must be BaseIntent"
        if not intent.lease_id.strip():
            return "lease_id is required"
        if not intent.holder.strip():
            return "holder is required"
        if not intent.command_id.strip():
            return "command_id is required"
        if not isinstance(intent.contract, ContractVersion):
            return "contract is required"
        if not isinstance(intent.seq, int) or intent.seq < 0:
            return "seq must be a non-negative integer"
        for name, value in (
            ("linear_x", intent.linear_x),
            ("linear_y", intent.linear_y),
            ("angular_z", intent.angular_z),
        ):
            if not _finite(value):
                return f"{name} must be finite"
        if not _finite(intent.duty_limit) or not 0 <= intent.duty_limit <= 1:
            return "duty_limit must be in [0, 1]"
        if not isinstance(intent.deadman_ms, int) or not 0 <= intent.deadman_ms <= 60_000:
            return "deadman_ms must be 0..60000"
        return ""

    def _mix_duty(self, intent: BaseIntent) -> tuple[float, float, float, float]:
        """Provisional mecanum mixer; not validated on hardware."""

        x = float(intent.linear_x)
        y = float(intent.linear_y)
        yaw = float(intent.angular_z) * self.config.track_half_width_m
        scale = self.config.speed_scale_mps
        raw = (
            (x - y - yaw) / scale,
            (x + y + yaw) / scale,
            (x + y - yaw) / scale,
            (x - y + yaw) / scale,
        )
        limit = self.config.max_duty_normalized * float(intent.duty_limit)
        return tuple(max(-limit, min(limit, value)) for value in raw)  # type: ignore[return-value]

    def _effective_timeout_s(self, intent: BaseIntent) -> float:
        timeout = self.config.intent_timeout_ms / 1000.0
        if intent.deadman_ms > 0:
            timeout = min(timeout, intent.deadman_ms / 1000.0)
        return timeout

    def _lease_check(
        self, intent: BaseIntent, now: float, touch: bool
    ) -> Any:
        return self.authority.check(
            intent.lease_id,
            "base",
            now,
            holder=intent.holder,
            touch=touch,
        )

    # ---- intake ----

    def submit(
        self, intent: BaseIntent, now: float | None = None
    ) -> BaseDecision:
        now = self._now(now)
        with self._lock:
            if self.state == DISABLED:
                self._zero("disabled")
                return BaseDecision(
                    False, ErrorCode.SYSTEM_DISARMED.value, self.state,
                    "base intake is disabled", BaseCommand.zero(),
                )
            if self.state == STOP_LATCHED:
                return BaseDecision(
                    False, ErrorCode.SAFETY_STOP_LATCHED.value, self.state,
                    "base stop is latched", BaseCommand.zero(),
                )
            if self.state == FAULT:
                return BaseDecision(
                    False, ErrorCode.SAFETY_FAULT.value, self.state,
                    "base intake is in fault", BaseCommand.zero(),
                )
            if self.safety is not None:
                safety_state = getattr(self.safety, "state", "")
                if self.safety.stop_latched or safety_state == "STOP_LATCHED":
                    self._latch(
                        ErrorCode.SAFETY_STOP_LATCHED.value, "safety stop is latched"
                    )
                    return BaseDecision(
                        False, ErrorCode.SAFETY_STOP_LATCHED.value, self.state,
                        "safety stop is latched", BaseCommand.zero(),
                    )
                if safety_state not in KNOWN_SAFETY_STATES:
                    self._latch(
                        ErrorCode.SAFETY_FAULT.value,
                        f"unknown shared safety state {safety_state!r}",
                    )
                    return BaseDecision(
                        False, ErrorCode.SAFETY_FAULT.value, self.state,
                        f"unknown shared safety state {safety_state!r}",
                        BaseCommand.zero(),
                    )
            problem = self._validate_intent(intent)
            if problem:
                if self.state == MOVING:
                    self._zero("invalid_intent")
                    self.state = ARMED
                self.last_reason = ErrorCode.INVALID_COMMAND.value
                self._emit("base_intent_rejected", reason=problem)
                return BaseDecision(
                    False, ErrorCode.INVALID_COMMAND.value, self.state,
                    problem, BaseCommand.zero(),
                )
            mismatch = intent.contract.mismatch(self.contract)
            if mismatch:
                self._fault(mismatch[0], mismatch[1])
                return BaseDecision(
                    False, mismatch[0], self.state, mismatch[1], BaseCommand.zero()
                )
            if intent.seq <= self.last_seq or intent.command_id == self.last_command_id:
                self._emit(
                    "base_stale_intent",
                    seq=intent.seq,
                    last_seq=self.last_seq,
                    command_id=intent.command_id,
                )
                return BaseDecision(
                    False, ErrorCode.INVALID_COMMAND.value, self.state,
                    "stale or duplicate base intent", self.last_command,
                )
            lease = self._lease_check(intent, now, touch=True)
            if not lease.ok:
                self._latch(lease.code, lease.message or "base lease is not valid")
                return BaseDecision(
                    False, lease.code, self.state,
                    lease.message or "base lease is not valid", BaseCommand.zero(),
                )
            duty = self._mix_duty(intent)
            command = BaseCommand(
                duty=duty,
                duration_s=self.config.command_period_s,
                command_id=intent.command_id,
                seq=intent.seq,
                source=intent.source,
            )
            self.last_seq = intent.seq
            self.last_command_id = intent.command_id
            self.last_intent_at = now
            self.last_intent = intent
            self.state = MOVING if not command.is_zero else ARMED
            self.last_reason = "" if not command.is_zero else "zero_intent"
            self._write(intent, command, "intent")
            self._emit(
                "base_intent",
                seq=intent.seq,
                command_id=intent.command_id,
                duty=list(duty),
                state=self.state,
            )
            return BaseDecision(True, ErrorCode.OK.value, self.state, "accepted", command)

    # ---- time-based supervision ----

    def tick(self, now: float | None = None) -> BaseDecision | None:
        now = self._now(now)
        with self._lock:
            if self.state in (DISABLED, STOP_LATCHED, FAULT):
                self._zero("terminal_state")
                return None
            # The base shares the system safety latch: an arm stop/fault must
            # zero the base even when no base intent is currently active.
            if self.safety is not None:
                safety_state = getattr(self.safety, "state", "")
                if self.safety.stop_latched or safety_state == "STOP_LATCHED":
                    self._latch(
                        ErrorCode.SAFETY_STOP_LATCHED.value,
                        "shared safety stop is latched",
                    )
                    return BaseDecision(
                        False,
                        ErrorCode.SAFETY_STOP_LATCHED.value,
                        self.state,
                        "shared safety stop is latched; base output is zero",
                        BaseCommand.zero(),
                    )
                if safety_state not in KNOWN_SAFETY_STATES:
                    self._latch(
                        ErrorCode.SAFETY_FAULT.value,
                        f"unknown shared safety state {safety_state!r}",
                    )
                    return BaseDecision(
                        False,
                        ErrorCode.SAFETY_FAULT.value,
                        self.state,
                        f"unknown shared safety state {safety_state!r}",
                        BaseCommand.zero(),
                    )
            intent = self.last_intent
            if intent is None:
                return None
            lease = self._lease_check(intent, now, touch=False)
            if not lease.ok:
                self._latch(lease.code, lease.message or "base lease lost")
                return BaseDecision(
                    False, lease.code, self.state,
                    lease.message or "base lease lost", BaseCommand.zero(),
                )
            if self.state == MOVING and self.last_intent_at is not None:
                if now - self.last_intent_at > self._effective_timeout_s(intent):
                    self._zero("intent_timeout")
                    self.state = ARMED
                    self.last_reason = "INTENT_TIMEOUT"
                    self._emit("base_intent_timeout", seq=intent.seq)
                    return BaseDecision(
                        True, "INTENT_TIMEOUT", self.state,
                        "base intent stream timed out; output is zero",
                        BaseCommand.zero(),
                    )
            return None

    # ---- lifecycle ----

    def stop(
        self,
        operator_id: str,
        reason: str,
        now: float | None = None,
        latch_safety: bool = True,
    ) -> BaseStopOutcome:
        now = self._now(now)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        with self._lock:
            self.state = STOP_LATCHED
            self.last_reason = ErrorCode.SAFETY_STOP_LATCHED.value
            if self.safety is not None and latch_safety and not self.safety.stop_latched:
                self.safety.latch(
                    ErrorCode.SAFETY_STOP_LATCHED,
                    f"base stop by {operator_id}: {reason}",
                )
            self._zero("stop")
            self._emit("base_stop", operator_id=operator_id, reason=reason)
        return BaseStopOutcome(True, self.state, reason, now)

    def reset(self, operator_id: str, reason: str) -> BaseStopOutcome:
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        with self._lock:
            if self.state in (STOP_LATCHED, FAULT):
                self.state = IDLE if self.config.enabled else DISABLED
                self.last_reason = ""
                self.last_intent_at = None
                self._zero("reset")
                self._emit("base_reset", operator_id=operator_id, reason=reason)
        return BaseStopOutcome(True, self.state, reason, self.clock.now())

    def link_down(self, reason: str = "LINK_DOWN") -> None:
        with self._lock:
            self._zero("link_down")
            self.last_intent_at = None
            self.last_reason = reason
            self.state = IDLE if self.config.enabled else DISABLED
            self._emit("base_link_down", reason=reason)

    def link_up(self) -> None:
        with self._lock:
            if self.state in (STOP_LATCHED, FAULT, DISABLED):
                return
            # Recovery starts from zero; a new valid intent is required.
            self._zero("link_up")
            self.state = IDLE
            self.last_intent_at = None
            self._emit("base_link_up")

    def restart(self) -> None:
        """Simulate a system restart: zero output, no intent replay."""

        with self._lock:
            self._zero("restart")
            self.last_seq = -1
            self.last_command_id = ""
            self.last_intent_at = None
            self.last_intent = None
            self.last_reason = "restarted"
            self.state = IDLE if self.config.enabled else DISABLED
            self._emit("base_restart")

    def restore_state(self, state: str) -> None:
        """Restore a persisted state without resuming motion.

        Unknown values fail closed (``FAULT``). A persisted ``MOVING``/``ARMED``
        is not trusted after a restart: the output is zeroed and the intake
        returns to ``IDLE``; a new valid intent and lease are required.
        """

        with self._lock:
            self._zero("restore")
            if state not in KNOWN_STATES:
                self._set_state(state)
                return
            if state in (MOVING, ARMED):
                self.state = IDLE if self.config.enabled else DISABLED
                self.last_reason = "restored_to_idle"
                self._emit("base_restored_to_idle", requested=state)
                return
            self.state = state

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "enabled": self.config.enabled,
                "last_seq": self.last_seq,
                "last_command_id": self.last_command_id,
                "last_reason": self.last_reason,
                "last_intent_at": self.last_intent_at,
                "output": self.last_command.to_dict(),
                "intent_timeout_ms": self.config.intent_timeout_ms,
            }


def base_sink_writer(
    submit: Callable[[BaseIntent, BaseCommand], None],
    zero: Callable[[], None],
) -> Sink:
    """Adapt ``HardwareAuthority.submit_base``/``zero_base`` to a base sink.

    A zero command is a stop and is sent through the stop output even when the
    causing intent/lease is no longer valid; a non-zero command must carry its
    intent so the hardware authority can re-check contract/lease/holder.
    """

    def sink(intent: BaseIntent | None, command: BaseCommand) -> None:
        if command.is_zero or intent is None:
            zero()
            return
        submit(intent, command)

    return sink
