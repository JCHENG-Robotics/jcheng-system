#!/usr/bin/env bash
# M6 S17 gate: MoveIt goals are plan-only and contain no execution path.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

fail=0
require_file() {
  if [[ ! -f "$1" ]]; then
    echo "MISSING: $1" >&2
    fail=1
  fi
}

for f in \
  docs/M6-SCOPE.md \
  ros2_ws/src/jcheng_moveit/package.xml \
  ros2_ws/src/jcheng_moveit/jcheng_moveit/plan_only.py \
  tests/test_moveit_plan_only.py
do
  require_file "$f"
done
if [[ "$fail" -ne 0 ]]; then
  echo "M6 MoveIt gate FAILED: missing assets" >&2
  exit 1
fi

if ! grep -q '"plan_only": True' ros2_ws/src/jcheng_moveit/jcheng_moveit/plan_only.py; then
  echo "ERROR: plan-only goal builder must set plan_only True." >&2
  fail=1
fi

if grep -RInE \
  --include='*.py' \
  'servo_controller_msgs|ros_robot_controller_msgs|/servo_controller|bus_servo/set_position|SetBusServoState|/move_action|/cmd_vel|/grasp|pyserial|import serial|/dev/tty|ttyUSB|ttyACM' \
  ros2_ws/src/jcheng_moveit >/dev/null 2>&1; then
  echo "ERROR: MoveIt package contains a hardware or execution path." >&2
  fail=1
fi

if grep -RInE \
  --exclude-dir=.git \
  --exclude-dir=build \
  --exclude-dir=install \
  --exclude-dir=log \
  --exclude='check_repo_gate.sh' \
  --exclude='check_m3_gate.sh' \
  --exclude='check_m4_readonly.sh' \
  --exclude='check_m5_mock.sh' \
  --exclude='check_m5_vendor.sh' \
  --exclude='check_m6_replay.sh' \
  --exclude='check_m6_moveit.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M6 MoveIt gate FAILED" >&2
  exit 1
fi
echo "M6 MoveIt gate OK: plan-only goals, no execution or hardware path"
