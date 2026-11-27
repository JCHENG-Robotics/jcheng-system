from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools.verify_lock import (
    _module_checkout_errors,
    _module_entry_errors,
    interfaces_range_contains,
    verify,
)

HASH = "sha256:" + "a" * 64


def test_lockfile_matches_local_configs(root: Path) -> None:
    assert verify(root) == []


def test_lockfile_requires_evidence(root: Path) -> None:
    if (root / "e2e" / "reports" / "m3-mock.json").exists():
        pytest.skip("an e2e report is present; the missing-evidence path is covered in CI")
    errors = verify(root, require_evidence=True)
    assert any("e2e report" in error for error in errors)


@pytest.mark.parametrize(
    ("spec", "version", "expected"),
    [
        (">=0.2,<0.3", "0.2.0", True),
        (">=0.2,<0.3", "0.2", True),
        (">=0.2,<0.3", "0.3.0", False),
        (">=0.2,<0.3", "0.1.9", False),
        (">=0.1,<0.2", "0.2.0", False),
        (">=0.2", "0.2.5", True),
        ("<0.3", "0.2.9", True),
        ("==0.2.0", "0.2.0", True),
        ("~0.2", "0.2.0", False),
        ("", "0.2.0", False),
    ],
)
def test_interfaces_range_contains(spec: str, version: str, expected: bool) -> None:
    assert interfaces_range_contains(spec, version) is expected


def test_module_entry_requires_full_commit_and_range() -> None:
    errors = _module_entry_errors(
        "jcheng-teleop",
        {"version": "0.3.0", "commit": "eca8223", "interfaces": ">=0.1,<0.2"},
        "0.2.0",
    )
    assert any("40-hex" in error for error in errors)
    assert any("does not include 0.2.0" in error for error in errors)


def test_module_entry_rejects_system_repositories() -> None:
    errors = _module_entry_errors(
        "jcheng-system",
        {"version": "0.3.0", "commit": "a" * 40, "interfaces": ">=0.2,<0.3"},
        "0.2.0",
    )
    assert errors == ["module jcheng-system: L0/L1/L2 repositories belong in repos, not modules"]


def _make_module_repo(tmp_path: Path, version: str = "0.1.0", contract_hash: str = HASH) -> tuple[Path, str]:
    path = tmp_path / "jcheng-fake"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "VERSION").write_text(version + "\n", encoding="utf-8")
    (path / "capability.yaml").write_text(
        "schema_version: 1\n"
        "name: jcheng-fake\n"
        f"version: {version}\n"
        "contract:\n"
        '  interfaces: ">=0.2,<0.3"\n'
        f'  contract_hash: "{contract_hash}"\n'
        '  system_api: ">=0.1,<0.2"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return path, commit


def test_module_checkout_matches_pin(tmp_path: Path) -> None:
    path, commit = _make_module_repo(tmp_path)
    errors = _module_checkout_errors(
        "jcheng-fake",
        {"version": "0.1.0", "commit": commit},
        path,
        HASH,
        "0.2.0",
        "0.1.0",
    )
    assert errors == []


def test_module_checkout_rejects_version_and_hash_mismatch(tmp_path: Path) -> None:
    path, commit = _make_module_repo(tmp_path)
    errors = _module_checkout_errors(
        "jcheng-fake", {"version": "9.9.9", "commit": commit}, path, "sha256:" + "0" * 64, "0.2.0"
    )
    assert any("VERSION at" in error for error in errors)
    assert any("contract_hash" in error for error in errors)


def test_module_checkout_rejects_system_api_mismatch(tmp_path: Path) -> None:
    path, commit = _make_module_repo(tmp_path)
    errors = _module_checkout_errors(
        "jcheng-fake",
        {"version": "0.1.0", "commit": commit},
        path,
        HASH,
        "0.2.0",
        "0.2.0",
    )
    assert any("system_api" in error for error in errors)
