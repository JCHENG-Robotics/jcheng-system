#!/usr/bin/env bash
# M5.2 gate: supervised vendor motion uses only /servo_controller and GetBusServoState.

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
  ros2_ws/src/jcheng_vendor/package.xml \
  ros2_ws/src/jcheng_vendor/jcheng_vendor/executor.py \
  ros2_ws/src/jcheng_vendor/jcheng_vendor/bridge.py \
  ros2_ws/src/jcheng_vendor/jcheng_vendor/node.py \
  ros2_ws/src/jcheng_bringup/launch/motion_vendor.launch.py \
  tests_ros/test_vendor_executor.py
do
  require_file "$f"
done
if [[ "$fail" -ne 0 ]]; then
  echo "M5.2 gate FAILED: missing assets" >&2
  exit 1
fi

vendor_paths=("ros2_ws/src/jcheng_vendor/jcheng_vendor")

if grep -RInE \
  --include='*.py' \
  'bus_servo/set_position|set_motor_speeds|SetBusServoState|SetPWMServoState|/cmd_vel|/grasp|/move_action|pyserial|import serial|/dev/tty|ttyUSB|ttyACM' \
  "${vendor_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: forbidden raw bus/device path in M5.2 code." >&2
  fail=1
fi

python3 - "$ROOT" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
paths = sorted((root / "ros2_ws/src/jcheng_vendor/jcheng_vendor").glob("*.py"))
text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
publishers = set(re.findall(r"create_publisher\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
clients = set(re.findall(r"create_client\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
assert publishers <= {"ServosPosition", "SafetyState"}, f"unexpected publishers: {publishers}"
assert clients <= {"GetBusServoState"}, f"unexpected clients: {clients}"
assert "ServosPosition" in text and "/servo_controller" in text
assert "GetBusServoState" in text
assert "HardwareExecutor" in text
print(f"M5.2 publisher/client gate OK: publishers={sorted(publishers)} clients={sorted(clients)}")
PY

if grep -RInE \
  --include='*.py' \
  'MockStack|jcheng_runtime' \
  ros2_ws/src/jcheng_vendor/jcheng_vendor/executor.py >/dev/null 2>&1; then
  echo "ERROR: executor must stay independent of the Mock/runtime node." >&2
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
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M5.2 gate FAILED" >&2
  exit 1
fi
echo "M5.2 gate OK: single /servo_controller path, read-only bus service, no raw bus/device"
