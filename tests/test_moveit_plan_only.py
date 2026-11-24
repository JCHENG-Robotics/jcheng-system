from __future__ import annotations

import pytest

from jcheng_moveit.plan_only import assert_plan_only, build_plan_only_goal


def test_plan_only_goal_is_never_executable() -> None:
    goal = build_plan_only_goal({"joint1": 0.05, "joint2": -0.2})
    assert_plan_only(goal)
    assert goal["planning_options"]["plan_only"] is True
    constraints = goal["request"]["goal_constraints"][0]["joint_constraints"]
    assert [item["joint_name"] for item in constraints] == ["joint1", "joint2"]
    assert "trajectory" not in goal
    assert "execute" not in goal


def test_plan_only_rejects_execution_goal() -> None:
    goal = build_plan_only_goal({"joint1": 0.05})
    goal["planning_options"]["plan_only"] = False
    with pytest.raises(ValueError, match="plan_only"):
        assert_plan_only(goal)

    goal = build_plan_only_goal({"joint1": 0.05})
    goal["trajectory"] = {"joint_names": ["joint1"], "points": []}
    with pytest.raises(ValueError, match="execution trajectory"):
        assert_plan_only(goal)


def test_plan_only_requires_targets() -> None:
    with pytest.raises(ValueError, match="target_joints"):
        build_plan_only_goal({})
