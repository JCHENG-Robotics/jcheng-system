"""Start the M5.1 Mock ROS transport runtime.

This launch file starts no vendor node and no hardware bridge. It is for
contract/service/action tests only; real motion is M5.2 and needs separate
authorization.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    root = LaunchConfiguration("root")
    evidence_path = LaunchConfiguration("evidence_path")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "root",
                default_value=os.environ.get("JCHENG_ROOT", ""),
                description="Path to the jcheng-system checkout (contains configs/)",
            ),
            DeclareLaunchArgument(
                "evidence_path",
                default_value="",
                description="Optional JSONL evidence path for Mock motion records",
            ),
            Node(
                package="jcheng_runtime",
                executable="jcheng_runtime",
                name="jcheng_runtime",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "root": root,
                        "evidence_path": evidence_path,
                    }
                ],
            ),
        ]
    )
