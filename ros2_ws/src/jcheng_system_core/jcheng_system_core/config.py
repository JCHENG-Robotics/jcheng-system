"""Robot profile and contract configuration loaders."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .models import ContractVersion


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JointLimit:
    name: str
    servo_id: int
    lower_rad: float
    upper_rad: float
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    direction: int = 1


@dataclass
class RobotProfile:
    path: Path
    raw: dict[str, Any]
    joints: dict[str, JointLimit]
    joint_order: list[str]
    gripper: dict[str, Any] | None
    workspace: dict[str, Any] | None
    table_plane: dict[str, Any] | None
    profiles: dict[str, Any]
    source_hashes: dict[str, str]
    profile_hash: str
    config_hash: str

    @classmethod
    def from_file(cls, path: str | Path) -> "RobotProfile":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("robot profile must be a mapping")
        joints: dict[str, JointLimit] = {}
        joint_order: list[str] = []
        for item in raw.get("joints", []):
            joint = JointLimit(
                name=str(item["name"]),
                servo_id=int(item["servo_id"]),
                lower_rad=float(item["lower_rad"]),
                upper_rad=float(item["upper_rad"]),
                max_velocity_rad_s=float(item["max_velocity_rad_s"]),
                max_acceleration_rad_s2=float(item["max_acceleration_rad_s2"]),
                direction=int(item.get("direction", 1)),
            )
            if joint.name in joints:
                raise ValueError(f"duplicate joint {joint.name}")
            joints[joint.name] = joint
            joint_order.append(joint.name)
        if not joints:
            raise ValueError("robot profile has no joints")
        for joint in joints.values():
            if joint.lower_rad >= joint.upper_rad:
                raise ValueError(f"{joint.name}: lower >= upper")
        workspace = raw.get("workspace")
        table_plane = raw.get("table_plane")
        if table_plane is not None and not table_plane["normal"][2] > 0:
            raise ValueError("table plane normal must point up")
        safety_subset = {
            "joints": raw["joints"],
            "workspace": workspace,
            "table_plane": table_plane,
            "profiles": raw.get("profiles", {}),
        }
        return cls(
            path=path,
            raw=raw,
            joints=joints,
            joint_order=joint_order,
            gripper=raw.get("gripper"),
            workspace=workspace,
            table_plane=table_plane,
            profiles=raw.get("profiles", {}),
            source_hashes=raw.get("source_hashes", {}),
            profile_hash=_sha256_file(path),
            config_hash=_sha256_json(safety_subset),
        )

    def joint(self, name: str) -> JointLimit:
        try:
            return self.joints[name]
        except KeyError as exc:
            raise KeyError(f"unknown joint {name}") from exc

    def profile(self, name: str) -> dict[str, Any]:
        if name not in self.profiles:
            raise KeyError(f"unknown profile {name}")
        return self.profiles[name]

    @property
    def joint_names(self) -> list[str]:
        return list(self.joint_order)

    @property
    def gripper_open_rad(self) -> float:
        if self.gripper is None:
            raise ValueError("robot profile has no gripper")
        return float(self.gripper["open_rad"])

    @property
    def gripper_closed_rad(self) -> float:
        if self.gripper is None:
            raise ValueError("robot profile has no gripper")
        return float(self.gripper["closed_rad"])


@dataclass(frozen=True)
class ContractConfig:
    version: ContractVersion
    interfaces_tag: str
    interfaces_commit: str
    path: Path

    @property
    def contract_hash(self) -> str:
        return self.version.contract_hash


def load_contract(path: str | Path) -> ContractConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("contract config must be a mapping")
    interfaces = raw["interfaces"]
    return ContractConfig(
        version=ContractVersion.from_dict(interfaces["contract"]),
        interfaces_tag=str(interfaces["tag"]),
        interfaces_commit=str(interfaces["commit"]),
        path=path,
    )


def find_repo_root() -> Path:
    """Walk up from this file to the repository root (best effort)."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "configs" / "contract.yaml").is_file():
            return candidate
    raise FileNotFoundError("cannot locate repository configs/contract.yaml")
