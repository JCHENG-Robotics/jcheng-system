from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from jcheng_hardware.readonly import ReadOnlyMapping
from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.evidence import EvidenceStore
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.models import MotionCommand, RobotState
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock
from jcheng_vendor.executor import BusSample, HardwareExecutor


class FakeBridge:
    publishers = {"jcheng_hardware"}

    def __init__(self, mapping: ReadOnlyMapping) -> None:
        self.mapping = mapping
        self.pulses = {joint.name: 500 for joint in mapping.joints}
        self.pulses["r_joint"] = 170
        self.hold_called = False
        self.sent: list[tuple[str, int]] = []

    def _pulse(self, joint_name: str, radians: float) -> int:
        joint = next(joint for joint in self.mapping.joints if joint.name == joint_name)
        return int(round(joint.init_raw + joint.direction * float(radians) / self.mapping.radians_per_tick))

    def _state(self, clock: Clock) -> RobotState:
        arm_joints = sorted(self.mapping.arm_joints, key=lambda joint: joint.name)
        arm = [self.pulses[joint.name] for joint in arm_joints]
        positions = [
            (pulse - 500) * self.mapping.radians_per_tick
            for pulse in arm
        ]
        gripper = (self.pulses["r_joint"] - 700) * -1 * self.mapping.radians_per_tick
        return RobotState(
            joint_names=[joint.name for joint in arm_joints],
            positions=positions,
            velocities=[0.0 for _ in positions],
            gripper_position=gripper,
            base_duty=[0.0, 0.0, 0.0, 0.0],
            battery_voltage=7.6,
            profile_hash="sha256:profile",
            truth="measured",
            verification="bus",
            fresh=True,
            stamp=clock.now(),
        )

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        pulse = self._pulse(joint_name, target_rad)
        self.pulses[joint_name] = pulse
        self.sent.append((joint_name, pulse))
        return pulse

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        pulse = self._pulse("r_joint", target_rad)
        self.pulses["r_joint"] = pulse
        self.sent.append(("r_joint", pulse))
        return pulse

    def hold(self) -> None:
        self.hold_called = True

    def read_bus(self, timeout_s: float = 1.0) -> BusSample:
        return BusSample(state=self._state(self.clock), pulses=dict(self.pulses))

    def state(self) -> RobotState:
        return self._state(self.clock)


def _executor(root: Path):
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    mapping = ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )
    clock = Clock()
    limits = SafetyLimits.from_file(root / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml")
    safety = SafetySupervisor(profile, contract.version, clock, limits)
    authority = LeaseManager(contract.version, clock)
    bridge = FakeBridge(mapping)
    bridge.clock = clock
    safety.update_state(bridge.state())
    evidence = EvidenceStore(clock)
    executor = HardwareExecutor(
        profile=profile,
        contract=contract.version,
        clock=clock,
        safety=safety,
        authority=authority,
        guard=PublisherGuard(),
        evidence=evidence,
        bridge=bridge,
        settle_s=0.01,
    )
    return profile, contract, clock, safety, authority, bridge, executor


def _arm_and_lease(profile, contract, safety, authority):
    safety.arm(
        operator_id="pytest",
        checklist_hash="checklist",
        confirm_phrase=safety.limits.arm_confirm_phrase,
        contract=contract.version,
        profile_hash=profile.profile_hash,
    )
    return authority.acquire(
        holder="pytest",
        scope="arm",
        mode="exclusive",
        priority=100,
        ttl_ms=30_000,
        deadman_required=False,
        contract=contract.version,
    )


def _step(contract, lease_id: str, duration: float = 0.5) -> MotionCommand:
    return MotionCommand.from_dict(
        {
            "intent_id": "intent-step",
            "idempotency_key": "idem-step",
            "lease_id": lease_id,
            "source": "pytest",
            "kind": "JOINT_STEP",
            "profile": "step",
            "contract": contract.version.to_dict(),
            "joint_names": ["joint1"],
            "target_positions": [0.05],
            "max_velocity_rad_s": 0.5,
            "max_acceleration_rad_s2": 0.5,
            "duration_s": duration,
            "verify_bus": True,
            "require_measured_state": True,
        }
    )


def test_executor_completes_step_with_bus_verification(root: Path) -> None:
    profile, contract, clock, safety, authority, bridge, executor = _executor(root)
    lease = _arm_and_lease(profile, contract, safety, authority)
    result = executor.execute(_step(contract, lease.lease_id))
    assert result.outcome == "COMPLETED"
    assert result.bus_verified is True
    assert bridge.pulses["joint1"] == 512
    assert result.measured_positions[0] == pytest.approx(0.05, abs=1e-3)


def test_executor_stop_holds_and_aborts(root: Path) -> None:
    profile, contract, clock, safety, authority, bridge, executor = _executor(root)
    lease = _arm_and_lease(profile, contract, safety, authority)
    result_box: list = []
    thread = threading.Thread(
        target=lambda: result_box.append(executor.execute(_step(contract, lease.lease_id, duration=1.0)))
    )
    thread.start()
    for _ in range(100):
        if executor._active is not None:
            break
        time.sleep(0.005)
    stop = executor.stop("pytest", "unit stop")
    thread.join(timeout=3.0)
    assert result_box and result_box[0].outcome == "ABORTED"
    assert bridge.hold_called is True
    assert stop.accepted is True
    assert stop.safety.stop_latched is True
    assert executor.last_stop_latency_ms is not None
    assert executor.last_stop_latency_ms < 100.0


def test_executor_rejects_non_m5_kind(root: Path) -> None:
    profile, contract, clock, safety, authority, bridge, executor = _executor(root)
    lease = _arm_and_lease(profile, contract, safety, authority)
    command = _step(contract, lease.lease_id)
    command.kind = "HOME"
    result = executor.execute(command)
    assert result.outcome in ("REJECTED", "ABORTED")
    assert result.bus_verified is False
