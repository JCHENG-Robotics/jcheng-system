#!/usr/bin/env bash
# Run the jcheng-system read-only /v1 gateway on the robot host.
#
# The host-side script only wraps `docker exec`. The node runs as the vendor
# `ubuntu` user inside the ArmPiUltra container, which already carries ROS 2
# Humble, CycloneDDS and `ros_robot_controller_msgs`. Everything here is
# read-only: the gateway exposes `writes: 0` and rejects every non-GET.
#
# The gateway binds `127.0.0.1` on the host network (`--network host`), so it is
# reachable from the robot itself only. The operator console reaches it through
# an SSH tunnel; do not widen the bind just to reach it from the laptop.
set -euo pipefail

WS="${JCHENG_WS:-/home/ubuntu/jcheng-m4}"
ROOT="${JCHENG_ROOT:-${WS}/jcheng-system}"
PORT="${JCHENG_READONLY_PORT:-8767}"
BUS_SERVICE="${JCHENG_BUS_SERVICE:-/ros_robot_controller/bus_servo/get_state}"

: "${JCHENG_GATEWAY_TOKEN:?JCHENG_GATEWAY_TOKEN must be set (see /etc/jcheng-system/readonly.env)}"

exec /usr/bin/docker exec -i \
    -e "JCHENG_GATEWAY_TOKEN=${JCHENG_GATEWAY_TOKEN}" \
    ArmPiUltra /bin/bash -lc "
set -euo pipefail
set +u
source /opt/ros/humble/setup.bash
source /home/ubuntu/ros2_ws/install/setup.bash 2>/dev/null
source '${WS}/install/setup.bash'
set -u
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
exec ros2 run jcheng_telemetry jcheng_telemetry_node --ros-args \
  -p root:='${ROOT}' \
  -p domain_id:=0 \
  -p gateway_port:=${PORT} \
  -p require_bus:=true \
  -p bus_service:=${BUS_SERVICE} \
  -p gateway_host:=127.0.0.1
"
