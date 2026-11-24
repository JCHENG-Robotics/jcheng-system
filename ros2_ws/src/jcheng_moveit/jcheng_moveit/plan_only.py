"""MoveIt plan-only goal construction and validation.

This module never calls an executor and never sends a trajectory to a
controller. It builds a MoveGroup/GetMotionPlan-shaped goal with
``planning_options.plan_only=True`` so a plan can be converted into the
JCHENG contract and submitted through the single motion entry.
"""

from __future__ import annotations

from typing import Any

DEFAULT_GROUP = "arm"
DEFAULT_FRAME = "base_link"
DEFAULT_TIP = "end_effector_link"
DEFAULT_TOLERANCE_RAD = 0.01


def build_plan_only_goal(
    target_joints: dict[str, float],
    *,
    group_name: str = DEFAULT_GROUP,
    frame_id: str = DEFAULT_FRAME,
    tip_link: str = DEFAULT_TIP,
    tolerance_rad: float = DEFAULT_TOLERANCE_RAD,
    planning_time_s: float = 5.0,
    attempts: int = 1,
) -> dict[str, Any]:
    """Build a plan-only goal dictionary; no hardware or ROS calls."""
    if not target_joints:
        raise ValueError("target_joints is required")
    if not group_name.strip():
        raise ValueError("group_name is required")
    if tolerance_rad <= 0:
        raise ValueError("tolerance_rad must be positive")
    constraints = []
    for name, position in target_joints.items():
        value = float(position)
        constraints.append(
            {
                "joint_name": str(name),
                "position": value,
                "tolerance_above": float(tolerance_rad),
                "tolerance_below": float(tolerance_rad),
                "weight": 1.0,
            }
        )
    return {
        "request": {
            "group_name": str(group_name),
            "goal_constraints": [{"joint_constraints": constraints}],
            "num_planning_attempts": max(1, int(attempts)),
            "allowed_planning_time": float(planning_time_s),
        },
        "planning_options": {
            "plan_only": True,
            "look_around": False,
            "planning_scene_diff": {
                "is_diff": True,
                "robot_state": {"is_diff": True},
            },
        },
        "frame_id": str(frame_id),
        "tip_link": str(tip_link),
    }


def assert_plan_only(goal: dict[str, Any]) -> None:
    """Reject a goal that could execute instead of only planning."""
    options = goal.get("planning_options")
    if not isinstance(options, dict):
        raise ValueError("planning_options is required")
    if options.get("plan_only") is not True:
        raise ValueError("planning_options.plan_only must be true")
    request = goal.get("request")
    if not isinstance(request, dict) or not request.get("group_name"):
        raise ValueError("request.group_name is required")
    if "execute" in goal or "execute" in request:
        raise ValueError("plan-only goal must not contain an execute field")
    if "trajectory" in goal:
        raise ValueError("plan-only goal must not contain an execution trajectory")
