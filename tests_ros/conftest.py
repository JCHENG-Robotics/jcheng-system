from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT,
    ROOT / "ros2_ws" / "src" / "jcheng_system_core",
    ROOT / "ros2_ws" / "src" / "jcheng_runtime",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT
