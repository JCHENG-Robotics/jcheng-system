"""Start the hardware-backed system gateway (M4/M5 target).

The node refuses to start without a token and without ``enable_motion:=true``.
It owns the only vendor bridge, polls the bus at about 0.25 s, and serves the
`/v1` HTTP gateway over the M5.2 executor. The vendor stack must already be
running; this launch starts no vendor container.

The token is read from `JCHENG_GATEWAY_TOKEN` by the node and is never placed
in the launch arguments. Use `host:=0.0.0.0` only on the robot network with the
token and firewall in place; the default is loopback.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    root = LaunchConfiguration("root")
    host = LaunchConfiguration("host")
    port = LaunchConfiguration("port")
    enable_motion = LaunchConfiguration("enable_motion")
    evidence_path = LaunchConfiguration("evidence_path")
    bus_service = LaunchConfiguration("bus_service")
    command_topic = LaunchConfiguration("command_topic")
    poll_s = LaunchConfiguration("poll_s")
    domain_id = LaunchConfiguration("domain_id")
    dds_uri = LaunchConfiguration("dds_uri")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "root",
                default_value=os.environ.get("JCHENG_ROOT", ""),
                description="Path to the jcheng-system checkout (contains configs/)",
            ),
            DeclareLaunchArgument(
                "host",
                default_value=os.environ.get("JCHENG_GATEWAY_HOST", "127.0.0.1"),
                description="Bind address; use 0.0.0.0 only on the robot network",
            ),
            DeclareLaunchArgument("port", default_value="8766"),
            DeclareLaunchArgument(
                "enable_motion",
                default_value="false",
                description="Must be true for the HTTP motion path to start",
            ),
            DeclareLaunchArgument(
                "evidence_path",
                default_value="/tmp/jcheng-gateway-vendor.jsonl",
                description="JSONL evidence path for the gateway",
            ),
            DeclareLaunchArgument(
                "bus_service",
                default_value="/ros_robot_controller/bus_servo/get_state",
            ),
            DeclareLaunchArgument(
                "command_topic", default_value="/servo_controller"
            ),
            DeclareLaunchArgument("poll_s", default_value="0.25"),
            DeclareLaunchArgument("domain_id", default_value="0"),
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
                executable="jcheng_gateway_vendor",
                name="jcheng_gateway_vendor",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        "root": root,
                        "host": host,
                        "port": port,
                        "enable_motion": enable_motion,
                        "evidence_path": evidence_path,
                        "bus_service": bus_service,
                        "command_topic": command_topic,
                        "poll_s": poll_s,
                    }
                ],
            ),
        ]
    )
