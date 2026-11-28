from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jcheng_system_core.config import RobotProfile, load_contract


def test_profile_and_contract(root: Path) -> None:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    assert len(profile.joint_names) == 5
    assert profile.profile_hash.startswith("sha256:")
    assert profile.config_hash.startswith("sha256:")
    assert contract.version.major == 0
    assert contract.version.minor == 2
    assert contract.version.contract_hash.startswith("sha256:")
    assert contract.interfaces_tag == "v0.2.0"


def test_piper_v2_profile(root: Path) -> None:
    profile = RobotProfile.from_file(
        root / "configs" / "robots" / "piper_x" / "profile.yaml"
    )
    assert profile.kind == "can_arm"
    assert profile.joint_names == [f"joint{index}" for index in range(1, 7)]
    joint = profile.joint("joint1")
    assert joint.index == 1
    assert joint.servo_id is None
    assert joint.chain == "arm"
    assert profile.chains is not None and "arm" in profile.chains
    assert profile.workspace is None
    assert profile.gripper is None


def test_v2_rejects_servo_id_on_can_arm(tmp_path: Path, root: Path) -> None:
    data = yaml.safe_load(
        (root / "configs" / "robots" / "piper_x" / "profile.yaml").read_text(encoding="utf-8")
    )
    data["joints"][0]["servo_id"] = 1
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="servo_id"):
        RobotProfile.from_file(path)


def test_v2_prismatic_gripper(tmp_path: Path) -> None:
    data = {
        "schema_version": 2,
        "kind": "can_arm",
        "robot": "fixture",
        "profile_version": "0.0.1",
        "chains": [{"id": "arm", "base_link": "base_link", "tip_link": "tcp"}],
        "joints": [
            {
                "name": "joint1",
                "chain": "arm",
                "index": 1,
                "lower_rad": -1.0,
                "upper_rad": 1.0,
                "max_velocity_rad_s": 1.0,
                "max_acceleration_rad_s2": 1.0,
            }
        ],
        "gripper": {
            "type": "parallel_prismatic",
            "master_joint": "gripper",
            "unit": "m",
            "lower": 0.0,
            "upper": 0.1,
            "closed": 0.0,
            "open": 0.1,
            "max_velocity": 0.05,
        },
        "profiles": {
            "moveit": {"max_velocity_rad_s": 1.0, "max_acceleration_rad_s2": 1.0},
            "step": {"max_step_rad": 0.2, "min_duration_s": 0.5, "max_duration_s": 5.0},
            "pose": {"max_velocity_rad_s": 0.5},
            "fast": {
                "max_velocity_rad_s": 2.0,
                "max_acceleration_rad_s2": 5.0,
                "requires_measured_verification": True,
            },
            "gripper": {"max_velocity": 0.05, "verify_measured": True},
        },
        "source_hashes": {},
    }
    path = tmp_path / "piper_like.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    profile = RobotProfile.from_file(path)
    assert profile.gripper_open_rad == pytest.approx(0.1)
    assert profile.gripper_closed_rad == pytest.approx(0.0)
    assert profile.profiles["gripper"]["max_velocity_rad_s"] == pytest.approx(0.05)
