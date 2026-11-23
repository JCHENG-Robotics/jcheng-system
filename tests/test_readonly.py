from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jcheng_hardware.readonly import (
    MockReadOnlyBus,
    ReadOnlyBridge,
    ReadOnlyMapping,
    ReadOnlySample,
    ReadOnlyViolation,
)
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.timebase import ManualClock
from jcheng_telemetry.core import TelemetryCache
from jcheng_telemetry.evidence import ReadOnlyEvidence
from jcheng_telemetry.preflight import (
    DomainConfig,
    PreflightInput,
    ReadOnlyConfig,
    load_lock_expectations,
    run_preflight,
)
from jcheng_telemetry.probe import run_probe


def _mapping(root: Path) -> ReadOnlyMapping:
    return ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )


def _sample(mapping: ReadOnlyMapping, stamp: float = 0.2) -> ReadOnlySample:
    bus = MockReadOnlyBus({joint.servo_id: 500 for joint in mapping.joints})
    pulse = mapping.gripper_joint.servo_id
    bus.set_pulse(pulse, 170)
    return ReadOnlyBridge(mapping, bus.read_positions).read(stamp)


def test_mapping_read_order_and_conversion(root: Path) -> None:
    mapping = _mapping(root)
    assert mapping.read_order == (1, 2, 3, 4, 5, 6)
    assert mapping.mapping_hash.startswith("sha256:")
    joint1 = next(joint for joint in mapping.arm_joints if joint.name == "joint1")
    joint3 = next(joint for joint in mapping.arm_joints if joint.name == "joint3")
    assert joint1.pulse_to_rad(700, mapping.radians_per_tick) == pytest.approx(0.837758, abs=1e-6)
    assert joint3.pulse_to_rad(300, mapping.radians_per_tick) == pytest.approx(0.837758, abs=1e-6)
    assert mapping.gripper_joint.pulse_to_rad(170, mapping.radians_per_tick) == pytest.approx(2.220, abs=1e-3)
    assert mapping.gripper_joint.pulse_to_rad(700, mapping.radians_per_tick) == pytest.approx(0.0)


def test_mapping_rejects_non_increasing_read_order(tmp_path: Path, root: Path) -> None:
    raw = yaml.safe_load(
        (root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml").read_text(
            encoding="utf-8"
        )
    )
    raw["read_order"] = [2, 1, 3, 4, 5, 6]
    path = tmp_path / "bad_mapping.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ReadOnlyViolation, match="strictly increasing"):
        ReadOnlyMapping.from_file(path)


def test_bridge_rejects_bad_pulse_and_has_no_write_path(root: Path) -> None:
    mapping = _mapping(root)
    bus = MockReadOnlyBus({joint.servo_id: 500 for joint in mapping.joints})
    bridge = ReadOnlyBridge(mapping, bus.read_positions)
    assert bridge.read(0.0).source == "bus"
    bus.pulses[6] = 1001
    with pytest.raises(ReadOnlyViolation, match="outside"):
        bridge.read(0.1)
    with pytest.raises(PermissionError, match="read-only"):
        bridge.attempt_write()
    assert bridge.health()["writes"] == 0


def test_cache_never_promotes_commanded_to_measured(root: Path) -> None:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    clock = ManualClock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    cache.update_joint_state(profile.joint_names, [0.1] * len(profile.joint_names), at=0.0)
    state = cache.snapshot(0.1)
    assert state.truth == "commanded"
    assert state.verification == "timed"
    assert state.fresh is True

    cache.update_measured(_sample(_mapping(root), stamp=0.2))
    state = cache.snapshot(0.3)
    assert state.truth == "measured"
    assert state.verification == "bus"
    assert state.positions == [0.0, 0.0, 0.0, 0.0, 0.0]

    state = cache.snapshot(1.0)
    assert state.truth == "commanded"
    assert state.verification == "timed"
    assert state.fresh is False


def test_safety_view_fails_closed_without_bus(root: Path) -> None:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    clock = ManualClock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    cache.update_joint_state(profile.joint_names, [0.0] * len(profile.joint_names), at=0.0)
    assert cache.safety_view("sha256:config", now=0.1).state == "STOP_LATCHED"
    cache.update_measured(_sample(_mapping(root), stamp=0.2))
    cache.update_battery_mv(7400, at=0.2)
    assert cache.safety_view("sha256:config", now=0.3).state == "DISARMED"


def test_preflight_domain_hash_and_write_checks(root: Path) -> None:
    domains = DomainConfig.from_file(root / "configs" / "domains.yaml")
    readonly = ReadOnlyConfig.from_file(root / "configs" / "readonly.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    data = PreflightInput(
        role="vendor",
        domain_id=0,
        contract_hash=contract.version.contract_hash,
        expected_contract_hash=contract.version.contract_hash,
        profile_hash="sha256:profile",
        expected_profile_hash="sha256:profile",
        clock_offset_ms=0.0,
        joint_state_age_s=0.1,
        bus_age_s=0.1,
        battery_age_s=0.1,
    )
    assert run_preflight(domains, readonly, data).ok
    wrong_domain = PreflightInput(**{**data.__dict__, "domain_id": 42})
    assert not run_preflight(domains, readonly, wrong_domain).ok
    write = PreflightInput(**{**data.__dict__, "write_count": 1})
    assert not run_preflight(domains, readonly, write).ok
    no_bus = PreflightInput(
        **{**data.__dict__, "bus_age_s": None, "require_bus": False}
    )
    assert run_preflight(domains, readonly, no_bus).ok


def test_domains_isolate_capability_and_pin_lock_hashes(root: Path) -> None:
    domains = DomainConfig.from_file(root / "configs" / "domains.yaml")
    readonly = ReadOnlyConfig.from_file(root / "configs" / "readonly.yaml")
    assert domains.domains["vendor"] == 0
    assert domains.domains["privileged"] == 41
    assert domains.domains["capability"] == 42
    assert domains.domains["simulation"] == 43
    assert domains.capability_allowed_domains == (42,)
    assert domains.privileged_allowed_domains == (0, 41)
    assert "/servo_controller" in readonly.forbidden_topics
    assert "/servo_controller" not in readonly.subscribe_topics
    contract = load_contract(root / "configs" / "contract.yaml")
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    expected_contract, expected_profile = load_lock_expectations(root)
    assert expected_contract == contract.version.contract_hash
    assert expected_profile == profile.profile_hash


def test_evidence_summary_records_zero_writes(root: Path) -> None:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    clock = ManualClock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    evidence = ReadOnlyEvidence(clock)
    cache.update_measured(_sample(_mapping(root), stamp=0.2))
    cache.update_battery_mv(7400, at=0.2)
    evidence.record_sample(cache.snapshot(0.2), cache.health(0.2))
    summary = evidence.summary(require_bus=True, max_gap_s=1.0)
    assert summary["writes"] == 0
    assert summary["measured_samples"] == 1
    assert summary["ok"] is True


def test_short_probe_is_read_only(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "m4.json"
    summary = run_probe(
        root=root,
        duration_s=0.3,
        interval_s=0.05,
        out=out,
        evidence_path=tmp_path / "m4.jsonl",
        use_mock=True,
        require_bus=True,
        max_gap_s=1.0,
    )
    assert summary["ok"] is True
    assert summary["writes"] == 0
    assert summary["measured_samples"] > 0
    assert out.is_file()
