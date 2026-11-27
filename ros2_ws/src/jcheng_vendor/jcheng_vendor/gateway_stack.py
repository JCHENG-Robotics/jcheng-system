"""Composition root for the hardware-backed HTTP gateway.

The stack wires the M5.2 ``HardwareExecutor`` and a vendor-style bridge into
the same objects the M3 ``MockStack`` exposes to ``GatewayApp``. It is the
chosen M4/M5 target: the system gateway, not the frozen
``armpi_debug_gateway``.

The ROS node that owns the bridge must poll the bus (the vendor runtime uses
0.25 s) and call ``safety.update_state``. ``sync_state`` here uses the last
bridge state and is for a bridge that keeps its own cache fresh; it is not a
bus poll.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid
from typing import Any

from jcheng_system_core.authority import LeaseManager
from jcheng_system_core.config import ContractConfig, RobotProfile, load_contract
from jcheng_system_core.evidence import EvidenceStore
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.hardware_authority import HardwareAuthority
from jcheng_system_core.models import ContractVersion, MotionCommand
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock, ManualClock

from .executor import HardwareBridge, HardwareExecutor


class VendorGatewayHardware:
    """Adapt the M5.2 bridge to the executor and ``GatewayApp`` surfaces.

    It is both the executor's ``HardwareBridge`` and the read object the HTTP
    gateway uses, so a failed bus read is visible to ``/v1/safety/reset``.
    """

    def __init__(self, bridge: HardwareBridge) -> None:
        self._bridge = bridge
        self.publishers = set(bridge.publishers)
        self._bus_failed = False

    @property
    def bus_failed(self) -> bool:
        return self._bus_failed

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        return self._bridge.send_step(joint_name, target_rad, duration_s)

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        return self._bridge.send_gripper(target_rad, duration_s)

    def pulse_snapshot(self) -> dict[str, int]:
        return self._bridge.pulse_snapshot()

    def read_bus(self, timeout_s: float = 1.0) -> Any:
        try:
            sample = self._bridge.read_bus(timeout_s)
        except Exception:
            self._bus_failed = True
            raise
        self._bus_failed = False
        return sample

    def state(self, now: float | None = None) -> Any:
        return self._bridge.state()

    def hold(self, now: float | None = None) -> None:
        self._bridge.hold()

    def zero_base(self, now: float | None = None) -> None:
        """The M5.2 target has no chassis write path; base commands reject."""

    def tick(self, now: float | None = None) -> None:
        return None


@dataclass
class VendorStack:
    profile: RobotProfile
    contract: ContractConfig
    clock: object
    safety: SafetySupervisor
    authority: LeaseManager
    hardware: VendorGatewayHardware
    guard: PublisherGuard
    evidence: EvidenceStore
    arbiter: HardwareExecutor
    mode: str = "hardware"
    capabilities: tuple[str, ...] = ("hardware",)
    hardware_authority: HardwareAuthority | None = None

    @property
    def version(self) -> ContractVersion:
        return self.contract.version

    @classmethod
    def build(
        cls,
        root: str | Path,
        bridge: HardwareBridge,
        clock: Clock | ManualClock | None = None,
        evidence_path: str | Path | None = None,
        robot: str = "armpi_ultra",
        settle_s: float = 0.4,
    ) -> "VendorStack":
        root = Path(root)
        clock = clock or Clock()
        profile_dir = root / "configs" / "robots" / robot
        profile = RobotProfile.from_file(profile_dir / "profile.yaml")
        contract = load_contract(root / "configs" / "contract.yaml")
        overlay = profile_dir / "safety_overlay.yaml"
        limits = SafetyLimits.from_file(overlay) if overlay.is_file() else SafetyLimits()
        safety = SafetySupervisor(profile, contract.version, clock, limits)
        authority = LeaseManager(contract.version, clock)
        guard = PublisherGuard()
        evidence = EvidenceStore(clock, evidence_path)
        hardware = VendorGatewayHardware(bridge)
        hardware_authority = HardwareAuthority(
            contract=contract.version,
            authority=authority,
            safety=safety,
            clock=clock,
            bridge=hardware,
            guard=guard,
            max_base_duty=float(
                profile.profiles.get("base", {}).get("max_abs_duty_normalized", 0.10)
            ),
        )
        arbiter = HardwareExecutor(
            profile=profile,
            contract=contract.version,
            clock=clock,
            safety=safety,
            authority=authority,
            guard=guard,
            evidence=evidence,
            bridge=hardware,
            settle_s=settle_s,
            hardware_authority=hardware_authority,
        )
        # Revoking a lease also stops: the executor latches safety, holds the
        # arm through the hardware authority and zeroes any base output.
        authority.set_stop_hook(
            lambda lease_id, reason: arbiter.stop(
                "lease_revoke", f"{lease_id}: {reason}"
            )
        )
        return cls(
            profile=profile,
            contract=contract,
            clock=clock,
            safety=safety,
            authority=authority,
            hardware=hardware,
            guard=guard,
            evidence=evidence,
            arbiter=arbiter,
            hardware_authority=hardware_authority,
        )

    def sync_state(self) -> None:
        self.safety.update_state(self.hardware.state(), self.clock.now())

    def arm(self, operator: str = "operator") -> object:
        self.sync_state()
        return self.safety.arm(
            operator_id=operator,
            checklist_hash="vendor-test-checklist",
            confirm_phrase=self.safety.limits.arm_confirm_phrase,
            contract=self.version,
            profile_hash=self.profile.profile_hash,
        )

    def lease(
        self,
        holder: str = "operator",
        scope: str = "arm",
        priority: int = 100,
        ttl_ms: int = 30_000,
        deadman_required: bool = False,
    ):
        return self.authority.acquire(
            holder=holder,
            scope=scope,
            mode="exclusive",
            priority=priority,
            ttl_ms=ttl_ms,
            deadman_required=deadman_required,
            contract=self.version,
        )

    def command(self, lease_id: str | None = None, **overrides: Any) -> MotionCommand:
        base = {
            "intent_id": f"intent-{uuid.uuid4().hex[:8]}",
            "idempotency_key": f"idem-{uuid.uuid4().hex[:8]}",
            "lease_id": lease_id or "",
            "source": "jcheng_vendor_gateway",
            "kind": "JOINT_STEP",
            "profile": "step",
            "contract": self.version.to_dict(),
            "joint_names": ["joint1"],
            "target_positions": [0.05],
            "max_velocity_rad_s": 0.5,
            "max_acceleration_rad_s2": 0.5,
            "duration_s": 1.0,
            "verify_bus": True,
            "require_measured_state": True,
        }
        base.update(overrides)
        return MotionCommand.from_dict(base)
