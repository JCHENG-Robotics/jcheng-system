"""Robot profile and contract configuration loaders."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .models import ContractVersion

DEFAULT_CHAIN = "arm"
PROFILE_KINDS = ("bus_servo", "can_arm", "ethercat_arm")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JointLimit:
    name: str
    servo_id: int | None
    lower_rad: float
    upper_rad: float
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    direction: int = 1
    chain: str = DEFAULT_CHAIN
    index: int | None = None
    type: str = "revolute"


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
    kind: str = "bus_servo"
    chains: dict[str, dict[str, Any]] | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "RobotProfile":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("robot profile must be a mapping")
        version = raw.get("schema_version", 1)
        if version == 2:
            return cls._from_v2(path, raw)
        if version != 1:
            raise ValueError(f"unsupported robot profile schema_version {version!r}")
        joints: dict[str, JointLimit] = {}
        joint_order: list[str] = []
        for item in raw.get("joints", []):
            joint = JointLimit(
                name=str(item["name"]),
                servo_id=int(item["servo_id"]) if item.get("servo_id") is not None else None,
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

    @classmethod
    def _from_v2(cls, path: Path, raw: dict[str, Any]) -> "RobotProfile":
        kind = str(raw.get("kind", ""))
        if kind not in PROFILE_KINDS:
            raise ValueError(f"unsupported robot profile kind {kind!r}")

        chains_raw = raw.get("chains")
        if not isinstance(chains_raw, list) or not chains_raw:
            raise ValueError("robot profile v2 requires a non-empty chains list")
        chains: dict[str, dict[str, Any]] = {}
        for item in chains_raw:
            if not isinstance(item, dict):
                raise ValueError("chain must be a mapping")
            chain_id = str(item.get("id", ""))
            if not chain_id:
                raise ValueError("chain id is required")
            if chain_id in chains:
                raise ValueError(f"duplicate chain {chain_id}")
            for key in ("base_link", "tip_link"):
                if not str(item.get(key, "")):
                    raise ValueError(f"{chain_id}: {key} is required")
            workspace = item.get("workspace")
            if workspace is not None:
                lower = [float(value) for value in workspace["lower"]]
                upper = [float(value) for value in workspace["upper"]]
                if any(lo >= hi for lo, hi in zip(lower, upper)):
                    raise ValueError(f"{chain_id}: workspace lower >= upper")
            chains[chain_id] = item

        joints: dict[str, JointLimit] = {}
        joint_order: list[str] = []
        for item in raw.get("joints", []):
            if not isinstance(item, dict):
                raise ValueError("joint must be a mapping")
            name = str(item["name"])
            chain_id = str(item.get("chain", ""))
            if chain_id not in chains:
                raise ValueError(f"{name}: unknown chain {chain_id!r}")
            if kind == "bus_servo":
                if item.get("servo_id") is None or item.get("index") is not None:
                    raise ValueError(f"{name}: bus_servo requires servo_id and no index")
            elif item.get("index") is None or item.get("servo_id") is not None:
                raise ValueError(f"{name}: {kind} requires index and no servo_id")
            joint = JointLimit(
                name=name,
                servo_id=int(item["servo_id"]) if item.get("servo_id") is not None else None,
                lower_rad=float(item["lower_rad"]),
                upper_rad=float(item["upper_rad"]),
                max_velocity_rad_s=float(item["max_velocity_rad_s"]),
                max_acceleration_rad_s2=float(item["max_acceleration_rad_s2"]),
                direction=int(item.get("direction", 1)),
                chain=chain_id,
                index=int(item["index"]) if item.get("index") is not None else None,
                type=str(item.get("type", "revolute")),
            )
            if joint.name in joints:
                raise ValueError(f"duplicate joint {joint.name}")
            if joint.lower_rad >= joint.upper_rad:
                raise ValueError(f"{joint.name}: lower >= upper")
            joints[joint.name] = joint
            joint_order.append(joint.name)
        if not joints:
            raise ValueError("robot profile has no joints")

        workspace = None
        if len(chains) == 1:
            chain = next(iter(chains.values()))
            chain_workspace = chain.get("workspace")
            if chain_workspace is not None:
                workspace = {
                    "frame": chain_workspace.get("frame") or chain["base_link"],
                    "tip_link": chain["tip_link"],
                    "lower": [float(value) for value in chain_workspace["lower"]],
                    "upper": [float(value) for value in chain_workspace["upper"]],
                }
        table_plane = raw.get("table_plane")
        if table_plane is not None and not float(table_plane["normal"][2]) > 0:
            raise ValueError("table plane normal must point up")

        gripper = raw.get("gripper")
        if gripper is not None:
            gripper = cls._normalise_gripper(gripper)

        profiles = raw.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("profiles must be a mapping")
        if "gripper" in profiles:
            gripper_profile = dict(profiles["gripper"])
            if "max_velocity" in gripper_profile and "max_velocity_rad_s" not in gripper_profile:
                gripper_profile["max_velocity_rad_s"] = gripper_profile["max_velocity"]
            profiles = {**profiles, "gripper": gripper_profile}

        safety_subset = {
            "kind": kind,
            "chains": raw["chains"],
            "joints": raw["joints"],
            "workspace": workspace,
            "table_plane": table_plane,
            "profiles": profiles,
        }
        return cls(
            path=path,
            raw=raw,
            joints=joints,
            joint_order=joint_order,
            gripper=gripper,
            workspace=workspace,
            table_plane=table_plane,
            profiles=profiles,
            source_hashes=raw.get("source_hashes", {}),
            profile_hash=_sha256_file(path),
            config_hash=_sha256_json(safety_subset),
            kind=kind,
            chains=chains,
        )

    @staticmethod
    def _normalise_gripper(gripper: dict[str, Any]) -> dict[str, Any]:
        kind = str(gripper.get("type", ""))
        if kind not in ("bus_servo", "parallel_prismatic"):
            raise ValueError(f"unsupported gripper type {kind!r}")
        unit = str(gripper.get("unit", ""))
        if unit not in ("rad", "m"):
            raise ValueError(f"gripper unit must be rad or m, got {unit!r}")
        lower = float(gripper["lower"])
        upper = float(gripper["upper"])
        if lower >= upper:
            raise ValueError("gripper lower >= upper")
        for key in ("closed", "open"):
            value = float(gripper[key])
            if not lower <= value <= upper:
                raise ValueError(f"gripper {key} outside [{lower}, {upper}]")
        return {
            **gripper,
            "open_rad": float(gripper["open"]),
            "closed_rad": float(gripper["closed"]),
            "max_velocity_rad_s": float(gripper["max_velocity"]),
        }

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
