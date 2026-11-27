"""Hardware authority: the single logical exit and its fail-closed checks.

Fault-injection coverage here: no lease, wrong holder/scope, expired/revoked
lease, deadman failure, incompatible contract, unknown state/kind, and the
arm/base stop coupling. Every rejection asserts that no bridge write happened.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.base import BaseCommand, BaseIntent
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.errors import ErrorCode, MotionRejected
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.hardware_authority import HardwareAuthority
from jcheng_system_core.mock_hardware import MockHardwareBridge
from jcheng_system_core.models import ContractVersion, MotionCommand
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import ManualClock

ROOT = Path(__file__).resolve().parents[1]


class RecordingBridge(MockHardwareBridge):
    """Mock bridge plus write-only counters (no ROS)."""

    def __init__(self, profile: RobotProfile, clock: ManualClock) -> None:
        super().__init__(profile, clock)
        self.holds = 0
        self.arm_writes: list[tuple[str, float, float]] = []
        self.gripper_writes: list[tuple[float, float]] = []
        self.motor_writes: list[list[float]] = []

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        self.arm_writes.append((joint_name, float(target_rad), float(duration_s)))
        self.publish_command("JOINT_STEP")
        return 500

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        self.gripper_writes.append((float(target_rad), float(duration_s)))
        self.publish_command("GRIPPER")
        return 700

    def publish_command(self, kind: str) -> None:
        self.commands.append({"kind": kind, "at": self.clock.now()})

    def hold(self, now: float | None = None) -> None:
        self.holds += 1
        super().hold(now)

    def publish_motor_speeds(self, duty: list[float], now: float | None = None) -> None:
        self.motor_writes.append([float(value) for value in duty])
        super().publish_motor_speeds(duty, now)


def _profile() -> RobotProfile:
    return RobotProfile.from_file(
        ROOT / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    )


def _contract():
    return load_contract(ROOT / "configs" / "contract.yaml")


def _setup(
    *,
    base_writes_enabled: bool = True,
    scope: str = "full",
    ttl_ms: int = 30_000,
    deadman_ms: int | None = None,
    holder: str = "operator",
):
    profile = _profile()
    contract = _contract()
    clock = ManualClock()
    bridge = RecordingBridge(profile, clock)
    safety = SafetySupervisor(
        profile,
        contract.version,
        clock,
        SafetyLimits.from_file(
            ROOT / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml"
        ),
    )
    safety.update_state(bridge.state(), clock.now())
    safety.arm(
        operator_id="operator",
        checklist_hash="hardware-authority-test",
        confirm_phrase=safety.limits.arm_confirm_phrase,
        contract=contract.version,
        profile_hash=profile.profile_hash,
    )
    authority = LeaseManager(contract.version, clock)
    lease = authority.acquire(
        holder=holder,
        scope=scope,
        mode="exclusive",
        priority=100,
        ttl_ms=ttl_ms,
        deadman_required=deadman_ms is not None,
        contract=contract.version,
        deadman_ms=deadman_ms,
    )
    hardware = HardwareAuthority(
        contract=contract.version,
        authority=authority,
        safety=safety,
        clock=clock,
        bridge=bridge,
        guard=PublisherGuard(),
        max_base_duty=0.10,
        base_writes_enabled=base_writes_enabled,
    )
    return clock, bridge, safety, authority, lease, hardware, contract.version


def _command(contract: ContractVersion, lease_id: str, *, holder: str = "") -> MotionCommand:
    payload = {
        "intent_id": "intent-hw",
        "idempotency_key": "idem-hw",
        "lease_id": lease_id,
        "source": "test",
        "kind": "JOINT_STEP",
        "profile": "step",
        "contract": contract.to_dict(),
        "joint_names": ["joint1"],
        "target_positions": [0.05],
        "max_velocity_rad_s": 0.5,
        "max_acceleration_rad_s2": 0.5,
        "duration_s": 1.0,
        "verify_bus": True,
        "require_measured_state": True,
        "holder": holder,
    }
    return MotionCommand.from_dict(payload)


def _base_intent(
    contract: ContractVersion,
    lease_id: str,
    *,
    holder: str = "operator",
    seq: int = 1,
    linear_x: float = 0.05,
    duty_limit: float = 1.0,
    deadman_ms: int = 0,
) -> BaseIntent:
    return BaseIntent(
        contract=contract,
        lease_id=lease_id,
        holder=holder,
        command_id=f"cmd-{seq}",
        seq=seq,
        linear_x=linear_x,
        duty_limit=duty_limit,
        deadman_ms=deadman_ms,
        source="test",
    )


def test_successful_arm_write_goes_through_the_bridge_once() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup()
    assert hardware.execute_arm(_command(contract, lease.lease_id)) == 500
    assert bridge.arm_writes == [("joint1", 0.05, 1.0)]
    assert bridge.gripper_writes == []
    report = hardware.validate_arm(_command(contract, lease.lease_id))
    assert report.approved is True


def test_no_lease_is_rejected_before_any_write() -> None:
    _, bridge, _, _, _, hardware, contract = _setup()
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, "lease-missing"))
    assert exc.value.code == ErrorCode.NO_LEASE.value
    assert bridge.arm_writes == []


def test_wrong_holder_is_rejected_before_any_write() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup()
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, lease.lease_id, holder="intruder"))
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert bridge.arm_writes == []


def test_wrong_scope_is_rejected_before_any_write() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup(scope="arm")
    intent = _base_intent(contract, lease.lease_id)
    with pytest.raises(MotionRejected) as exc:
        hardware.submit_base(intent, BaseCommand(duty=(0.1, 0.1, 0.1, 0.1)))
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert bridge.motor_writes == []


def test_expired_lease_is_rejected_before_any_write() -> None:
    clock, bridge, _, _, lease, hardware, contract = _setup(ttl_ms=100)
    clock.advance(0.2)
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, lease.lease_id))
    assert exc.value.code == ErrorCode.LEASE_EXPIRED.value
    assert bridge.arm_writes == []


def test_revoked_lease_is_rejected_before_any_write() -> None:
    _, bridge, _, authority, lease, hardware, contract = _setup()
    authority.revoke(lease.lease_id, "operator", "test revoke", contract)
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, lease.lease_id))
    assert exc.value.code == ErrorCode.LEASE_REVOKED.value
    assert bridge.arm_writes == []


def test_failed_deadman_is_rejected_before_any_write() -> None:
    clock, bridge, _, _, lease, hardware, contract = _setup(deadman_ms=100)
    clock.advance(0.2)
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, lease.lease_id))
    assert exc.value.code == ErrorCode.SAFETY_HEARTBEAT_MISSING.value
    assert bridge.arm_writes == []


def test_incompatible_contract_is_rejected_before_any_write() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup()
    bad = ContractVersion(major=contract.major + 1, minor=0, patch=0, contract_hash=contract.contract_hash)
    command = replace(_command(contract, lease.lease_id), contract=bad)
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(command)
    assert exc.value.code == ErrorCode.CONTRACT_MAJOR_MISMATCH.value
    assert bridge.arm_writes == []


def test_unknown_safety_state_fails_closed() -> None:
    _, bridge, safety, _, lease, hardware, contract = _setup()
    safety.state = "NOT_A_STATE"
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(_command(contract, lease.lease_id))
    assert exc.value.code == ErrorCode.SAFETY_FAULT.value
    assert bridge.arm_writes == []


def test_unknown_kind_is_rejected_before_any_write() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup()
    payload = _command(contract, lease.lease_id).to_dict()
    payload.update(
        {
            "kind": "BASE_PULSE",
            "profile": "base",
            "base_pulse": {"duty": [0.0, 0.0, 0.0, 0.0], "duration_s": 1.0},
        }
    )
    command = MotionCommand.from_dict(payload)
    with pytest.raises(MotionRejected) as exc:
        hardware.execute_arm(command)
    assert exc.value.code == ErrorCode.UNKNOWN_KIND.value
    assert bridge.arm_writes == []
    assert bridge.gripper_writes == []


def test_base_writes_are_disabled_by_default() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup(base_writes_enabled=False)
    intent = _base_intent(contract, lease.lease_id)
    with pytest.raises(MotionRejected) as exc:
        hardware.submit_base(intent, BaseCommand(duty=(0.1, 0.1, 0.1, 0.1)))
    assert exc.value.code == ErrorCode.SYSTEM_DISARMED.value
    assert bridge.motor_writes == []


def test_submit_base_requires_lease_holder_and_limits() -> None:
    _, bridge, _, _, lease, hardware, contract = _setup()
    with pytest.raises(MotionRejected) as exc:
        hardware.submit_base(
            _base_intent(contract, "lease-missing"),
            BaseCommand(duty=(0.1, 0.1, 0.1, 0.1)),
        )
    assert exc.value.code == ErrorCode.NO_LEASE.value

    with pytest.raises(MotionRejected) as exc:
        hardware.submit_base(
            _base_intent(contract, lease.lease_id, holder="intruder"),
            BaseCommand(duty=(0.1, 0.1, 0.1, 0.1)),
        )
    assert exc.value.code == ErrorCode.NOT_LEASE_HOLDER.value

    with pytest.raises(MotionRejected) as exc:
        hardware.submit_base(
            _base_intent(contract, lease.lease_id),
            BaseCommand(duty=(0.2, 0.0, 0.0, 0.0)),
        )
    assert exc.value.code == ErrorCode.VELOCITY_LIMIT.value
    assert bridge.motor_writes == []

    hardware.submit_base(
        _base_intent(contract, lease.lease_id),
        BaseCommand(duty=(0.1, 0.1, 0.1, 0.1)),
    )
    assert bridge.motor_writes[-1] == [0.1, 0.1, 0.1, 0.1]


def test_stop_holds_arm_and_zeroes_base_without_a_lease() -> None:
    _, bridge, safety, authority, lease, hardware, _ = _setup()
    authority.release(lease.lease_id)
    assert authority.active() == []
    result = hardware.stop("operator", "test stop")
    assert result.accepted is True
    assert result.confirmation == "request_accepted"
    assert safety.stop_latched is True
    assert bridge.holds == 1
    assert bridge.motor_writes[-1] == [0.0, 0.0, 0.0, 0.0]
    status = hardware.status()
    assert status["stop_latched"] is True
    assert status["last_stop_outputs"] == {"arm_hold": True, "base_zero": True}


def test_stop_zeroes_base_even_when_base_writes_are_disabled() -> None:
    _, bridge, safety, _, _, hardware, _ = _setup(base_writes_enabled=False)
    hardware.stop("operator", "disabled intake stop")
    assert safety.stop_latched is True
    assert bridge.motor_writes[-1] == [0.0, 0.0, 0.0, 0.0]


def test_missing_bridge_fails_closed() -> None:
    profile = _profile()
    contract = _contract()
    clock = ManualClock()
    safety = SafetySupervisor(profile, contract.version, clock)
    safety.update_state(MockHardwareBridge(profile, clock).state())
    authority = LeaseManager(contract.version, clock)
    lease = authority.acquire(
        holder="operator",
        scope="full",
        mode="exclusive",
        priority=100,
        ttl_ms=10_000,
        deadman_required=False,
        contract=contract.version,
    )
    hardware = HardwareAuthority(
        contract=contract.version,
        authority=authority,
        safety=safety,
        clock=clock,
        bridge=None,
        base_writes_enabled=True,
    )
    with pytest.raises(MotionRejected) as exc:
        hardware.validate_arm(_command(contract.version, lease.lease_id))
    assert exc.value.code == ErrorCode.HARDWARE_UNAVAILABLE.value
