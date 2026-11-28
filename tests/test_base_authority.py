"""ROS-free base intent state machine tests.

Fault-injection checklist covered here: no lease, wrong holder/scope,
expired/revoked lease, deadman failure, intent stream loss, network
disconnect/recovery, duplicate/stale seq, concurrent submits, stop, restart,
unknown state, incompatible contract, legacy entry and the configurable
(provisional) 200 ms timeout. Zero output is asserted as the stop frame.
"""

from __future__ import annotations

from pathlib import Path
import threading

import pytest

from jcheng_system_core.base import (
    ARMED,
    DISABLED,
    FAULT,
    IDLE,
    MOVING,
    STOP_LATCHED,
    BaseAuthority,
    BaseConfig,
    BaseIntent,
)
from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.models import ContractVersion
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock

ROOT = Path(__file__).resolve().parents[1]


class Recorder:
    def __init__(self) -> None:
        self.outputs: list[tuple[BaseIntent | None, object]] = []
        self.lock = threading.Lock()

    def __call__(self, intent, command) -> None:
        with self.lock:
            self.outputs.append((intent, command))

    @property
    def last(self):
        with self.lock:
            return self.outputs[-1][1] if self.outputs else None

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.outputs)


def _setup(
    *,
    enabled: bool = True,
    timeout_ms: float = 200.0,
    scope: str = "base",
    ttl_ms: int = 30_000,
    deadman_ms: int | None = None,
):
    clock = ManualClock()
    stack = MockStack.build(ROOT, clock=clock)
    recorder = Recorder()
    config = BaseConfig(enabled=enabled, intent_timeout_ms=timeout_ms)
    base = BaseAuthority(
        authority=stack.authority,
        clock=clock,
        sink=recorder,
        config=config,
        safety=stack.safety,
        contract=stack.version,
    )
    lease = stack.authority.acquire(
        holder="nav",
        scope=scope,
        mode="exclusive",
        priority=50,
        ttl_ms=ttl_ms,
        deadman_required=True,
        contract=stack.version,
        deadman_ms=deadman_ms,
    )
    return stack, base, recorder, lease


def _intent(
    stack: MockStack,
    lease_id: str,
    *,
    holder: str = "nav",
    seq: int = 1,
    command_id: str | None = None,
    linear_x: float = 0.05,
    duty_limit: float = 1.0,
    deadman_ms: int = 0,
    contract: ContractVersion | None = None,
) -> BaseIntent:
    return BaseIntent(
        contract=contract or stack.version,
        lease_id=lease_id,
        holder=holder,
        command_id=command_id or f"cmd-{seq}",
        seq=seq,
        linear_x=linear_x,
        duty_limit=duty_limit,
        deadman_ms=deadman_ms,
        source="test",
    )


def test_disabled_intake_rejects_and_emits_zero() -> None:
    stack, base, recorder, lease = _setup(enabled=False)
    decision = base.submit(_intent(stack, lease.lease_id), now=0.0)
    assert decision.accepted is False
    assert decision.code == ErrorCode.SYSTEM_DISARMED.value
    assert base.state == DISABLED
    assert recorder.last.is_zero is True


def test_no_lease_latches_and_zeroes() -> None:
    stack, base, recorder, _lease = _setup()
    decision = base.submit(_intent(stack, "lease-missing"), now=0.0)
    assert decision.accepted is False
    assert decision.code == ErrorCode.NO_LEASE.value
    assert base.state == STOP_LATCHED
    assert stack.safety.stop_latched is True
    assert recorder.last.is_zero is True


def test_wrong_holder_latches_and_zeroes() -> None:
    stack, base, recorder, lease = _setup()
    decision = base.submit(
        _intent(stack, lease.lease_id, holder="intruder"), now=0.0
    )
    assert decision.accepted is False
    assert decision.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_wrong_scope_latches_and_zeroes() -> None:
    stack, base, recorder, lease = _setup(scope="arm")
    decision = base.submit(_intent(stack, lease.lease_id), now=0.0)
    assert decision.accepted is False
    assert decision.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_expired_lease_latches_and_zeroes() -> None:
    stack, base, recorder, lease = _setup(ttl_ms=100)
    stack.clock.advance(0.2)
    decision = base.submit(_intent(stack, lease.lease_id), now=stack.clock.now())
    assert decision.accepted is False
    assert decision.code == ErrorCode.LEASE_EXPIRED.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_revoked_lease_latches_and_zeroes() -> None:
    stack, base, recorder, lease = _setup()
    stack.authority.revoke(lease.lease_id, "operator", "test", stack.version)
    decision = base.submit(_intent(stack, lease.lease_id), now=0.0)
    assert decision.accepted is False
    assert decision.code == ErrorCode.LEASE_REVOKED.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_deadman_failure_latches_and_zeroes() -> None:
    stack, base, recorder, lease = _setup(deadman_ms=100)
    stack.clock.advance(0.2)
    decision = base.submit(_intent(stack, lease.lease_id), now=stack.clock.now())
    assert decision.accepted is False
    assert decision.code == ErrorCode.SAFETY_HEARTBEAT_MISSING.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_deadman_expiry_without_a_new_intent_latches_on_tick() -> None:
    stack, base, recorder, lease = _setup(deadman_ms=100)
    assert base.submit(_intent(stack, lease.lease_id), now=0.0).accepted is True
    decision = base.tick(0.15)
    assert decision is not None
    assert decision.code == ErrorCode.SAFETY_HEARTBEAT_MISSING.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_intent_stream_loss_zeroes_and_resumes_on_a_new_intent() -> None:
    stack, base, recorder, lease = _setup(timeout_ms=200.0)
    first = base.submit(_intent(stack, lease.lease_id, seq=1), now=0.0)
    assert first.accepted is True
    assert first.state == MOVING
    assert recorder.last.is_zero is False

    decision = base.tick(0.201)
    assert decision is not None
    assert decision.code == "INTENT_TIMEOUT"
    assert decision.state == ARMED
    assert recorder.last.is_zero is True

    # The next valid intent resumes without a manual reset.
    second = base.submit(_intent(stack, lease.lease_id, seq=2, command_id="cmd-2"), now=0.202)
    assert second.accepted is True
    assert second.state == MOVING
    assert recorder.last.is_zero is False


def test_timeout_is_configurable() -> None:
    stack, base, recorder, lease = _setup(timeout_ms=50.0)
    assert base.status()["intent_timeout_ms"] == 50.0
    assert base.submit(_intent(stack, lease.lease_id), now=0.0).accepted is True
    assert base.tick(0.051) is not None
    assert recorder.last.is_zero is True


def test_network_disconnect_recovery_and_stale_replay() -> None:
    stack, base, recorder, lease = _setup()
    assert base.submit(_intent(stack, lease.lease_id, seq=1), now=0.0).accepted is True
    base.link_down("network lost")
    assert base.state == IDLE
    assert recorder.last.is_zero is True
    assert base.tick(0.1) is None

    base.link_up()
    assert base.state == IDLE
    # A repeated pre-disconnect seq is rejected after recovery.
    stale = base.submit(_intent(stack, lease.lease_id, seq=1), now=0.2)
    assert stale.accepted is False
    assert stale.code == ErrorCode.INVALID_COMMAND.value
    fresh = base.submit(_intent(stack, lease.lease_id, seq=2), now=0.2)
    assert fresh.accepted is True
    assert fresh.state == MOVING


def test_duplicate_and_stale_sequences_do_not_write() -> None:
    stack, base, recorder, lease = _setup()
    assert base.submit(_intent(stack, lease.lease_id, seq=5), now=0.0).accepted is True
    count = recorder.count

    duplicate = base.submit(_intent(stack, lease.lease_id, seq=5), now=0.01)
    assert duplicate.accepted is False
    assert duplicate.code == ErrorCode.INVALID_COMMAND.value
    assert recorder.count == count

    stale = base.submit(_intent(stack, lease.lease_id, seq=4), now=0.02)
    assert stale.accepted is False
    assert recorder.count == count

    duplicate_id = base.submit(
        _intent(stack, lease.lease_id, seq=6, command_id="cmd-5"), now=0.03
    )
    assert duplicate_id.accepted is False
    assert recorder.count == count

    fresh = base.submit(_intent(stack, lease.lease_id, seq=6, command_id="cmd-6"), now=0.04)
    assert fresh.accepted is True
    assert recorder.count == count + 1


def test_concurrent_submits_are_serialized() -> None:
    stack, base, recorder, lease = _setup()
    barrier = threading.Barrier(2)
    decisions: list = []
    errors: list[BaseException] = []

    def worker(seq: int, linear_x: float) -> None:
        try:
            barrier.wait(2.0)
            decisions.append(
                base.submit(
                    _intent(stack, lease.lease_id, seq=seq, linear_x=linear_x),
                    now=0.0,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - thread boundary
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(10, 0.05)),
        threading.Thread(target=worker, args=(11, 0.02)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)
    assert errors == []
    assert len(decisions) == 2
    assert base.last_seq == 11
    assert base.state == MOVING
    assert recorder.last.seq == 11
    # The final frame belongs to the highest accepted sequence, not a torn mix.
    assert recorder.last.duty == pytest.approx((0.04, 0.04, 0.04, 0.04))


def test_stop_latches_safety_and_zeroes_then_requires_reset() -> None:
    stack, base, recorder, lease = _setup()
    assert base.submit(_intent(stack, lease.lease_id), now=0.0).accepted is True
    outcome = base.stop("operator", "operator stop", now=0.01)
    assert outcome.accepted is True
    assert base.state == STOP_LATCHED
    assert stack.safety.stop_latched is True
    assert recorder.last.is_zero is True

    rejected = base.submit(_intent(stack, lease.lease_id, seq=2), now=0.02)
    assert rejected.accepted is False
    assert rejected.code == ErrorCode.SAFETY_STOP_LATCHED.value

    base.reset("operator", "clear base latch")
    assert base.state == IDLE
    # The shared safety latch still blocks motion until the arm safety reset.
    re_latched = base.submit(_intent(stack, lease.lease_id, seq=3), now=0.03)
    assert re_latched.accepted is False
    assert re_latched.code == ErrorCode.SAFETY_STOP_LATCHED.value
    assert recorder.last.is_zero is True


def test_tick_latches_on_the_shared_safety_stop_without_a_base_intent() -> None:
    stack, base, recorder, _lease = _setup()
    assert base.state == IDLE
    stack.safety.latch(ErrorCode.SAFETY_STOP_LATCHED, "arm stop from shared safety")
    decision = base.tick(0.0)
    assert decision is not None
    assert decision.accepted is False
    assert decision.code == ErrorCode.SAFETY_STOP_LATCHED.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_tick_zeroes_when_the_shared_safety_state_is_unknown() -> None:
    stack, base, recorder, _lease = _setup()
    stack.safety.state = "NOT_A_SAFETY_STATE"
    decision = base.tick(0.0)
    assert decision is not None
    assert decision.code == ErrorCode.SAFETY_FAULT.value
    assert base.state == STOP_LATCHED
    assert recorder.last.is_zero is True


def test_restart_zeroes_and_does_not_replay_the_previous_intent() -> None:
    stack, base, recorder, lease = _setup()
    assert base.submit(_intent(stack, lease.lease_id, seq=1), now=0.0).accepted is True
    base.restart()
    assert base.state == IDLE
    assert base.last_seq == -1
    assert recorder.last.is_zero is True
    assert base.tick(0.1) is None
    assert recorder.last.is_zero is True
    # A restart begins a new session: seq 1 is valid again, but only on submit.
    assert base.submit(_intent(stack, lease.lease_id, seq=1), now=0.2).accepted is True
    assert recorder.last.is_zero is False


def test_restored_moving_state_does_not_resume_motion() -> None:
    stack, base, recorder, lease = _setup()
    assert base.submit(_intent(stack, lease.lease_id), now=0.0).accepted is True
    base.restore_state(MOVING)
    assert base.state == IDLE
    assert base.last_reason == "restored_to_idle"
    assert recorder.last.is_zero is True
    assert base.tick(0.1) is None
    assert recorder.last.is_zero is True


def test_unknown_restored_state_fails_closed() -> None:
    stack, base, recorder, lease = _setup()
    base.restore_state("NOT_A_REAL_STATE")
    assert base.state == FAULT
    assert recorder.last.is_zero is True
    decision = base.submit(_intent(stack, lease.lease_id), now=0.0)
    assert decision.accepted is False
    assert decision.code == ErrorCode.SAFETY_FAULT.value


def test_incompatible_contract_latches_fault_and_zeroes() -> None:
    stack, base, recorder, lease = _setup()
    bad = ContractVersion(
        major=stack.version.major + 1,
        minor=0,
        patch=0,
        contract_hash=stack.version.contract_hash,
    )
    decision = base.submit(
        _intent(stack, lease.lease_id, contract=bad), now=0.0
    )
    assert decision.accepted is False
    assert decision.code == ErrorCode.CONTRACT_MAJOR_MISMATCH.value
    assert base.state == FAULT
    assert recorder.last.is_zero is True


def test_legacy_intent_is_rejected_without_a_write() -> None:
    stack, base, recorder, lease = _setup()
    legacy = {
        "lease_id": lease.lease_id,
        "seq": 1,
        "linear_x": 0.05,
        "contract": stack.version.to_dict(),
    }
    with pytest.raises(KeyError):
        BaseIntent.from_dict(legacy)
    decision = base.submit(legacy, now=0.0)  # type: ignore[arg-type]
    assert decision.accepted is False
    assert decision.code == ErrorCode.INVALID_COMMAND.value
    assert recorder.count == 0


def test_zero_intent_and_zero_duty_limit_emit_stop_frames() -> None:
    stack, base, recorder, lease = _setup()
    neutral = base.submit(
        _intent(stack, lease.lease_id, seq=1, linear_x=0.0), now=0.0
    )
    assert neutral.accepted is True
    assert neutral.state == ARMED
    assert recorder.last.is_zero is True

    limited = base.submit(
        _intent(stack, lease.lease_id, seq=2, linear_x=0.05, duty_limit=0.0), now=0.01
    )
    assert limited.accepted is True
    assert limited.state == ARMED
    assert recorder.last.is_zero is True
