"""Replay v1.0.0 G8 recorded joint targets through the new validator.

This is a decision-only replay: it compares the baseline preview's
``valid`` decision with the new SafetySupervisor's ``approved`` decision.
It does not execute motion and does not compare physical landing points.

Usage:
    python3 tools/replay_g8.py --root . \
      --baseline ../jcheng-manipulation/evidence/g8_acceptance_20261123/g8_run.json \
      --out e2e/reports/s13-replay.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.mock_hardware import MockHardwareBridge
from jcheng_system_core.models import (
    ContractVersion,
    GripperCommand,
    JointTrajectory,
    JointTrajectoryPoint,
    MotionCommand,
)
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock

LIMIT_TOLERANCE_RAD = 1e-3


def _clamp_joint(name: str, value: float, profile: RobotProfile) -> tuple[float, bool]:
    value = float(value)
    if name == "r_joint":
        lower, upper = 0.0, profile.gripper_open_rad
        value = abs(value)
    else:
        joint = profile.joints[name]
        lower, upper = joint.lower_rad, joint.upper_rad
    clamped = min(upper, max(lower, value))
    if abs(clamped - value) > LIMIT_TOLERANCE_RAD:
        return value, False
    return clamped, abs(clamped - value) > 1e-12


def _target_map(command: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(target["joint"]): target
        for target in command.get("preview", {}).get("targets", [])
    }


def _command_from_baseline(
    baseline: dict[str, Any],
    targets: dict[str, dict[str, Any]],
    profile: RobotProfile,
    contract: ContractVersion,
    current: dict[str, float],
    index: int,
    normalizations: list[dict[str, Any]],
) -> MotionCommand:
    if "r_joint" in targets:
        target = targets["r_joint"]
        duration = float(target.get("duration_s", 1.0))
        position, normalized = _clamp_joint("r_joint", target["target_rad"], profile)
        if normalized:
            normalizations.append(
                {
                    "joint": "r_joint",
                    "raw": abs(float(target["target_rad"])),
                    "clamped": position,
                }
            )
        return MotionCommand(
            intent_id=f"s13-{index}",
            idempotency_key=f"s13-{index}",
            lease_id="replay-lease",
            source="s13_replay",
            kind="GRIPPER",
            profile="gripper",
            contract=contract,
            gripper=GripperCommand(
                position_rad=position,
                max_velocity_rad_s=0.5,
                duration_s=duration,
                verify_bus=True,
            ),
            duration_s=duration,
            verify_bus=True,
            require_measured_state=True,
        )

    names = list(profile.joint_names)
    first = [float(current.get(name, 0.0)) for name in names]
    last = [float(current.get(name, 0.0)) for name in names]
    duration = 0.0
    for name in names:
        target = targets.get(name)
        if target is None:
            continue
        start_value, start_normalized = _clamp_joint(
            name, target.get("from_rad", current.get(name, 0.0)), profile
        )
        target_value, target_normalized = _clamp_joint(
            name, target["target_rad"], profile
        )
        if start_normalized or target_normalized:
            normalizations.append(
                {
                    "joint": name,
                    "raw_start": float(target.get("from_rad", current.get(name, 0.0))),
                    "clamped_start": start_value,
                    "raw_target": float(target["target_rad"]),
                    "clamped_target": target_value,
                }
            )
        first[names.index(name)] = start_value
        last[names.index(name)] = target_value
        duration = max(duration, float(target.get("duration_s", 0.0)))
    duration = max(0.05, duration)
    return MotionCommand(
        intent_id=f"s13-{index}",
        idempotency_key=f"s13-{index}",
        lease_id="replay-lease",
        source="s13_replay",
        kind="JOINT_TRAJECTORY",
        profile="fast",
        contract=contract,
        trajectory=JointTrajectory(
            joint_names=names,
            points=[
                JointTrajectoryPoint(positions=first, time_from_start=0.0),
                JointTrajectoryPoint(positions=last, time_from_start=duration),
            ],
        ),
        joint_names=names,
        target_positions=last,
        max_velocity_rad_s=4.0,
        max_acceleration_rad_s2=20.0,
        duration_s=duration,
        verify_bus=True,
        require_measured_state=True,
    )


def replay_file(
    baseline_path: Path,
    profile: RobotProfile,
    contract: ContractVersion,
    limits: SafetyLimits,
) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    start = baseline.get("plan", {}).get("start_joints", {}) or {}
    current = {str(name): float(value) for name, value in start.items()}
    clock = Clock()
    hardware = MockHardwareBridge(profile, clock)
    safety = SafetySupervisor(profile, contract, clock, limits)
    hardware.positions = [float(current.get(name, 0.0)) for name in profile.joint_names]
    hardware.gripper_position = abs(float(baseline.get("plan", {}).get("start_gripper_rad", 0.0)))
    hardware.stamp = clock.now()
    safety.update_state(hardware.state())
    safety.arm(
        operator_id="s13-replay",
        checklist_hash="s13",
        confirm_phrase=limits.arm_confirm_phrase,
        contract=contract,
        profile_hash=profile.profile_hash,
    )

    records: list[dict[str, Any]] = []
    index = 0
    for step in baseline.get("steps", []):
        for command in step.get("commands", []):
            targets = _target_map(command)
            if not targets:
                continue
            # Set the simulated measured state to the command's start pose.
            for name, target in targets.items():
                if name == "r_joint":
                    hardware.gripper_position = abs(float(target.get("from_rad", 0.0)))
                elif name in profile.joints:
                    hardware.positions[profile.joint_names.index(name)] = float(
                        target.get("from_rad", 0.0)
                    )
            hardware.stamp = clock.now()
            safety.update_state(hardware.state())
            normalizations: list[dict[str, Any]] = []
            motion = _command_from_baseline(
                baseline, targets, profile, contract, current, index, normalizations
            )
            report = safety.validate(motion, lease_ok=True, lease_code="OK")
            expected = bool(command.get("preview", {}).get("valid", False))
            record = {
                "index": index,
                "step": step.get("label", ""),
                "command": command.get("label", ""),
                "kind": motion.kind,
                "profile": motion.profile,
                "expected_approved": expected,
                "actual_approved": bool(report.approved),
                "actual_reason": report.reason_code,
                "violations": list(report.violations),
                "normalizations": normalizations,
                "match": bool(report.approved) == expected,
            }
            records.append(record)
            for name, target in targets.items():
                if name != "r_joint":
                    current[name] = float(target["target_rad"])
            index += 1

    mismatches = [record for record in records if not record["match"]]
    return {
        "schema_version": 1,
        "kind": "s13_replay",
        "baseline": str(baseline_path),
        "commands": len(records),
        "matched": len(records) - len(mismatches),
        "mismatched": len(mismatches),
        "match_rate": (len(records) - len(mismatches)) / len(records) if records else 0.0,
        "ok": not mismatches,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="S13 G8 decision replay")
    parser.add_argument("--root", default=".")
    parser.add_argument("--baseline", action="append", required=True)
    parser.add_argument("--out", default="e2e/reports/s13-replay.json")
    args = parser.parse_args()
    root = Path(args.root)
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract_config = load_contract(root / "configs" / "contract.yaml")
    limits = SafetyLimits.from_file(root / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml")
    results = [
        replay_file(
            Path(path),
            profile,
            contract_config.version,
            limits,
        )
        for path in args.baseline
    ]
    output = {
        "schema_version": 1,
        "kind": "s13_replay_bundle",
        "ok": all(result["ok"] for result in results),
        "runs": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("ok",)}, ensure_ascii=False))
    for result in results:
        print(
            f"{result['baseline']}: {result['matched']}/{result['commands']} "
            f"matched, {result['mismatched']} mismatched"
        )
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
