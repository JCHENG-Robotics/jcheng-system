"""Short deterministic read-only probe for CI and offline evidence.

This probe does not connect to ROS or hardware. It exercises the same cache,
bridge, evidence, and preflight code used by the ROS node with a deterministic
mock bus. The 30-minute robot run uses ``jcheng_telemetry_node`` instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from jcheng_system_core.config import RobotProfile, find_repo_root, load_contract
from jcheng_system_core.timebase import Clock

from jcheng_hardware.readonly import MockReadOnlyBus, ReadOnlyBridge, ReadOnlyMapping

from .core import TelemetryCache
from .evidence import ReadOnlyEvidence, write_summary
from .preflight import (
    DomainConfig,
    PreflightInput,
    ReadOnlyConfig,
    load_lock_expectations,
    run_preflight,
)


def _mock_pulses(mapping: ReadOnlyMapping) -> dict[int, int]:
    pulses = {joint.servo_id: 500 for joint in mapping.joints}
    pulses[mapping.gripper_joint.servo_id] = 170
    return pulses


def run_probe(
    root: Path,
    duration_s: float,
    interval_s: float,
    out: Path,
    evidence_path: Path | None,
    use_mock: bool,
    require_bus: bool,
    max_gap_s: float,
) -> dict[str, Any]:
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    mapping = ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )
    domains = DomainConfig.from_file(root / "configs" / "domains.yaml")
    readonly = ReadOnlyConfig.from_file(root / "configs" / "readonly.yaml")
    clock = Clock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    evidence = ReadOnlyEvidence(clock, evidence_path)
    evidence.record_event("readonly_probe_start", duration_s=duration_s, interval_s=interval_s)

    bus = MockReadOnlyBus(_mock_pulses(mapping)) if use_mock else None
    bridge = ReadOnlyBridge(mapping, bus.read_positions) if bus is not None else None

    start = clock.now()
    deadline = start + float(duration_s)
    samples = 0
    while True:
        now = clock.now()
        if now >= deadline:
            break
        if bridge is not None:
            sample = bridge.read(now)
            cache.update_measured(sample)
            cache.update_joint_state(
                list(profile.joint_names),
                [sample.arm_positions_rad[name] for name in profile.joint_names],
                now,
            )
        else:
            cache.update_joint_state(list(profile.joint_names), [0.0 for _ in profile.joint_names], now)
        cache.update_battery_mv(7400, now)
        snapshot = cache.snapshot(now)
        health = cache.health(now)
        evidence.record_sample(snapshot, health, probe="mock" if use_mock else "commanded_only")
        samples += 1
        remaining = deadline - clock.now()
        if remaining <= 0:
            break
        time.sleep(min(float(interval_s), remaining))

    health = cache.health()
    expected_contract, expected_profile = load_lock_expectations(root)
    preflight = run_preflight(
        domains,
        readonly,
        PreflightInput(
            role="vendor",
            domain_id=0,
            contract_hash=contract.version.contract_hash,
            expected_contract_hash=expected_contract,
            profile_hash=profile.profile_hash,
            expected_profile_hash=expected_profile,
            clock_offset_ms=0.0,
            joint_state_age_s=health["commanded_age_s"],
            bus_age_s=health["measured_age_s"],
            battery_age_s=health["battery_age_s"],
            write_count=0,
            max_clock_offset_ms=50.0,
            max_state_age_s=0.5,
            max_battery_age_s=5.0,
            require_bus=require_bus,
        ),
    )
    summary = evidence.summary(require_bus=require_bus, max_gap_s=max_gap_s)
    summary.update(
        {
            "probe": "mock" if use_mock else "commanded_only",
            "samples_loop": samples,
            "contract_hash": contract.version.contract_hash,
            "profile_hash": profile.profile_hash,
            "mapping_hash": mapping.mapping_hash,
            "preflight": preflight.to_dict(),
        }
    )
    summary["ok"] = bool(summary["ok"] and preflight.ok)
    write_summary(out, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="JCHENG M4 read-only probe")
    parser.add_argument("--root", default=None)
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--out", default="e2e/reports/m4-readonly.json")
    parser.add_argument("--evidence", default="e2e/reports/m4-readonly.jsonl")
    parser.add_argument("--no-mock", action="store_true")
    parser.add_argument("--allow-unmeasured", action="store_true")
    parser.add_argument("--max-gap-s", type=float, default=2.0)
    args = parser.parse_args()
    root = Path(args.root).expanduser() if args.root else find_repo_root()
    evidence_path = Path(args.evidence) if args.evidence else None
    summary = run_probe(
        root=root,
        duration_s=float(args.duration_s),
        interval_s=float(args.interval_s),
        out=Path(args.out),
        evidence_path=evidence_path,
        use_mock=not args.no_mock,
        require_bus=not args.allow_unmeasured,
        max_gap_s=float(args.max_gap_s),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
