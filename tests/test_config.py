from __future__ import annotations

from pathlib import Path

from jcheng_system_core.config import RobotProfile, load_contract


def test_profile_and_contract(root: Path) -> None:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    assert len(profile.joint_names) == 5
    assert profile.profile_hash.startswith("sha256:")
    assert profile.config_hash.startswith("sha256:")
    assert contract.version.major == 0
    assert contract.version.minor == 1
    assert contract.version.contract_hash.startswith("sha256:")
    assert contract.interfaces_tag == "v0.1.0"
