#!/usr/bin/env bash
# M3 repository gate: mock-only code, no real hardware access, no runtime transport yet.

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
  .github/workflows/ci.yml \
  .github/CODEOWNERS \
  scripts/check_repo_gate.sh
do
  require_file "$f"
done

for pkg in \
  jcheng_system_core \
  jcheng_bringup \
  jcheng_safety \
  jcheng_authority \
  jcheng_motion \
  jcheng_hardware \
  jcheng_gateway \
  jcheng_guard \
  jcheng_testkit
do
  require_file "ros2_ws/src/${pkg}/package.xml"
done

if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

if ! grep -q 'M3' docs/M3-SCOPE.md || ! grep -qi 'mock' docs/M3-SCOPE.md; then
  echo "ERROR: M3 scope must document the mock boundary." >&2
  fail=1
fi

if grep -RIl --include='*.py' -E '\brclpy\b|\brclcpp\b' ros2_ws/src >/dev/null 2>&1; then
  echo "ERROR: ROS transport (rclpy/rclcpp) is not allowed in M3; it belongs to M4." >&2
  fail=1
fi

if grep -RIl --include='*.py' -E '/dev/(tty|rrc)|serial\.Serial|import serial|pyserial|ttyUSB|ttyACM|ros_robot_controller|servo_controller_msgs|/cmd_vel|/move_action' ros2_ws/src >/dev/null 2>&1; then
  echo "ERROR: real hardware access or vendor command path found in M3 code." >&2
  fail=1
fi

if find . -path ./.git -prune -o -path ./build -prune -o -path ./install -prune -o -path ./log -prune -o -name '*.launch.py' -print | grep -v 'jcheng_bringup/launch/mock_stack.launch.py' | grep -q .; then
  echo "ERROR: only the mock bringup launch file is allowed in M3." >&2
  fail=1
fi

if grep -RIlE \
  --exclude-dir=.git \
  --exclude-dir=build \
  --exclude-dir=install \
  --exclude-dir=log \
  --exclude='check_repo_gate.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M3 repository gate FAILED" >&2
  exit 1
fi
echo "M3 repository gate OK: 9 packages, mock-only, no hardware access, no LICENSE"
