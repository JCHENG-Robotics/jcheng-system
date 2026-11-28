"""C2p ordering tests for the M5.2 hardware executor.

The executor is the chosen M4/M5 hardware path behind the system gateway. A
stop that is accepted before the bridge send must not be followed by a send.
"""

from __future__ import annotations

from pathlib import Path
import threading

from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.evidence import EvidenceStore
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.models import MotionCommand, RobotState
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock
from jcheng_vendor.executor import BusSample, HardwareExecutor

ROOT = Path(__file__).resolve().parents[1]
TICK = 0.004188790204786391


class FakeBridge:
    publishers = {"jcheng_hardware"}

    def __init__(self, clock: Clock, profile: RobotProfile) -> None:
        self.clock = clock
        self.profile = profile
        self.pulses = {name: 500 for name in profile.joint_names}
        self.pulses["r_joint"] = 700
        self.events: list[str] = []
        self.lock = threading.Lock()
        self.block_snapshot = False
        self.snapshot_entered = threading.Event()
        self.release_snapshot = threading.Event()
        self.sent = threading.Event()
        self.holds = 0

    def _record(self, event: str) -> None:
        with self.lock:
            self.events.append(event)

    def pulse_snapshot(self) -> dict[str, int]:
        self._record("snapshot")
        self.snapshot_entered.set()
        if self.block_snapshot:
            self.release_snapshot.wait(2.0)
        return dict(self.pulses)

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        self._record("send_step")
        pulse = int(round(500 + float(target_rad) / TICK))
        self.pulses[joint_name] = pulse
        self.sent.set()
        return pulse

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        self._record("send_gripper")
        pulse = int(round(700 + float(target_rad) / TICK))
        self.pulses["r_joint"] = pulse
        self.sent.set()
        return pulse

    def hold(self) -> None:
        self._record("hold")
        self.holds += 1

    def read_bus(self, timeout_s: float = 1.0) -> BusSample:
        return BusSample(state=self.state(), pulses=dict(self.pulses))

    def state(self) -> RobotState:
        return RobotState(
            joint_names=list(self.profile.joint_names),
            positions=[
                (self.pulses[name] - 500) * TICK for name in self.profile.joint_names
            ],
            velocities=[0.0 for _ in self.profile.joint_names],
            gripper_position=(self.pulses["r_joint"] - 700) * TICK,
            base_duty=[0.0, 0.0, 0.0, 0.0],
            battery_voltage=7.4,
            profile_hash=self.profile.profile_hash,
            truth="measured",
            verification="bus",
            fresh=True,
            stamp=self.clock.now(),
        )


def _setup():
    profile = RobotProfile.from_file(
        ROOT / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    )
    contract = load_contract(ROOT / "configs" / "contract.yaml")
    clock = Clock()
    safety = SafetySupervisor(
        profile,
        contract.version,
        clock,
        SafetyLimits.from_file(
            ROOT / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml"
        ),
    )
    authority = LeaseManager(contract.version, clock)
    bridge = FakeBridge(clock, profile)
    safety.update_state(bridge.state(), clock.now())
    safety.arm(
        operator_id="operator",
        checklist_hash="c2p-test",
        confirm_phrase=safety.limits.arm_confirm_phrase,
        contract=contract.version,
        profile_hash=profile.profile_hash,
    )
    lease = authority.acquire(
        holder="operator",
        scope="arm",
        mode="exclusive",
        priority=100,
        ttl_ms=30_000,
        deadman_required=True,
        contract=contract.version,
    )
    executor = HardwareExecutor(
        profile=profile,
        contract=contract.version,
        clock=clock,
        safety=safety,
        authority=authority,
        guard=PublisherGuard(),
        evidence=EvidenceStore(clock),
        bridge=bridge,
        settle_s=0.0,
    )
    command = MotionCommand.from_dict(
        {
            "intent_id": "intent-c2p",
            "idempotency_key": "idem-c2p",
            "lease_id": lease.lease_id,
            "holder": "operator",
            "source": "test",
            "kind": "JOINT_STEP",
            "profile": "step",
            "contract": contract.version.to_dict(),
            "joint_names": ["joint1"],
            "target_positions": [0.05],
            "max_velocity_rad_s": 0.5,
            "max_acceleration_rad_s2": 0.5,
            "duration_s": 0.5,
            "verify_bus": True,
            "require_measured_state": True,
        }
    )
    return executor, bridge, command


def test_stop_before_send_does_not_publish() -> None:
    executor, bridge, command = _setup()
    bridge.block_snapshot = True
    results: list = []
    worker = threading.Thread(target=lambda: results.append(executor.execute(command)))
    worker.start()
    assert bridge.snapshot_entered.wait(2.0)
    stop = executor.stop("operator", "test stop")
    assert stop.accepted is True
    assert stop.confirmation == "request_accepted"
    assert stop.physical_stop == "unverified"
    bridge.release_snapshot.set()
    worker.join(2.0)
    assert not worker.is_alive()
    assert results[0].outcome == "ABORTED"
    assert "send_step" not in bridge.events, bridge.events


def test_stop_after_send_holds_and_aborts() -> None:
    executor, bridge, command = _setup()
    results: list = []
    worker = threading.Thread(target=lambda: results.append(executor.execute(command)))
    worker.start()
    assert bridge.sent.wait(2.0)
    stop = executor.stop("operator", "test stop")
    assert stop.confirmation == "request_accepted"
    worker.join(2.0)
    assert not worker.is_alive()
    assert results[0].outcome == "ABORTED"
    assert bridge.events.index("send_step") < bridge.events.index("hold")


def test_completed_step_uses_bus_verification() -> None:
    executor, bridge, command = _setup()
    result = executor.execute(command)
    assert result.outcome == "COMPLETED"
    assert result.bus_verified is True
    assert bridge.holds == 0
