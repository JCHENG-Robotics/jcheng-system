"""Composition root for the M3 mock system."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from .authority import LeaseManager
from .config import ContractConfig, RobotProfile, load_contract
from .evidence import EvidenceStore
from .guard import PublisherGuard
from .mock_hardware import MockHardwareBridge
from .models import ContractVersion, MotionCommand
from .motion import MotionArbiter
from .safety import SafetyLimits, SafetySupervisor
from .timebase import Clock, ManualClock


@dataclass
class MockStack:
    profile: RobotProfile
    contract: ContractConfig
    clock: object
    safety: SafetySupervisor
    authority: LeaseManager
    hardware: MockHardwareBridge
    guard: PublisherGuard
    evidence: EvidenceStore
    arbiter: MotionArbiter
    mode: str = "mock"
    capabilities: tuple[str, ...] = ("mock",)

    @classmethod
    def build(
        cls,
        root: str | Path,
        clock: Clock | ManualClock | None = None,
        evidence_path: str | Path | None = None,
        hardware: MockHardwareBridge | None = None,
        robot: str = "armpi_ultra",
        allow_deadman_opt_out: bool = False,
        require_holder: bool = True,
    ) -> "MockStack":
        root = Path(root)
        clock = clock or Clock()
        profile_dir = root / "configs" / "robots" / robot
        profile = RobotProfile.from_file(profile_dir / "profile.yaml")
        contract = load_contract(root / "configs" / "contract.yaml")
        if not isinstance(clock, (Clock, ManualClock)):
            raise TypeError("clock must be Clock or ManualClock")
        overlay = profile_dir / "safety_overlay.yaml"
        limits = SafetyLimits.from_file(overlay) if overlay.is_file() else SafetyLimits()
        safety = SafetySupervisor(profile, contract.version, clock, limits)
        authority = LeaseManager(
            contract.version,
            clock,
            allow_deadman_opt_out=allow_deadman_opt_out,
            require_holder=require_holder,
        )
        hardware = hardware or MockHardwareBridge(profile, clock)
        guard = PublisherGuard()
        evidence = EvidenceStore(clock, evidence_path)
        arbiter = MotionArbiter(
            profile=profile,
            contract=contract.version,
            clock=clock,
            safety=safety,
            authority=authority,
            hardware=hardware,
            guard=guard,
            evidence=evidence,
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
        )

    @property
    def version(self) -> ContractVersion:
        return self.contract.version

    def sync_state(self) -> None:
        self.safety.update_state(self.hardware.state(), self.clock.now())

    def arm(self, operator: str = "operator") -> object:
        self.sync_state()
        return self.safety.arm(
            operator_id=operator,
            checklist_hash="mock-checklist",
            confirm_phrase=self.safety.limits.arm_confirm_phrase,
            contract=self.version,
            profile_hash=self.profile.profile_hash,
        )

    def lease(
        self,
        holder: str = "operator",
        scope: str = "full",
        priority: int = 100,
        ttl_ms: int = 30_000,
        deadman_required: bool | None = True,
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

    def command(self, lease_id: str | None = None, **overrides):
        holder = overrides.pop("holder", None)
        if holder is None and lease_id:
            holder = self.authority.holder_for(lease_id) or ""
        base = {
            "intent_id": f"intent-{uuid.uuid4().hex[:8]}",
            "idempotency_key": f"idem-{uuid.uuid4().hex[:8]}",
            "lease_id": lease_id or "",
            "holder": holder or "",
            "source": "jcheng_mock",
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
