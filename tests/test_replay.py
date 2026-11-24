from __future__ import annotations

import json
from pathlib import Path

from tools.replay_g8 import replay_file

from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.safety import SafetyLimits


def test_replay_matches_valid_and_invalid_decisions(tmp_path: Path, root: Path) -> None:
    baseline = {
        "plan": {
            "start_joints": {
                "joint1": 0.0,
                "joint2": 0.0,
                "joint3": 0.0,
                "joint4": 0.0,
                "joint5": 0.0,
            },
            "start_gripper_rad": -2.0,
        },
        "steps": [
            {
                "label": "valid",
                "commands": [
                    {
                        "label": "step",
                        "preview": {
                            "valid": True,
                            "targets": [
                                {
                                    "joint": "joint1",
                                    "from_rad": 0.0,
                                    "target_rad": 0.05,
                                    "duration_s": 1.0,
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "label": "invalid",
                "commands": [
                    {
                        "label": "beyond_limit",
                        "preview": {
                            "valid": False,
                            "targets": [
                                {
                                    "joint": "joint1",
                                    "from_rad": 0.05,
                                    "target_rad": 2.0,
                                    "duration_s": 1.0,
                                }
                            ],
                        },
                    }
                ],
            },
        ],
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    limits = SafetyLimits.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml"
    )
    result = replay_file(path, profile, contract.version, limits)
    assert result["ok"] is True
    assert result["commands"] == 2
    assert result["matched"] == 2
    assert result["mismatched"] == 0
    assert [record["actual_approved"] for record in result["records"]] == [True, False]
