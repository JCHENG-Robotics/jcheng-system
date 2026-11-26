#!/usr/bin/env bash
# M5.1 gate: the ROS transport runtime must remain Mock-only.

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
  docs/M5-SCOPE.md \
  ros2_ws/src/jcheng_runtime/package.xml \
  ros2_ws/src/jcheng_runtime/jcheng_runtime/node.py \
  ros2_ws/src/jcheng_runtime/jcheng_runtime/conversions.py \
  ros2_ws/src/jcheng_bringup/launch/motion_mock.launch.py \
  tests_ros/test_runtime.py
do
  require_file "$f"
done
if [[ "$fail" -ne 0 ]]; then
  echo "M5.1 gate FAILED: missing assets" >&2
  exit 1
fi

runtime_paths=("ros2_ws/src/jcheng_runtime/jcheng_runtime")

if grep -RInE \
  --include='*.py' \
  'servo_controller_msgs|ros_robot_controller_msgs|/servo_controller|set_motor_speeds|SetBusServoState|SetPWMServoState|bus_servo/set_position|/grasp|/move_action|/cmd_vel|pyserial|import serial|/dev/tty|ttyUSB|ttyACM' \
  "${runtime_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: hardware command path found in M5.1 runtime." >&2
  fail=1
fi

if ! grep -RIn --include='*.py' 'MockStack' "${runtime_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: M5.1 runtime must use MockStack." >&2
  fail=1
fi

python3 - "$ROOT" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
paths = sorted((root / "ros2_ws/src/jcheng_runtime/jcheng_runtime").glob("*.py"))
text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
publishers = set(re.findall(r"create_publisher\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
clients = set(re.findall(r"create_client\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
topics = set(re.findall(r'create_publisher\s*\(\s*[A-Za-z_][\w.]*\s*,\s*"([^"]+)"', text, re.S))
assert publishers <= {"SafetyState", "JointState", "RobotState"}, f"unexpected publishers: {publishers}"
assert topics <= {
    "/jcheng/telemetry/safety",
    "/sim/joint_command",
    "/jcheng/telemetry/state",
}, f"unexpected topics: {topics}"
assert not clients, f"M5.1 must not create clients: {clients}"
assert "ExecuteMotion" in text, "ExecuteMotion action missing"
# The simulation bridge is test-only: it must stay off by default.
assert 'declare_parameter("sim_bridge", False)' in text, "sim bridge must default to off"
print(
    "M5.1 publisher/client gate OK: "
    f"publishers={sorted(publishers)} topics={sorted(topics)} clients=[]"
)
PY

if grep -RInE \
  --exclude-dir=.git \
  --exclude-dir=build \
  --exclude-dir=install \
  --exclude-dir=log \
  --exclude='check_repo_gate.sh' \
  --exclude='check_m3_gate.sh' \
  --exclude='check_m4_readonly.sh' \
  --exclude='check_m5_mock.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M5.1 gate FAILED" >&2
  exit 1
fi
echo "M5.1 gate OK: Mock-only runtime (test-only sim bridge, read-only state tap), lease/safety/action, no hardware command path"
