"""ROS-free gated intake for the provisional mobile base intent envelope.

The envelope is the mobile-side provisional JSON shape
``jcheng.mobile.base_intent.v1`` (see mobile ``intent.py`` ``to_dict``)::

    {
      "schema": "jcheng.mobile.base_intent.v1",
      "lease_id": str,
      "holder": str,
      "command_id": str,
      "seq": int,
      "stamp_ns": int,          // monotonic timestamp, not a header stamp
      "frame": "base_link",
      "linear": {"x": float, "y": float},
      "angular": {"z": float},
      "duty_limit": float       // normalized 0..1
    }

This is a repo-local, test-only envelope: it is **not** a
``jcheng-interfaces`` contract. The intake maps it to :class:`BaseIntent` with
the deployed (local) contract; the envelope itself carries no contract field.
Unknown schema/frame, malformed JSON, stale/future ``stamp_ns``, or a lease,
holder, scope, deadman or stop failure are all rejected without a hardware
write. ``duty_limit`` is clamped to 0..1; zero duty is a stop frame.

The 200 ms freshness window is provisional and has not been validated on
hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from typing import Any

from .base import BaseAuthority, BaseCommand, BaseDecision, BaseIntent
from .errors import ErrorCode, MotionRejected
from .models import ContractVersion
from .timebase import Clock

BASE_INTENT_SCHEMA = "jcheng.mobile.base_intent.v1"
BASE_INTENT_TOPIC = "/jcheng/intent/base"
BASE_INTENT_DOMAIN_ID = 42
BASE_INTENT_FRAME = "base_link"
BASE_INTENT_SOURCE = "jcheng-mobile"

# Provisional freshness window; not a validated safety limit.
DEFAULT_MAX_AGE_NS = 200_000_000
DEFAULT_MAX_FUTURE_NS = 50_000_000

DISABLED = "DISABLED"
WRONG_DOMAIN = "WRONG_DOMAIN"
INVALID_ENVELOPE = "INVALID_ENVELOPE"
STALE_TIMESTAMP = "STALE_TIMESTAMP"
FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"


class IntakeRejected(Exception):
    """Envelope rejection with a canonical code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value: Any, field: str) -> float:
    if not _is_number(value) or not math.isfinite(float(value)):
        raise IntakeRejected(INVALID_ENVELOPE, f"{field} must be a finite number")
    return float(value)


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise IntakeRejected(
            INVALID_ENVELOPE, f"{field} must be an integer >= {minimum}"
        )
    return int(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeRejected(INVALID_ENVELOPE, f"{field} must be a non-empty string")
    return value


def parse_base_intent(
    payload: str | bytes,
    *,
    contract: ContractVersion,
    receive_time_ns: int,
    max_age_ns: int = DEFAULT_MAX_AGE_NS,
    max_future_ns: int = DEFAULT_MAX_FUTURE_NS,
) -> BaseIntent:
    """Parse and validate one mobile envelope; raise ``IntakeRejected``.

    ``receive_time_ns`` must come from the same monotonic clock domain as
    ``stamp_ns`` (``time.monotonic_ns()`` on the same host).
    """

    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntakeRejected(INVALID_ENVELOPE, "payload is not UTF-8") from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise IntakeRejected(INVALID_ENVELOPE, "payload must be a JSON string")

    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise IntakeRejected(INVALID_ENVELOPE, f"payload is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise IntakeRejected(INVALID_ENVELOPE, "envelope must be a JSON object")

    schema = value.get("schema")
    if schema != BASE_INTENT_SCHEMA:
        raise IntakeRejected(
            INVALID_ENVELOPE,
            f"schema must be {BASE_INTENT_SCHEMA!r}, got {schema!r}",
        )
    frame = value.get("frame")
    if frame != BASE_INTENT_FRAME:
        raise IntakeRejected(
            INVALID_ENVELOPE, f"frame must be {BASE_INTENT_FRAME!r}, got {frame!r}"
        )

    linear = value.get("linear")
    angular = value.get("angular")
    if not isinstance(linear, dict) or not isinstance(angular, dict):
        raise IntakeRejected(
            INVALID_ENVELOPE, "linear and angular must be JSON objects"
        )

    stamp_ns = _integer(value.get("stamp_ns"), "stamp_ns")
    if not isinstance(receive_time_ns, int) or isinstance(receive_time_ns, bool):
        raise IntakeRejected(INVALID_ENVELOPE, "receive_time_ns must be an integer")
    age_ns = int(receive_time_ns) - stamp_ns
    if age_ns > int(max_age_ns):
        raise IntakeRejected(
            STALE_TIMESTAMP,
            f"stamp_ns is {age_ns / 1e6:.1f} ms old (limit {int(max_age_ns) / 1e6:.1f} ms)",
        )
    if age_ns < -int(max_future_ns):
        raise IntakeRejected(
            FUTURE_TIMESTAMP,
            f"stamp_ns is {-age_ns / 1e6:.1f} ms in the future "
            f"(limit {int(max_future_ns) / 1e6:.1f} ms)",
        )

    duty_limit = _number(value.get("duty_limit"), "duty_limit")
    # Clamp rather than reject: a too-large cap is limited to the profile cap
    # and a negative cap becomes a stop/zero command.
    duty_limit = max(0.0, min(1.0, duty_limit))

    return BaseIntent(
        contract=contract,
        lease_id=_text(value.get("lease_id"), "lease_id"),
        holder=_text(value.get("holder"), "holder"),
        command_id=_text(value.get("command_id"), "command_id"),
        seq=_integer(value.get("seq"), "seq"),
        linear_x=_number(linear.get("x"), "linear.x"),
        linear_y=_number(linear.get("y"), "linear.y"),
        angular_z=_number(angular.get("z"), "angular.z"),
        duty_limit=duty_limit,
        deadman_ms=0,
        source=BASE_INTENT_SOURCE,
        stamp_s=stamp_ns / 1e9,
    )


@dataclass(frozen=True)
class BaseIntakeResult:
    accepted: bool
    code: str
    state: str = ""
    message: str = ""
    decision: BaseDecision | None = None


class BaseIntake:
    """Enabled/domain gate plus envelope parsing in front of ``BaseAuthority``."""

    def __init__(
        self,
        base: BaseAuthority,
        clock: Clock,
        *,
        contract: ContractVersion | None = None,
        enabled: bool = False,
        domain_id: int | None = None,
        required_domain_id: int = BASE_INTENT_DOMAIN_ID,
        topic: str = BASE_INTENT_TOPIC,
        max_age_ns: int = DEFAULT_MAX_AGE_NS,
        max_future_ns: int = DEFAULT_MAX_FUTURE_NS,
    ) -> None:
        self.base = base
        self.clock = clock
        self.contract = contract or base.contract
        self.topic = str(topic)
        self.required_domain_id = int(required_domain_id)
        self.domain_id = domain_id
        self.max_age_ns = int(max_age_ns)
        self.max_future_ns = int(max_future_ns)
        self.enabled, self.reason = self._gate(enabled, domain_id)
        self.counters = {"received": 0, "accepted": 0, "rejected": 0}
        self.last_result: BaseIntakeResult | None = None

    def _gate(self, enabled: bool, domain_id: int | None) -> tuple[bool, str]:
        if not enabled:
            return False, DISABLED
        if domain_id is None or int(domain_id) != self.required_domain_id:
            return False, WRONG_DOMAIN
        return True, "OK"

    def _result(
        self, accepted: bool, code: str, message: str, decision: BaseDecision | None = None
    ) -> BaseIntakeResult:
        result = BaseIntakeResult(
            accepted=accepted,
            code=code,
            state=getattr(self.base, "state", ""),
            message=message,
            decision=decision,
        )
        self.last_result = result
        if accepted:
            self.counters["accepted"] += 1
        else:
            self.counters["rejected"] += 1
        return result

    def handle(
        self, payload: str | bytes, receive_time_ns: int | None = None
    ) -> BaseIntakeResult:
        self.counters["received"] += 1
        if not self.enabled:
            detail = (
                "enabled=false"
                if self.reason == DISABLED
                else f"ROS domain {self.domain_id!r} is not {self.required_domain_id}"
            )
            return self._result(
                False, self.reason, f"base intake is disabled: {detail}"
            )
        receive = time.monotonic_ns() if receive_time_ns is None else int(receive_time_ns)
        try:
            intent = parse_base_intent(
                payload,
                contract=self.contract,
                receive_time_ns=receive,
                max_age_ns=self.max_age_ns,
                max_future_ns=self.max_future_ns,
            )
        except IntakeRejected as exc:
            return self._result(False, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - intake boundary fails closed
            return self._result(
                False, INVALID_ENVELOPE, f"envelope parsing failed closed: {exc}"
            )
        try:
            decision = self.base.submit(intent, now=self.clock.now())
        except MotionRejected as exc:
            return self._result(False, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - intake boundary fails closed
            return self._result(
                False,
                ErrorCode.SAFETY_FAULT.value,
                f"base submit failed closed: {exc}",
            )
        return self._result(
            decision.accepted, decision.code, decision.message, decision
        )

    def tick(self, now: float | None = None) -> BaseDecision | None:
        if not self.enabled:
            return None
        return self.base.tick(now)

    def stop(self, operator_id: str, reason: str, now: float | None = None):
        return self.base.stop(operator_id, reason, now)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "topic": self.topic,
            "domain_id": self.domain_id,
            "required_domain_id": self.required_domain_id,
            "schema": BASE_INTENT_SCHEMA,
            "counters": dict(self.counters),
            "last_result": None
            if self.last_result is None
            else {
                "accepted": self.last_result.accepted,
                "code": self.last_result.code,
                "state": self.last_result.state,
                "message": self.last_result.message,
            },
            "base": self.base.status(),
        }


class UnavailableBaseBridge:
    """Vendor-style bridge with no motor-speed output.

    The base intake node uses this until a reviewed base bridge exists. Every
    write raises ``HARDWARE_UNAVAILABLE`` instead of fabricating a duty frame.
    """

    publishers = {"jcheng_base"}

    def publish_motor_speeds(self, duty: list[float], now: float | None = None) -> None:
        raise MotionRejected(
            ErrorCode.HARDWARE_UNAVAILABLE,
            "vendor bridge has no motor-speed output",
        )

    def zero_base(self, now: float | None = None) -> None:
        raise MotionRejected(
            ErrorCode.HARDWARE_UNAVAILABLE,
            "vendor bridge has no motor-speed output",
        )
