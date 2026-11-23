#!/usr/bin/env bash
# M4 gate: read-only access only. No command publisher, write service, or device.

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
  docs/M4-SCOPE.md \
  configs/domains.yaml \
  configs/readonly.yaml \
  configs/robots/armpi_ultra/readonly_mapping.yaml \
  configs/dds/cyclonedds.domain0.xml \
  configs/dds/cyclonedds.domain41.xml \
  configs/dds/cyclonedds.domain42.xml \
  configs/dds/cyclonedds.domain43.xml \
  ros2_ws/src/jcheng_telemetry/package.xml \
  ros2_ws/src/jcheng_telemetry/jcheng_telemetry/node.py \
  ros2_ws/src/jcheng_telemetry/jcheng_telemetry/probe.py \
  ros2_ws/src/jcheng_telemetry/jcheng_telemetry/gateway.py \
  ros2_ws/src/jcheng_telemetry/jcheng_telemetry/preflight.py \
  ros2_ws/src/jcheng_hardware/jcheng_hardware/readonly.py \
  ros2_ws/src/jcheng_bringup/launch/readonly.launch.py
do
  require_file "$f"
done

if [[ "$fail" -ne 0 ]]; then
  echo "M4 gate FAILED: missing read-only assets" >&2
  exit 1
fi

python3 - "$ROOT" <<'PY'
import pathlib
import sys
import yaml

root = pathlib.Path(sys.argv[1])
domains = yaml.safe_load((root / "configs/domains.yaml").read_text(encoding="utf-8"))
assert domains["mode"] == "readonly", "domains mode must be readonly"
ids = domains["domains"]
assert len(set(ids.values())) == 4, "domain IDs must be distinct"
assert domains["readonly"]["writes_allowed"] == 0, "domains must forbid writes"
assert domains["isolation"]["capability_allowed_domains"] == [ids["capability"]], "capability isolation"
assert domains["isolation"]["forbid_domain_override"] is True, "domain override must be forbidden"
profiles = domains["dds"]["profiles"]
for name in ("vendor", "privileged", "capability", "simulation"):
    path = root / "configs/dds" / profiles[name]
    assert path.is_file(), f"missing DDS profile {path}"

readonly = yaml.safe_load((root / "configs/readonly.yaml").read_text(encoding="utf-8"))
assert readonly["mode"] == "readonly"
assert readonly["writes_allowed"] == 0
for topic in ("/servo_controller", "/ros_robot_controller/bus_servo/set_position",
              "/ros_robot_controller/set_motor_speeds", "/move_action", "/cmd_vel"):
    assert topic in readonly["forbidden"]["topics"], f"missing forbidden topic {topic}"
for service in ("/ros_robot_controller/bus_servo/set_state",):
    assert service in readonly["forbidden"]["services"], f"missing forbidden service {service}"
assert readonly["services"]["call"] == ["/ros_robot_controller/bus_servo/get_state"]
assert set(readonly["topics"]["publish"]) == {
    "/jcheng/telemetry/state",
    "/jcheng/telemetry/safety",
}, "only the two read-only state topics may be published"

mapping = yaml.safe_load(
    (root / "configs/robots/armpi_ultra/readonly_mapping.yaml").read_text(encoding="utf-8")
)
order = [int(value) for value in mapping["read_order"]]
assert order == sorted(order) and len(set(order)) == len(order), "read_order must increase"
assert order == [1, 2, 3, 4, 5, 6], "M4 reads servo IDs 1..6 in order"
assert str(mapping["source_sha256"]).startswith("sha256:"), "mapping source hash required"
assert mapping["radians_per_tick"] > 0
print("M4 config gate OK")
PY

m4_paths=(
  "ros2_ws/src/jcheng_hardware/jcheng_hardware/readonly.py"
  "ros2_ws/src/jcheng_telemetry/jcheng_telemetry"
)
if grep -RInE \
  --include='*.py' \
  'SetBusServoState|SetPWMServoState|set_motor_speeds|ServosPosition|bus_servo/set_position|/grasp|/move_action|/cmd_vel|pyserial|import serial|/dev/tty|ttyUSB|ttyACM|set_bus_servo_state' \
  "${m4_paths[@]}" >/dev/null 2>&1; then
  echo "ERROR: forbidden command or device path found in M4 code." >&2
  fail=1
fi

python3 - "$ROOT" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
paths = [
    root / "ros2_ws/src/jcheng_hardware/jcheng_hardware/readonly.py",
    *sorted((root / "ros2_ws/src/jcheng_telemetry/jcheng_telemetry").glob("*.py")),
]
text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
publishers = set(re.findall(r"create_publisher\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
clients = set(re.findall(r"create_client\s*\(\s*([A-Za-z_][\w.]*)", text, re.S))
assert publishers <= {"RobotState", "SafetyState"}, f"unexpected publishers: {publishers}"
assert clients <= {"GetBusServoState"}, f"unexpected service clients: {clients}"
print(f"M4 publisher/client gate OK: publishers={sorted(publishers)} clients={sorted(clients)}")
PY

if grep -nE 'create_publisher|create_client|create_subscription' \
  ros2_ws/src/jcheng_hardware/jcheng_hardware/readonly.py >/dev/null 2>&1; then
  echo "ERROR: the read-only hardware bridge must stay ROS-free." >&2
  fail=1
fi

bad_launch="$(grep -nE 'package=' ros2_ws/src/jcheng_bringup/launch/readonly.launch.py | grep -v 'jcheng_telemetry' || true)"
if [[ -n "${bad_launch}" ]]; then
  echo "ERROR: the read-only launch may start only jcheng_telemetry:" >&2
  echo "${bad_launch}" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M4 gate FAILED" >&2
  exit 1
fi
echo "M4 gate OK: read-only telemetry, no command publisher, no write service, no device"
