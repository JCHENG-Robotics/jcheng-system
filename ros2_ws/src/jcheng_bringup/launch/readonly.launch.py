"""Start the M4 read-only telemetry node.

This launch file never starts a vendor container, a motion node, or a command
publisher. The gateway token is passed as a launch argument or environment
variable and is never stored in the repository.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    root = LaunchConfiguration("root")
    domain_id = LaunchConfiguration("domain_id")
    gateway_port = LaunchConfiguration("gateway_port")
    gateway_token = LaunchConfiguration("gateway_token")
    require_bus = LaunchConfiguration("require_bus")
    bus_service = LaunchConfiguration("bus_service")
    evidence_path = LaunchConfiguration("evidence_path")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "root",
                default_value=os.environ.get("JCHENG_ROOT", ""),
                description="Path to the jcheng-system checkout (contains configs/)",
            ),
            DeclareLaunchArgument("domain_id", default_value="0"),
            DeclareLaunchArgument("gateway_port", default_value="8767"),
            DeclareLaunchArgument(
                "gateway_token",
                default_value=os.environ.get("JCHENG_GATEWAY_TOKEN", ""),
                description="Read-only gateway bearer token (required for the gateway)",
            ),
            DeclareLaunchArgument("require_bus", default_value="true"),
            DeclareLaunchArgument(
                "bus_service",
                default_value="/ros_robot_controller/bus_servo/get_state",
            ),
            DeclareLaunchArgument(
                "evidence_path",
                default_value=os.environ.get("JCHENG_READONLY_EVIDENCE", ""),
                description="Optional JSONL path for the read-only evidence run",
            ),
            SetEnvironmentVariable("ROS_DOMAIN_ID", domain_id),
            SetEnvironmentVariable("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp"),
            Node(
                package="jcheng_telemetry",
                executable="jcheng_telemetry_node",
                name="jcheng_telemetry",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "root": root,
                        "domain_id": domain_id,
                        "gateway_port": gateway_port,
                        "gateway_token": gateway_token,
                        "require_bus": require_bus,
                        "bus_service": bus_service,
                        "gateway_host": "127.0.0.1",
                        "evidence_path": evidence_path,
                    }
                ],
            ),
        ]
    )
