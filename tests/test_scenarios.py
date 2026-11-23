from __future__ import annotations

from pathlib import Path

from jcheng_system_core.scenarios import REQUIRED, run_all


def test_required_mock_scenarios(root: Path) -> None:
    report = run_all(root)
    scenarios = {item["id"]: item for item in report["scenarios"]}
    assert report["summary"]["required_failed"] == 0, report
    assert report["summary"]["failed"] == 0, report
    for scenario_id in sorted(REQUIRED):
        assert scenarios[scenario_id]["status"] == "pass", scenarios[scenario_id]


def test_future_scenarios_are_explicit(root: Path) -> None:
    report = run_all(root)
    scenarios = {item["id"]: item for item in report["scenarios"]}
    assert scenarios["S13"]["status"] == "skipped"
    assert scenarios["S17"]["status"] == "skipped"
