"""Single logical hardware exit for the JCHENG system.

Every arm or base write goes through :class:`HardwareAuthority`, which holds
the bridge. Before a bridge write it checks, in order:

1. the caller's contract version matches the deployed contract;
2. the lease exists, is active, is held by the asserted holder and covers the
   requested scope;
3. the deadman window is satisfied for deadman leases;
4. the safety supervisor is not stop-latched and is not in an unknown state;
5. the publisher guard and the kind/limit validation pass.

Anything unknown or unsupported fails closed: no bridge write is attempted and
a ``MotionRejected`` is raised (or ``False`` returned on the stop-output path
so a stop never becomes unreachable because an output is missing).

This module is ROS-free. ROS nodes should pass a thin bridge object; the
vendor node passes ``VendorBridge`` and the mock stack passes
``MockHardwareBridge``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Any

from .authority import LeaseCheck, LeaseView
from .base import BASE_DUTY_CHANNELS, BaseCommand, BaseIntent
from .errors import ErrorCode, MotionRejected
from .guard import PublisherGuard
from .models import ContractVersion, MotionCommand, SafetyState, StopResult
from .safety import SafetySupervisor
from .timebase import Clock

KNOWN_SAFETY_STATES = {"DISARMED", "ARMED_IDLE", "MOTION_ACTIVE", "STOP_LATCHED"}
ARM_KINDS = {"JOINT_STEP", "GRIPPER"}


@dataclass(frozen=True)
class HardwareDecision:
    """Non-raising decision record used by ``status`` and tests."""

    ok: bool
    code: str
    message: str = ""
    scope: str = ""


class HardwareAuthority:
    def __init__(
        self,
        contract: ContractVersion,
        authority: LeaseView,
        safety: SafetySupervisor,
        clock: Clock,
        bridge: Any,
        guard: PublisherGuard | None = None,
        max_base_duty: float = 0.10,
        base_writes_enabled: bool = False,
    ) -> None:
        self.contract = contract
        self.authority = authority
        self.safety = safety
        self.clock = clock
        self.bridge = bridge
        self.guard = guard or PublisherGuard()
        self.max_base_duty = float(max_base_duty)
        self.base_writes_enabled = bool(base_writes_enabled)
        self._lock = threading.RLock()
        self._last_base_duty = [0.0] * BASE_DUTY_CHANNELS
        self.last_stop_latency_ms: float | None = None
        self.last_stop_outputs = {"arm_hold": False, "base_zero": False}

    # ---- helpers ----

    def _now(self, now: float | None) -> float:
        return self.clock.now() if now is None else float(now)

    @staticmethod
    def scope_for_kind(kind: str) -> str:
        if kind == "GRIPPER":
            return "gripper"
        if kind == "BASE_PULSE":
            return "base"
        return "arm"

    def _check_contract(self, contract: ContractVersion) -> None:
        if not isinstance(contract, ContractVersion):
            raise MotionRejected(ErrorCode.CONTRACT_UNKNOWN, "contract is required")
        mismatch = contract.mismatch(self.contract)
        if mismatch:
            raise MotionRejected(mismatch[0], mismatch[1])

    def _require_bridge(self) -> Any:
        if self.bridge is None:
            raise MotionRejected(
                ErrorCode.HARDWARE_UNAVAILABLE, "hardware bridge is not configured"
            )
        return self.bridge

    def _check_safety_state(self) -> None:
        state = getattr(self.safety, "state", "")
        if state not in KNOWN_SAFETY_STATES:
            self.safety.latch(
                ErrorCode.SAFETY_FAULT, f"unknown safety state {state!r}"
            )
            raise MotionRejected(
                ErrorCode.SAFETY_FAULT, f"unknown safety state {state!r}"
            )

    # ---- arm ----

    def validate_arm(
        self, command: MotionCommand, now: float | None = None
    ) -> Any:
        """Guard + lease + safety/limit validation for an arm command.

        This is the same validation the executors used before the authority
        existed, with holder and deadman enforcement added.
        """

        now = self._now(now)
        with self._lock:
            # Contract mismatch is reported by safety.validate as a rejected
            # report, preserving the executor's existing HTTP result shape.
            self._check_safety_state()
            bridge = self._require_bridge()
            guard = self.guard.enforce(
                self.safety, getattr(bridge, "publishers", {"jcheng_hardware"})
            )
            lease = self.authority.check(
                command.lease_id,
                self.scope_for_kind(command.kind),
                now,
                holder=command.holder or None,
                touch=True,
            )
            if not guard.ok:
                return self.safety.validate(
                    command, False, ErrorCode.SAFETY_FAULT.value, now
                )
            return self.safety.validate(command, lease.ok, lease.code, now)

    def execute_arm(self, command: MotionCommand, now: float | None = None) -> int:
        """Validate and then write one JOINT_STEP or GRIPPER target."""

        now = self._now(now)
        with self._lock:
            report = self.validate_arm(command, now)
            if not report.approved:
                message = (
                    report.violations[0]
                    if report.violations
                    else "arm command rejected before the bridge write"
                )
                raise MotionRejected(report.reason_code, message)
            if command.kind == "JOINT_STEP":
                if len(command.joint_names) != 1 or len(command.target_positions) != 1:
                    raise MotionRejected(
                        ErrorCode.INVALID_COMMAND,
                        "hardware authority supports one joint per JOINT_STEP",
                    )
                send = getattr(self.bridge, "send_step", None)
                if not callable(send):
                    raise MotionRejected(
                        ErrorCode.HARDWARE_UNAVAILABLE,
                        "bridge has no send_step output",
                    )
                try:
                    return int(
                        send(
                            command.joint_names[0],
                            float(command.target_positions[0]),
                            float(command.duration_s or 0.0),
                        )
                    )
                except MotionRejected:
                    raise
                except Exception as exc:  # noqa: BLE001 - bridge boundary
                    self.safety.latch(
                        ErrorCode.SAFETY_FAULT, f"arm write failed: {exc}"
                    )
                    raise MotionRejected(
                        ErrorCode.HARDWARE_UNAVAILABLE, f"arm write failed: {exc}"
                    ) from exc
            if command.kind == "GRIPPER":
                if command.gripper is None:
                    raise MotionRejected(
                        ErrorCode.INVALID_COMMAND, "gripper command is missing"
                    )
                send = getattr(self.bridge, "send_gripper", None)
                if not callable(send):
                    raise MotionRejected(
                        ErrorCode.HARDWARE_UNAVAILABLE,
                        "bridge has no send_gripper output",
                    )
                try:
                    return int(
                        send(
                            float(command.gripper.position_rad),
                            float(command.gripper.duration_s or command.duration_s or 0.0),
                        )
                    )
                except MotionRejected:
                    raise
                except Exception as exc:  # noqa: BLE001 - bridge boundary
                    self.safety.latch(
                        ErrorCode.SAFETY_FAULT, f"gripper write failed: {exc}"
                    )
                    raise MotionRejected(
                        ErrorCode.HARDWARE_UNAVAILABLE,
                        f"gripper write failed: {exc}",
                    ) from exc
            raise MotionRejected(
                ErrorCode.UNKNOWN_KIND,
                f"{command.kind!r} is not an arm hardware command",
            )

    # ---- base ----

    def submit_base(
        self,
        intent: BaseIntent,
        command: BaseCommand,
        now: float | None = None,
    ) -> None:
        """Validate and publish one base duty frame.

        The base write path is disabled unless ``base_writes_enabled`` is set,
        matching the default-closed base intake. A stop/zero frame still
        reaches :meth:`stop`/:meth:`zero_base` even when writes are disabled.
        """

        now = self._now(now)
        with self._lock:
            if not self.base_writes_enabled:
                raise MotionRejected(
                    ErrorCode.SYSTEM_DISARMED,
                    "base hardware writes are disabled",
                )
            self._check_contract(intent.contract)
            self._check_safety_state()
            if self.safety.stop_latched:
                raise MotionRejected(
                    ErrorCode.SAFETY_STOP_LATCHED, "safety stop is latched"
                )
            lease = self.authority.check(
                intent.lease_id, "base", now, holder=intent.holder, touch=True
            )
            self._require_base_lease(lease)
            duty = [float(value) for value in command.duty]
            self._check_base_duty(duty)
            publish = getattr(self.bridge, "publish_motor_speeds", None)
            if not callable(publish):
                raise MotionRejected(
                    ErrorCode.HARDWARE_UNAVAILABLE,
                    "bridge has no publish_motor_speeds output",
                )
            try:
                publish(duty)
            except MotionRejected:
                raise
            except Exception as exc:  # noqa: BLE001 - bridge boundary
                self.safety.latch(
                    ErrorCode.SAFETY_FAULT, f"base write failed: {exc}"
                )
                raise MotionRejected(
                    ErrorCode.HARDWARE_UNAVAILABLE, f"base write failed: {exc}"
                ) from exc
            self._last_base_duty = list(duty)

    def _require_base_lease(self, lease: LeaseCheck) -> None:
        if not lease.ok:
            raise MotionRejected(lease.code, lease.message or "base lease is not valid")

    def _check_base_duty(self, duty: list[float]) -> None:
        if len(duty) != BASE_DUTY_CHANNELS:
            raise MotionRejected(
                ErrorCode.INVALID_COMMAND, "base output requires four channels"
            )
        for value in duty:
            if not math.isfinite(value):
                raise MotionRejected(
                    ErrorCode.INVALID_COMMAND, "base duty must be finite"
                )
            if abs(value) > self.max_base_duty + 1e-9:
                raise MotionRejected(
                    ErrorCode.VELOCITY_LIMIT,
                    f"base duty {value:.3f} exceeds limit {self.max_base_duty:.3f}",
                )

    # ---- stop outputs ----

    def hold_arm(self, now: float | None = None) -> bool:
        """Hold the arm at the last measured position, if the bridge can."""

        with self._lock:
            hold = getattr(self.bridge, "hold", None)
            if not callable(hold):
                self.last_stop_outputs["arm_hold"] = False
                return False
            try:
                hold()
                self.last_stop_outputs["arm_hold"] = True
                return True
            except Exception:  # noqa: BLE001 - stop path must continue
                self.last_stop_outputs["arm_hold"] = False
                return False

    def zero_base(self, now: float | None = None) -> bool:
        """Publish an all-zero base duty frame, if the bridge can."""

        zeros = [0.0] * BASE_DUTY_CHANNELS
        with self._lock:
            publish = getattr(self.bridge, "publish_motor_speeds", None)
            if callable(publish):
                try:
                    publish(zeros)
                    self._last_base_duty = list(zeros)
                    self.last_stop_outputs["base_zero"] = True
                    return True
                except Exception:  # noqa: BLE001 - stop path must continue
                    pass
            zero = getattr(self.bridge, "zero_base", None)
            if callable(zero):
                try:
                    zero()
                    self._last_base_duty = list(zeros)
                    self.last_stop_outputs["base_zero"] = True
                    return True
                except Exception:  # noqa: BLE001 - stop path must continue
                    pass
            self.last_stop_outputs["base_zero"] = False
            return False

    def stop(
        self,
        operator_id: str,
        reason: str,
        now: float | None = None,
    ) -> StopResult:
        """Latched software stop covering both arm and base outputs.

        This never requires a lease or a valid contract: a stop must remain
        reachable when ordinary motion authority is lost.
        """

        now = self._now(now)
        if not operator_id.strip():
            raise MotionRejected(ErrorCode.INVALID_COMMAND, "operator_id is required")
        with self._lock:
            self.safety.latch(
                ErrorCode.SAFETY_STOP_LATCHED,
                f"software stop by {operator_id}: {reason}",
            )
            started = time.monotonic()
            self.hold_arm(now)
            self.zero_base(now)
            self.last_stop_latency_ms = (time.monotonic() - started) * 1000.0
        return StopResult(
            accepted=True,
            safety=self.safety.current_state(),
            message="software stop requested; not an emergency stop",
            requested_at=now,
            confirmation="request_accepted",
        )

    # ---- observability ----

    def status(self) -> dict[str, Any]:
        with self._lock:
            state: SafetyState = self.safety.current_state()
            return {
                "bridge": type(self.bridge).__name__ if self.bridge is not None else "",
                "base_writes_enabled": self.base_writes_enabled,
                "last_base_duty": list(self._last_base_duty),
                "stop_latched": bool(state.stop_latched),
                "safety_state": state.state,
                "last_stop_latency_ms": self.last_stop_latency_ms,
                "last_stop_outputs": dict(self.last_stop_outputs),
            }
