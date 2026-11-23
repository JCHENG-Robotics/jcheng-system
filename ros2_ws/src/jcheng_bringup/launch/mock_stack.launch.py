"""Start the M3 mock gateway.

The token is intentionally not stored in the repository; pass it with
``token:=...`` or export JCHENG_GATEWAY_TOKEN before launching.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    token = LaunchConfiguration("token")
    port = LaunchConfiguration("port")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "token",
                default_value=os.environ.get("JCHENG_GATEWAY_TOKEN", ""),
                description="Gateway bearer token (required)",
            ),
            DeclareLaunchArgument("port", default_value="8766"),
            ExecuteProcess(
                cmd=[
                    "ros2",
                    "run",
                    "jcheng_gateway",
                    "jcheng_gateway",
                    "--port",
                    port,
                    "--token",
                    token,
                ],
                output="screen",
            ),
        ]
    )
