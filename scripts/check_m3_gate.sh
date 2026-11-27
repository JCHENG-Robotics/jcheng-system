#!/usr/bin/env bash
# Frozen M3 gate: the nine Mock packages must stay pure Python and hardware-free.
# M4 read-only code lives outside this package set and is checked by check_m4_readonly.sh.

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
  README.md \
  VERSION \
  CHANGELOG.md \
  README.zh-CN.md \
  ROADMAP.md \
  ACCEPTANCE.md \
  LICENSE-REVIEW.md \
  pytest.ini \
  configs/contract.yaml \
  configs/robots/armpi_ultra/profile.yaml \
  configs/robots/armpi_ultra/safety_overlay.yaml \
  locks/ecosystem.lock.yaml \
  docs/ARCHITECTURE.md \
  docs/SAFETY.md \
  docs/ACCEPTANCE.md \
  docs/OPERATIONS.md \
  docs/M3-SCOPE.md \
  tools/verify_lock.py \
  ros2_ws/src/jcheng_bringup/launch/mock_stack.launch.py
do
  require_file "$f"
done

m3_packages=(
  jcheng_system_core
  jcheng_bringup
  jcheng_safety
  jcheng_authority
  jcheng_motion
  jcheng_hardware
  jcheng_gateway
  jcheng_guard
  jcheng_testkit
)
m3_paths=()
for pkg in "${m3_packages[@]}"; do
  require_file "ros2_ws/src/${pkg}/package.xml"
  m3_paths+=("ros2_ws/src/${pkg}")
done

if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

if ! grep -q 'M3' docs/M3-SCOPE.md || ! grep -qi 'mock' docs/M3-SCOPE.md; then
  echo "ERROR: M3 scope must document the mock boundary." >&2
  fail=1
fi

if grep -RIl --include='*.py' -E '\brclpy\b|\brclcpp\b' "${m3_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: ROS transport is not allowed in the M3 package set." >&2
  fail=1
fi

if grep -RIl --include='*.py' --exclude='readonly.launch.py' --exclude='motion_vendor.launch.py' --exclude='gateway_vendor.launch.py' -E '/dev/(tty|rrc)|serial\.Serial|import serial|pyserial|ttyUSB|ttyACM|ros_robot_controller|servo_controller_msgs|/cmd_vel|/move_action' "${m3_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: real hardware access or vendor command path found in M3 code." >&2
  fail=1
fi

if grep -RIlE \
  --exclude-dir=.git \
  --exclude-dir=build \
  --exclude-dir=install \
  --exclude-dir=log \
  --exclude='check_repo_gate.sh' \
  --exclude='check_m3_gate.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M3 gate FAILED" >&2
  exit 1
fi
echo "M3 gate OK: 9 Mock packages, no ROS transport, no hardware access, no LICENSE"
