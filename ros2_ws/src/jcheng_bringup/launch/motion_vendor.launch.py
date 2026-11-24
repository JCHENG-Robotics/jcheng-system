"""Start the M5.2 vendor runtime.

It starts no vendor node and no container. The vendor stack must already be
running. Only the reviewed `jcheng_vendor` runtime is started.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    root = LaunchConfiguration("root")
    domain_id = LaunchConfiguration("domain_id")
    evidence_path = LaunchConfiguration("evidence_path")
    bus_service = LaunchConfiguration("bus_service")
    dds_uri = LaunchConfiguration("dds_uri")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "root",
                default_value=os.environ.get("JCHENG_ROOT", ""),
                description="Path to the jcheng-system checkout (contains configs/)",
            ),
            DeclareLaunchArgument("domain_id", default_value="0"),
            DeclareLaunchArgument(
                "evidence_path",
                default_value="/tmp/jcheng-m5-vendor.jsonl",
                description="JSONL evidence path for supervised M5.2 motion",
            ),
            DeclareLaunchArgument(
                "bus_service",
                default_value="/ros_robot_controller/bus_servo/get_state",
            ),
            DeclareLaunchArgument(
                "dds_uri",
                default_value=os.environ.get(
                    "JCHENG_CYCLONEDDS_URI", "file:///home/ubuntu/cyclonedds.xml"
                ),
            ),
            SetEnvironmentVariable("ROS_DOMAIN_ID", domain_id),
            SetEnvironmentVariable("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp"),
            SetEnvironmentVariable("CYCLONEDDS_URI", dds_uri),
            Node(
                package="jcheng_vendor",
                executable="jcheng_vendor_runtime",
                name="jcheng_vendor_runtime",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "root": root,
                        "evidence_path": evidence_path,
                        "bus_service": bus_service,
                        "command_topic": "/servo_controller",
                    }
                ],
            ),
        ]
    )
