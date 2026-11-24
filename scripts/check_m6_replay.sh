#!/usr/bin/env bash
# M6 offline gate: S13 replay is decision-only and never touches hardware.

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
  tools/replay_g8.py \
  tests/test_replay.py
do
  require_file "$f"
done
if [[ "$fail" -ne 0 ]]; then
  echo "M6 replay gate FAILED: missing assets" >&2
  exit 1
fi

if grep -RInE \
  --include='*.py' \
  'servo_controller_msgs|ros_robot_controller_msgs|/servo_controller|bus_servo/set_position|SetBusServoState|/cmd_vel|/move_action|/grasp|pyserial|import serial|/dev/tty|ttyUSB|ttyACM' \
  tools/replay_g8.py >/dev/null 2>&1; then
  echo "ERROR: replay tool contains a hardware command path." >&2
  fail=1
fi

if ! grep -q 'SafetySupervisor' tools/replay_g8.py; then
  echo "ERROR: replay must use the new validator." >&2
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
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M6 replay gate FAILED" >&2
  exit 1
fi
echo "M6 replay gate OK: decision-only replay, no hardware command path"
