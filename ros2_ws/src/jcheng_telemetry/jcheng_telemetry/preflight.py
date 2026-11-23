"""M4 preflight checks for the read-only robot integration.

These checks are pure Python so CI can run them without ROS or hardware. The
ROS node fills the same inputs from live topics/services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import math

import yaml


@dataclass(frozen=True)
class DomainConfig:
    path: Path
    domains: dict[str, int]
    processes: dict[str, tuple[int, ...]]
    capability_allowed_domains: tuple[int, ...]
    privileged_allowed_domains: tuple[int, ...]
    simulation_allowed_domains: tuple[int, ...]
    forbid_domain_override: bool
    dds_profiles: dict[str, str]
    writes_allowed: int

    @classmethod
    def from_file(cls, path: str | Path) -> "DomainConfig":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("domains config must be a mapping")
        domains = {str(key): int(value) for key, value in raw["domains"].items()}
        expected = {"vendor", "privileged", "capability", "simulation"}
        if set(domains) != expected:
            raise ValueError(f"domains must be exactly {sorted(expected)}")
        if len(set(domains.values())) != len(domains):
            raise ValueError("domain IDs must be distinct")
        known = set(domains.values())
        processes: dict[str, tuple[int, ...]] = {}
        for name, values in raw.get("processes", {}).items():
            ids = tuple(int(value) for value in values)
            if not ids or not set(ids) <= known:
                raise ValueError(f"process {name} has an unknown domain: {ids}")
            processes[str(name)] = ids
        isolation = raw.get("isolation", {})
        capability = tuple(int(value) for value in isolation.get("capability_allowed_domains", []))
        privileged = tuple(int(value) for value in isolation.get("privileged_allowed_domains", []))
        simulation = tuple(int(value) for value in isolation.get("simulation_allowed_domains", []))
        if capability != (domains["capability"],):
            raise ValueError("capability plane must be limited to its own domain")
        if not set(privileged) <= {domains["vendor"], domains["privileged"]}:
            raise ValueError("privileged plane may only join vendor or privileged domains")
        if simulation != (domains["simulation"],):
            raise ValueError("simulation plane must be limited to its own domain")
        dds = raw.get("dds", {})
        profiles = {str(key): str(value) for key, value in dds.get("profiles", {}).items()}
        if set(profiles) != expected:
            raise ValueError("dds profiles must cover all four domains")
        readonly = raw.get("readonly", {})
        writes_allowed = int(readonly.get("writes_allowed", -1))
        if writes_allowed != 0:
            raise ValueError("M4 domains config must not allow writes")
        return cls(
            path=path,
            domains=domains,
            processes=processes,
            capability_allowed_domains=capability,
            privileged_allowed_domains=privileged,
            simulation_allowed_domains=simulation,
            forbid_domain_override=bool(isolation.get("forbid_domain_override", True)),
            dds_profiles=profiles,
            writes_allowed=writes_allowed,
        )


@dataclass(frozen=True)
class ReadOnlyConfig:
    path: Path
    mode: str
    writes_allowed: int
    subscribe_topics: tuple[str, ...]
    publish_topics: tuple[str, ...]
    call_services: tuple[str, ...]
    forbidden_topics: tuple[str, ...]
    forbidden_services: tuple[str, ...]
    forbidden_devices: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> "ReadOnlyConfig":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("readonly config must be a mapping")
        if str(raw.get("mode")) != "readonly":
            raise ValueError("readonly config must set mode: readonly")
        if int(raw.get("writes_allowed", -1)) != 0:
            raise ValueError("readonly config must set writes_allowed: 0")
        topics = raw.get("topics", {})
        services = raw.get("services", {})
        forbidden = raw.get("forbidden", {})
        config = cls(
            path=path,
            mode="readonly",
            writes_allowed=0,
            subscribe_topics=tuple(str(value) for value in topics.get("subscribe", [])),
            publish_topics=tuple(str(value) for value in topics.get("publish", [])),
            call_services=tuple(str(value) for value in services.get("call", [])),
            forbidden_topics=tuple(str(value) for value in forbidden.get("topics", [])),
            forbidden_services=tuple(str(value) for value in forbidden.get("services", [])),
            forbidden_devices=tuple(str(value) for value in forbidden.get("devices", [])),
        )
        overlap = (
            set(config.publish_topics) & set(config.forbidden_topics)
            | set(config.call_services) & set(config.forbidden_services)
        )
        if overlap:
            raise ValueError(f"readonly allowlist overlaps forbidden entries: {sorted(overlap)}")
        if not config.publish_topics:
            raise ValueError("readonly allowlist has no publish topics")
        return config

    def is_forbidden_topic(self, topic: str) -> bool:
        return topic in self.forbidden_topics

    def is_forbidden_service(self, service: str) -> bool:
        return service in self.forbidden_services


def load_lock_expectations(root: str | Path) -> tuple[str, str]:
    """Return the contract and profile hashes pinned by locks/ecosystem.lock.yaml."""
    root = Path(root)
    raw = yaml.safe_load((root / "locks" / "ecosystem.lock.yaml").read_text(encoding="utf-8"))
    contract_hash = str(raw["repos"]["jcheng-interfaces"]["contract_hash"])
    profile_hash = str(raw["robot"]["profile_sha256"])
    if not contract_hash.startswith("sha256:") or not profile_hash.startswith("sha256:"):
        raise ValueError("lockfile hashes must start with sha256:")
    return contract_hash, profile_hash


@dataclass(frozen=True)
class PreflightInput:
    role: str
    domain_id: int
    contract_hash: str
    expected_contract_hash: str
    profile_hash: str
    expected_profile_hash: str
    clock_offset_ms: float
    joint_state_age_s: float | None
    bus_age_s: float | None
    battery_age_s: float | None
    write_count: int = 0
    max_clock_offset_ms: float = 50.0
    max_state_age_s: float = 0.5
    max_battery_age_s: float = 5.0
    require_bus: bool = True


@dataclass
class PreflightReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    role: str = ""
    domain_id: int = 0
    require_bus: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "readonly_preflight",
            "ok": self.ok,
            "role": self.role,
            "domain_id": self.domain_id,
            "require_bus": self.require_bus,
            "writes": 0,
            "checks": self.checks,
            "errors": self.errors,
        }


def _check(report: PreflightReport, check_id: str, ok: bool, detail: str) -> None:
    report.checks.append({"id": check_id, "ok": bool(ok), "detail": detail})
    if not ok:
        report.errors.append(f"{check_id}: {detail}")


def run_preflight(
    domains: DomainConfig,
    readonly: ReadOnlyConfig,
    data: PreflightInput,
) -> PreflightReport:
    report = PreflightReport(
        ok=True,
        role=data.role,
        domain_id=int(data.domain_id),
        require_bus=bool(data.require_bus),
    )
    expected_domain = domains.domains.get(data.role)
    _check(
        report,
        "role",
        expected_domain is not None,
        f"role={data.role} expected_domain={expected_domain}",
    )
    if expected_domain is not None:
        _check(
            report,
            "domain",
            int(data.domain_id) == expected_domain,
            f"ROS_DOMAIN_ID={data.domain_id} expected={expected_domain}",
        )
    _check(
        report,
        "domain_override",
        not domains.forbid_domain_override or int(data.domain_id) in domains.domains.values(),
        "domain override is disabled",
    )
    _check(
        report,
        "readonly_mode",
        readonly.mode == "readonly" and readonly.writes_allowed == 0 and domains.writes_allowed == 0,
        "read-only allowlist forbids writes",
    )
    _check(
        report,
        "contract_hash",
        bool(data.expected_contract_hash)
        and data.contract_hash == data.expected_contract_hash,
        f"contract={data.contract_hash} expected={data.expected_contract_hash}",
    )
    _check(
        report,
        "profile_hash",
        bool(data.expected_profile_hash) and data.profile_hash == data.expected_profile_hash,
        f"profile={data.profile_hash} expected={data.expected_profile_hash}",
    )
    offset_ok = math.isfinite(float(data.clock_offset_ms)) and abs(float(data.clock_offset_ms)) <= float(
        data.max_clock_offset_ms
    )
    _check(
        report,
        "clock_offset",
        offset_ok,
        f"offset_ms={data.clock_offset_ms} limit={data.max_clock_offset_ms}",
    )
    _check(
        report,
        "joint_state_fresh",
        data.joint_state_age_s is not None and 0 <= data.joint_state_age_s <= data.max_state_age_s,
        f"age_s={data.joint_state_age_s} limit={data.max_state_age_s}",
    )
    if data.require_bus:
        _check(
            report,
            "bus_measured_fresh",
            data.bus_age_s is not None and 0 <= data.bus_age_s <= data.max_state_age_s,
            f"age_s={data.bus_age_s} limit={data.max_state_age_s}",
        )
    else:
        _check(report, "bus_measured_fresh", True, "bus measurement not required in this mode")
    _check(
        report,
        "battery_fresh",
        data.battery_age_s is not None and 0 <= data.battery_age_s <= data.max_battery_age_s,
        f"age_s={data.battery_age_s} limit={data.max_battery_age_s}",
    )
    _check(report, "writes", int(data.write_count) == 0, f"writes={data.write_count}")
    report.ok = not report.errors
    return report
