from __future__ import annotations

from pathlib import Path

import pytest

from tools.verify_lock import verify


def test_lockfile_matches_local_configs(root: Path) -> None:
    assert verify(root) == []


def test_lockfile_requires_evidence(root: Path) -> None:
    if (root / "e2e" / "reports" / "m3-mock.json").exists():
        pytest.skip("an e2e report is present; the missing-evidence path is covered in CI")
    errors = verify(root, require_evidence=True)
    assert any("e2e report" in error for error in errors)
