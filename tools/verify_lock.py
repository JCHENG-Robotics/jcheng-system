#!/usr/bin/env python3
"""Verify the ecosystem lockfile against the local configs and (optionally) remote tags."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_version(text: str) -> tuple[int, ...] | None:
    parts = text.strip().split(".")
    if not 2 <= len(parts) <= 4:
        return None
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _padded(value: tuple[int, ...], length: int) -> tuple[int, ...]:
    return value + (0,) * (length - len(value))


def interfaces_range_contains(spec: str, version: str) -> bool:
    """Return True when a range such as ``>=0.2,<0.3`` contains ``version``."""
    target = _parse_version(version)
    if target is None:
        return False
    clauses = [clause.strip() for clause in spec.split(",") if clause.strip()]
    if not clauses:
        return False
    for clause in clauses:
        for operator in (">=", "<=", "==", ">", "<"):
            if not clause.startswith(operator):
                continue
            boundary = _parse_version(clause[len(operator):])
            if boundary is None:
                return False
            width = max(len(target), len(boundary))
            left, right = _padded(target, width), _padded(boundary, width)
            if operator == ">=" and not left >= right:
                return False
            if operator == ">" and not left > right:
                return False
            if operator == "<=" and not left <= right:
                return False
            if operator == "<" and not left < right:
                return False
            if operator == "==" and not left == right:
                return False
            break
        else:
            return False
    return True


def _module_entry_errors(name: str, entry: object, locked_version: str) -> list[str]:
    if name in ("jcheng-interfaces", "jcheng-system"):
        return [f"module {name}: L0/L1/L2 repositories belong in repos, not modules"]
    if not isinstance(entry, dict):
        return [f"module {name}: entry must be a mapping"]
    errors: list[str] = []
    commit = str(entry.get("commit", ""))
    if not COMMIT_PATTERN.match(commit):
        errors.append(f"module {name}: commit must be a full 40-hex SHA")
    version = str(entry.get("version", ""))
    if _parse_version(version) is None:
        errors.append(f"module {name}: version {version!r} is not a dotted version")
    spec = str(entry.get("interfaces", ""))
    if not interfaces_range_contains(spec, locked_version):
        errors.append(
            f"module {name}: interfaces {spec!r} does not include {locked_version}"
        )
    return errors


def _git_show(path: Path, commit: str, relative: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "show", f"{commit}:{relative}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _module_checkout_errors(
    name: str,
    entry: dict,
    path: Path,
    locked_hash: str,
    locked_version: str,
    api_version: str | None = None,
) -> list[str]:
    """Verify the pinned commit's VERSION and capability.yaml in a local checkout."""
    errors: list[str] = []
    commit = str(entry["commit"])
    try:
        exists = subprocess.run(
            ["git", "-C", str(path), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"module {name}: cannot inspect {path}: {exc}"]
    if exists.returncode != 0:
        return [f"module {name}: commit {commit} is not present in {path}"]

    version = _git_show(path, commit, "VERSION")
    if version is None:
        errors.append(f"module {name}: VERSION missing at {commit}")
    elif version.strip() != str(entry["version"]):
        errors.append(
            f"module {name}: VERSION at {commit} is {version.strip()!r}, "
            f"lock says {entry['version']!r}"
        )

    capability = _git_show(path, commit, "capability.yaml")
    if capability is None:
        errors.append(f"module {name}: capability.yaml missing at {commit}")
        return errors
    try:
        manifest = yaml.safe_load(capability) or {}
    except yaml.YAMLError as exc:
        errors.append(f"module {name}: capability.yaml is invalid at {commit}: {exc}")
        return errors
    contract = manifest.get("contract") if isinstance(manifest, dict) else None
    if not isinstance(contract, dict):
        return errors
    declared_hash = contract.get("contract_hash")
    if declared_hash is not None and str(declared_hash) != locked_hash:
        errors.append(
            f"module {name}: contract_hash {declared_hash} != lock {locked_hash}"
        )
    declared_spec = contract.get("interfaces")
    if declared_spec is not None and not interfaces_range_contains(
        str(declared_spec), locked_version
    ):
        errors.append(
            f"module {name}: declared interfaces {declared_spec!r} "
            f"does not include {locked_version}"
        )
    declared_api = contract.get("system_api")
    if (
        api_version
        and declared_api is not None
        and not interfaces_range_contains(str(declared_api), api_version)
    ):
        errors.append(
            f"module {name}: declared system_api {declared_api!r} "
            f"does not include {api_version}"
        )
    return errors


def verify(
    root: str | Path,
    *,
    remote: bool = False,
    require_evidence: bool = False,
    interfaces_root: str | Path | None = None,
    module_root: str | Path | None = None,
) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    lock_path = root / "locks" / "ecosystem.lock.yaml"
    if not lock_path.is_file():
        return [f"missing lockfile: {lock_path}"]
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    for key in ("schema_version", "release", "channel", "toolchain", "repos", "modules", "robot", "verification"):
        if key not in lock:
            errors.append(f"lockfile: missing {key}")
    if errors:
        return errors
    if lock["schema_version"] != 1:
        errors.append("lockfile: schema_version must be 1")
    if lock["channel"] not in ("dev", "rc", "stable"):
        errors.append("lockfile: invalid channel")

    contract_path = root / "configs" / "contract.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    interfaces = contract["interfaces"]
    api_path = root / "configs" / "api.yaml"
    api_version: str | None = None
    if api_path.is_file():
        api = yaml.safe_load(api_path.read_text(encoding="utf-8")) or {}
        value = str(api.get("api_version", ""))
        if _parse_version(value) is None:
            errors.append("configs/api.yaml: api_version is not a dotted version")
        else:
            api_version = value
    repo = lock["repos"].get("jcheng-interfaces")
    if not isinstance(repo, dict):
        errors.append("lockfile: jcheng-interfaces pin is missing")
        return errors
    expected = {
        "version": str(interfaces["version"]),
        "tag": str(interfaces["tag"]),
        "commit": str(interfaces["commit"]),
        "contract_hash": str(interfaces["contract"]["contract_hash"]),
    }
    for key, value in expected.items():
        if str(repo.get(key)) != value:
            errors.append(f"lockfile: jcheng-interfaces.{key} expected {value}, got {repo.get(key)}")

    profile_path = root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    profile_hash = sha256(profile_path)
    if str(lock["robot"].get("profile_sha256")) != profile_hash:
        errors.append(
            f"lockfile: profile hash expected {lock['robot'].get('profile_sha256')}, got {profile_hash}"
        )

    modules = lock.get("modules")
    if not isinstance(modules, dict):
        errors.append("lockfile: modules must be a mapping")
    else:
        for name, entry in modules.items():
            errors.extend(_module_entry_errors(str(name), entry, expected["version"]))
        if module_root:
            module_root_path = Path(module_root)
            for name, entry in modules.items():
                path = module_root_path / str(name)
                if not path.is_dir():
                    errors.append(f"module {name}: checkout missing at {path}")
                    continue
                if not isinstance(entry, dict):
                    continue
                if not COMMIT_PATTERN.match(str(entry.get("commit", ""))):
                    continue
                errors.extend(
                    _module_checkout_errors(
                        str(name),
                        entry,
                        path,
                        expected["contract_hash"],
                        expected["version"],
                        api_version,
                    )
                )

    if interfaces_root:
        manifest_path = Path(interfaces_root) / "interface_manifest.json"
        if not manifest_path.is_file():
            errors.append(f"interfaces manifest missing: {manifest_path}")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("contract_hash") != expected["contract_hash"]:
                errors.append(
                    f"interfaces manifest hash {manifest.get('contract_hash')} != lock {expected['contract_hash']}"
                )

    report_path = root / str(lock["verification"].get("e2e_report", ""))
    if require_evidence:
        if not report_path.is_file():
            errors.append(f"missing e2e report: {report_path}")
        else:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary = report.get("summary", {})
            if summary.get("required_failed", 1) != 0 or summary.get("failed", 1) != 0:
                errors.append(f"e2e report has failures: {summary}")

    if remote:
        try:
            result = subprocess.run(
                [
                    "git",
                    "ls-remote",
                    str(repo["url"]),
                    f"refs/tags/{repo['tag']}",
                    f"refs/tags/{repo['tag']}^{{}}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            refs = {}
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    refs[parts[1]] = parts[0]
            peeled = refs.get(f"refs/tags/{repo['tag']}^{{}}") or refs.get(f"refs/tags/{repo['tag']}")
            if peeled != expected["commit"]:
                errors.append(f"remote tag {repo['tag']} points to {peeled}, expected {expected['commit']}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"remote check failed: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--require-evidence", action="store_true")
    parser.add_argument("--interfaces-root", default=None)
    parser.add_argument(
        "--module-root",
        default=None,
        help="workspace root with one checkout per locked module (e.g. the parent of the jcheng-* repos)",
    )
    args = parser.parse_args()
    errors = verify(
        args.root,
        remote=args.remote,
        require_evidence=args.require_evidence,
        interfaces_root=args.interfaces_root,
        module_root=args.module_root,
    )
    if errors:
        print("LOCK CHECK FAILED", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("LOCK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
