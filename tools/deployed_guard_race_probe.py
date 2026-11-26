#!/usr/bin/env python3
"""Offline probe of the deployed gateway's motion-guard stop race.

This tool loads ``armpi_debug_gateway.motion`` from the frozen
``jcheng-manipulation`` checkout and drives its public state machine. It does
**not** import ROS, open a device, start the gateway, or publish a command. It
is an offline reproduction of the ordering used by the deployed node:

```text
dispatch_motion:  validate() -> publish() -> start()
stop_motion:                         publish(hold) -> mark_stopped()
```

The guard locks each method individually, not the validate-to-start sequence.
A stop that arrives between ``validate()`` and ``start()`` finds no active
command, publishes a hold, and is then overwritten by ``start()``. The command
can subsequently complete. This is the C2p "stop before publish" window that
the mock system gateway closed in R2; the deployed implementation is a
different code base and has not been patched.

The probe is evidence for M4 readiness, not a C1-C5 hardware probe and not a
fix. The frozen baseline is read, never modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

TICK = 0.004188790204786391
JOINTS = {
    "joint1": (6, 70, 930, 1),
    "joint2": (5, 342, 810, 1),
    "joint3": (4, 500, 930, -1),
    "joint4": (3, 120, 880, 1),
    "joint5": (2, 120, 870, 1),
}


def _default_gateway_src() -> Path:
    workspace = Path(__file__).resolve().parents[2]
    return (
        workspace
        / "jcheng-manipulation"
        / "armpi-debug-studio"
        / "ros2_gateway"
    )


def _load_motion(gateway_src: Path) -> Any:
    package = gateway_src / "armpi_debug_gateway"
    motion = package / "motion.py"
    if not motion.is_file():
        raise SystemExit(
            f"deployed gateway source not found: {motion}\n"
            "pass --gateway-src pointing at the ros2_gateway directory"
        )
    name = "armpi_debug_gateway_probe"
    spec = importlib.util.spec_from_file_location(
        name,
        package / "__init__.py",
        submodule_search_locations=[str(package)],
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load the deployed package from {package}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(f"{name}.motion")


def _config(motion: Any) -> Any:
    raw = {
        "schema_version": 1,
        "mapping_version": "offline-race-probe",
        "radians_per_tick": TICK,
        "joints": {
            joint: {
                "servo_id": servo_id,
                "min_pulse": low,
                "max_pulse": high,
                "init_pulse": 500,
                "direction": direction,
                "radians_per_tick": TICK,
            }
            for joint, (servo_id, low, high, direction) in JOINTS.items()
        },
        "safety": {
            "max_step_rad": 0.10,
            "min_duration_s": 1.0,
            "max_duration_s": 10.0,
            "feedback_max_age_ms": 500,
            "tolerance_pulses": 3,
            "settle_samples": 3,
            "timeout_margin_s": 2.0,
            "single_joint_only": True,
        },
    }
    return motion.motion_config_from_dict(raw)


def _measured(raw_position: int = 500) -> dict[str, dict[str, Any]]:
    return {
        joint: {
            "raw_position": raw_position,
            "sample_age_ms": 5,
            "received_at": "2026-11-26T00:00:00Z",
        }
        for joint in JOINTS
    }


def _payload(config: Any, command_id: str) -> dict[str, Any]:
    return {
        "command_id": command_id,
        "duration_s": 1.0,
        "joints": [
            {
                "joint": "joint1",
                "servo_id": config.joints["joint1"].servo_id,
                "target_pulse": 520,
                "target_rad": config.joints["joint1"].pulse_to_rad(520),
            }
        ],
    }


def _case(motion: Any, config: Any, *, stop_between: bool) -> dict[str, Any]:
    guard = motion.MotionGuard(config)
    measured = _measured()
    plan = guard.validate(_payload(config, "race"), measured, 10.0)
    steps = ["validate"]

    state_after_stop = None
    if stop_between:
        status = guard.mark_stopped(measured, 10.001)
        state_after_stop = status["state"]
        steps.append("mark_stopped")

    guard.start(plan, measured)
    state_after_start = guard.status()["state"]
    steps.append("start")

    target = _measured(520)
    for now in (10.1, 10.2, 10.3):
        guard.observe(target, now)
    state_after_observe = guard.status()["state"]
    steps.append("observe_target")

    return {
        "stop_between_validate_and_start": stop_between,
        "steps": steps,
        "state_after_stop": state_after_stop,
        "state_after_start": state_after_start,
        "state_after_observe": state_after_observe,
        "stop_lost": stop_between and state_after_observe == "completed",
    }


def _latch_case(motion: Any, config: Any) -> dict[str, Any]:
    """Observe whether a guard stop prevents the next command by itself."""

    guard = motion.MotionGuard(config)
    measured = _measured()
    plan = guard.validate(_payload(config, "first"), measured, 10.0)
    guard.start(plan, measured)
    guard.mark_stopped(measured, 10.5)
    state_after_stop = guard.status()["state"]
    safety_stop = guard.status().get("safety_stop")
    allowed = True
    try:
        guard.validate(_payload(config, "second"), measured, 10.6)
    except motion.MotionRejected:
        allowed = False
    return {
        "state_after_stop": state_after_stop,
        "guard_safety_stop": safety_stop,
        "second_command_allowed": allowed,
        "note": (
            "The deployed gateway's stop is a hold, not a guard latch. "
            "A latch must come from the external safety supervisor; if this "
            "gateway is the motion authority, a second command is accepted "
            "immediately after a stop."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-src",
        type=Path,
        default=_default_gateway_src(),
        help="path to the deployed ros2_gateway directory",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    motion = _load_motion(args.gateway_src)
    config = _config(motion)
    normal = _case(motion, config, stop_between=False)
    race = _case(motion, config, stop_between=True)
    latch = _latch_case(motion, config)
    source = args.gateway_src / "armpi_debug_gateway" / "motion.py"
    workspace = Path(__file__).resolve().parents[2]
    try:
        source_text = str(source.relative_to(workspace))
    except ValueError:
        source_text = str(source)
    report = {
        "probe": "deployed_motion_guard_race",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source_text,
        "hardware": "none",
        "ros_imported": False,
        "config": "synthetic test mapping; not a calibration",
        "cases": {"control": normal, "race": race, "latch": latch},
        "finding": (
            "STOP_LOST_BETWEEN_VALIDATE_AND_START"
            if race["stop_lost"]
            else "NOT_REPRODUCED"
        ),
        "secondary_observation": (
            "STOP_NOT_LATCHED_BY_GUARD"
            if latch["second_command_allowed"]
            else "STOP_LATCHED"
        ),
        "severity": "P0 for M4 if the deployed gateway is the motion target",
        "code_order": {
            "dispatch": "node.dispatch_motion: validate -> publish -> start",
            "stop": "node.stop_motion: publish(hold) -> mark_stopped",
            "guard": "MotionGuard locks each method, not the sequence",
        },
        "limitations": [
            "Offline motion-guard reproduction; not the deployed HTTP process.",
            "No ROS, no publisher, no measured subscription and no hardware.",
            "A hardware C1-C5 run is still required after a fix.",
        ],
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    if race["stop_lost"]:
        print(
            "RACE CONFIRMED: a stop between validate and start is lost; "
            "the command can complete afterwards.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
