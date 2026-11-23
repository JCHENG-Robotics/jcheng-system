#!/usr/bin/env python3
"""Verify the ecosystem lockfile against the local configs and (optionally) remote tags."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verify(
    root: str | Path,
    *,
    remote: bool = False,
    require_evidence: bool = False,
    interfaces_root: str | Path | None = None,
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
    args = parser.parse_args()
    errors = verify(
        args.root,
        remote=args.remote,
        require_evidence=args.require_evidence,
        interfaces_root=args.interfaces_root,
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
