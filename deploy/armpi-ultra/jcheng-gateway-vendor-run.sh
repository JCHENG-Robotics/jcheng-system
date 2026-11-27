#!/usr/bin/env bash
# Run the hardware-backed R10 vendor gateway on the robot host.
#
# The host-side script only wraps `docker exec`. The node runs as the vendor
# `ubuntu` user inside ArmPiUltra, which already carries ROS 2 Humble,
# CycloneDDS, `ros_robot_controller_msgs` and the vendor bringup.
#
# This gateway is motion-capable: it refuses to start without
# `enable_motion:=true`, starts DISARMED, and needs a lease before it will
# execute anything. Start it only in an operator-authorized session; never
# enable it at boot. It binds 127.0.0.1 on the host network, so the operator
# console reaches it through an SSH tunnel.
#
# The R10 startup fix defers the first `GetBusServoState` until the executor
# is spinning; without it the node can deadlock in `__init__`.
set -euo pipefail

WS="${JCHENG_WS:-/home/ubuntu/jcheng-r10}"
ROOT="${JCHENG_ROOT:-${WS}}"
VENDOR_WS="${JCHENG_VENDOR_WS:-/home/ubuntu/ros2_ws/install}"
INTERFACES_WS="${JCHENG_INTERFACES_WS:-}"
PORT="${JCHENG_VENDOR_PORT:-8766}"
DOMAIN="${JCHENG_DOMAIN_ID:-0}"
BUS_SERVICE="${JCHENG_BUS_SERVICE:-/ros_robot_controller/bus_servo/get_state}"
EVIDENCE="${JCHENG_EVIDENCE:-/home/ubuntu/jcheng-gateway-vendor.jsonl}"

: "${JCHENG_GATEWAY_TOKEN:?JCHENG_GATEWAY_TOKEN must be set (see /etc/jcheng-system/vendor.env)}"

exec /usr/bin/docker exec -i \
    -e "JCHENG_GATEWAY_TOKEN=${JCHENG_GATEWAY_TOKEN}" \
    ArmPiUltra /bin/bash -lc "
set -euo pipefail
set +u
source /opt/ros/humble/setup.bash
if [[ -f '${VENDOR_WS}/setup.bash' ]]; then source '${VENDOR_WS}/setup.bash'; fi
if [[ -n '${INTERFACES_WS}' && -f '${INTERFACES_WS}/setup.bash' ]]; then source '${INTERFACES_WS}/setup.bash'; fi
source '${WS}/install/setup.bash'
set -u
export ROS_DOMAIN_ID='${DOMAIN}'
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///home/ubuntu/cyclonedds.xml
export JCHENG_ROOT='${ROOT}'
exec ros2 launch jcheng_bringup gateway_vendor.launch.py \
  root:='${ROOT}' \
  host:=127.0.0.1 \
  port:=${PORT} \
  enable_motion:=true \
  domain_id:=${DOMAIN} \
  bus_service:='${BUS_SERVICE}' \
  evidence_path:='${EVIDENCE}'
"
