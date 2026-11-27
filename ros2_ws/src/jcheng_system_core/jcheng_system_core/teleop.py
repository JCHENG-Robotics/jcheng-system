"""Teleoperation intake for the mock system (M2).

This is the system-side boundary that consumes a high-rate teleoperation
sample and turns it into bounded, lease-gated multi-joint steps through the
existing MotionArbiter. It does not add a second motion origin: every step
goes through the arbiter's lease, safety, guard and evidence path.

The M2 profile uses JOINT_STEP/profile=pose because the mock supports
multi-joint steps; the vendor executor does not (M6 remains blocked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import uuid

from .errors import ErrorCode, MotionRejected
from .models import ContractVersion, MotionCommand, MotionResult, StopResult
from .stack import MockStack


@dataclass(frozen=True)
class TeleopSample:
    seq: int
    lease_id: str
    source: str
    contract: ContractVersion
    mode: str
    joint_velocities_rad_s: tuple[float, ...]
    deadman: bool
    stamp_s: float
    received_s: float
    cartesian_linear: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cartesian_angular: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class TeleopIntake:
    stack: MockStack
    deadman_s: float = 0.3
    step_period_s: float = 0.1
    max_velocity_rad_s: float = 0.5
    max_stamp_skew_s: float = 0.5
    active: bool = False
    last_seq: int | None = None
    last_received_s: float | None = None
    last_tick_s: float | None = None
    latest: TeleopSample | None = None
    stop_reason: str = ""
    _counter: int = 0

    def _reject(self, code: ErrorCode | str, message: str) -> None:
        raise MotionRejected(code, message)

    def on_sample(self, sample: TeleopSample, now: float | None = None) -> None:
        now = self.stack.clock.now() if now is None else float(now)
        mismatch = sample.contract.mismatch(self.stack.version)
        if mismatch:
            self._reject(mismatch[0], mismatch[1])
        if sample.mode != "JOINT_VELOCITY":
            self._reject(ErrorCode.UNKNOWN_KIND, f"unsupported teleop mode {sample.mode!r}")
        if len(sample.joint_velocities_rad_s) != len(self.stack.profile.joint_names):
            self._reject(ErrorCode.INVALID_COMMAND, "joint velocity width does not match the profile")
        if not all(math.isfinite(float(value)) for value in sample.joint_velocities_rad_s):
            self._reject(ErrorCode.INVALID_COMMAND, "non-finite teleop velocity")
        if sample.stamp_s < 0 or not math.isfinite(sample.stamp_s):
            self._reject(ErrorCode.INVALID_COMMAND, "teleop stamp is invalid")
        # Liveness uses the caller's receipt time. The message stamp is for
        # provenance/latency; comparing it to the monotonic clock would reject
        # every ROS wall-clock stamp.
        if self.last_seq is not None and sample.seq <= self.last_seq:
            self._reject(ErrorCode.INVALID_COMMAND, "teleop seq must strictly increase")
        if not sample.deadman:
            self.abort("deadman_release")
            return
        lease = self.stack.authority.check(sample.lease_id, "arm", now)
        if not lease.ok:
            self._reject(lease.code, lease.message or "teleop lease is not valid")
        self.last_seq = sample.seq
        self.last_received_s = now
        self.latest = sample
        self.active = True
        self.stop_reason = ""

    def tick(self, now: float | None = None) -> MotionResult | None:
        now = self.stack.clock.now() if now is None else float(now)
        if not self.active or self.latest is None:
            return None
        if self.last_received_s is None or now - self.last_received_s > self.deadman_s:
            self.abort("teleop_input_stale")
            return None
        if self.last_tick_s is not None and now - self.last_tick_s < self.step_period_s:
            return None
        # Anchor on the actual tick time, not on a theoretical grid: adding the
        # period each time accumulates drift and skips a step when the timer
        # lands a few microseconds early.
        self.last_tick_s = now
        try:
            command = self._step_command(now)
        except MotionRejected as exc:
            self.stop_reason = exc.code
            self.active = False
            return None
        result = self.stack.arbiter.execute(command, now)
        if result.outcome != "COMPLETED":
            self.active = False
            self.stop_reason = result.reason_code
        return result

    def _step_command(self, now: float) -> MotionCommand:
        state = self.stack.hardware.state(now)
        if not state.fresh or state.truth != "measured":
            self._reject(ErrorCode.STATE_STALE, "measured state is unavailable")
        dt = self.step_period_s
        targets: list[float] = []
        for name, velocity in zip(self.stack.profile.joint_names, self.latest.joint_velocities_rad_s):
            limit = min(self.max_velocity_rad_s, self.stack.profile.joints[name].max_velocity_rad_s)
            step = max(-limit, min(limit, float(velocity))) * dt
            index = self.stack.profile.joint_order.index(name)
            targets.append(state.positions[index] + step)
        self._counter += 1
        return MotionCommand(
            intent_id=f"teleop-{self._counter:08d}",
            idempotency_key=f"teleop-{uuid.uuid4().hex}",
            lease_id=self.latest.lease_id,
            source="jcheng-teleop",
            kind="JOINT_STEP",
            profile="pose",
            contract=self.stack.version,
            joint_names=list(self.stack.profile.joint_names),
            target_positions=targets,
            max_velocity_rad_s=self.max_velocity_rad_s,
            max_acceleration_rad_s2=0.5,
            duration_s=dt,
            verify_bus=True,
            require_measured_state=True,
            expected_profile_hash=self.stack.profile.profile_hash,
        )

    def abort(self, reason: str) -> None:
        """Non-latching teleop session abort.

        The next step requires a fresh enable edge. An explicit safety stop
        still uses MotionArbiter.stop() and latches.
        """
        self.active = False
        self.latest = None
        self.last_received_s = None
        self.stop_reason = reason

    def stop(self, reason: str, now: float | None = None) -> StopResult:
        self.abort(reason)
        return self.stack.arbiter.stop("jcheng-teleop", reason, now)

    def state(self) -> dict[str, object]:
        return {
            "active": self.active,
            "last_seq": self.last_seq,
            "last_received_s": self.last_received_s,
            "latest": None
            if self.latest is None
            else {
                "seq": self.latest.seq,
                "lease_id": self.latest.lease_id,
                "source": self.latest.source,
                "mode": self.latest.mode,
                "deadman": self.latest.deadman,
            },
            "stop_reason": self.stop_reason,
        }
