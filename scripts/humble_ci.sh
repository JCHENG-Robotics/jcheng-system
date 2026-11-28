#!/usr/bin/env bash
# Build and test jcheng-system in the laptop ROS 2 Humble container.
#
# CI (.github/workflows/ci.yml) builds every package under ros2_ws/src in
# ros:humble-ros-base, then runs the gate for one milestone. This script does
# the same in the local `armpi-humble-moveit` image, inside a disposable copy
# of the repository: the host checkout keeps no Humble build/install artifacts
# and no fetched interface package. CI remains the authority for published
# artifacts; this is the local parity check.
#
#   scripts/humble_ci.sh                  # m5-vendor (default)
#   scripts/humble_ci.sh --job m3         # M3 gate + scenarios + lock check
#   scripts/humble_ci.sh --job m4         # M4 read-only gate + short probe
#   scripts/humble_ci.sh --job m5-mock    # M5.1 Mock transport
#   scripts/humble_ci.sh --job m6         # replay/plan-only (no ROS build)
#   scripts/humble_ci.sh --offline        # skip the remote tag check
#
# The interface package comes from ../jcheng-interfaces by default (CI clones
# the pinned v0.3.0 tag). Override with INTERFACES=/path/to/jcheng-interfaces.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-armpi-humble-moveit:latest}"
INTERFACES="${INTERFACES:-$REPO_ROOT/../jcheng-interfaces}"
JOB="m5-vendor"
REMOTE=1

usage() {
    sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while (($# > 0)); do
    case "$1" in
        --job) JOB="${2:-}"; shift 2 ;;
        --offline) REMOTE=0; shift ;;
        -h|--help|help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$JOB" in
    m3|m4|m5-mock|m5-vendor|m6) ;;
    *) echo "unknown job: $JOB" >&2; usage >&2; exit 2 ;;
esac

[[ -d "$INTERFACES/ros2_ws/src/jcheng_interfaces" ]] || {
    echo "FAIL: interface package not found under $INTERFACES" >&2
    echo "      clone JCHENG-Robotics/jcheng-interfaces next to this repo or set INTERFACES" >&2
    exit 1
}
docker info >/dev/null 2>&1 || { echo "FAIL: cannot access the Docker daemon" >&2; exit 1; }

remote_flag=""
[[ "$REMOTE" == "1" ]] && remote_flag="--remote"

inner=$(cat <<INNER
set -e
source /opt/ros/humble/setup.bash

rm -rf /tmp/repo
cp -a /repo/. /tmp/repo/
rm -rf /tmp/repo/build /tmp/repo/install /tmp/repo/log
cp -r /interfaces/ros2_ws/src/jcheng_interfaces /tmp/repo/ros2_ws/src/
cd /tmp/repo

case "$JOB" in
    m3)
        bash scripts/check_m3_gate.sh
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        source install/setup.bash
        source /opt/jcheng/venv/bin/activate
        python3 -m pytest -q
        ros2 run jcheng_testkit jcheng_scenarios --root . --out e2e/reports/m3-mock.json
        python3 tools/verify_lock.py --root . $remote_flag --interfaces-root /interfaces --require-evidence
        ;;
    m4)
        bash scripts/check_m4_readonly.sh
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        source install/setup.bash
        source /opt/jcheng/venv/bin/activate
        python3 -m pytest -q
        ros2 run jcheng_telemetry jcheng_readonly_probe --root . --duration-s 3 --interval-s 0.2 --out e2e/reports/m4-readonly.json --evidence e2e/reports/m4-readonly.jsonl --max-gap-s 1.0
        bash scripts/check_m4_readonly.sh
        ;;
    m5-mock)
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        source install/setup.bash
        source /opt/jcheng/venv/bin/activate
        python3 -m pytest -q
        python3 -m pytest -q tests_ros
        bash scripts/check_m5_mock.sh
        ;;
    m5-vendor)
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        source install/setup.bash
        source /opt/jcheng/venv/bin/activate
        python3 -m pytest -q
        python3 -m pytest -q tests_ros
        bash scripts/check_m5_vendor.sh
        ;;
    m6)
        source /opt/jcheng/venv/bin/activate
        python3 -m pytest -q tests/test_replay.py tests/test_moveit_plan_only.py
        bash scripts/check_m6_replay.sh
        bash scripts/check_m6_moveit.sh
        ;;
esac
INNER
)

docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$REPO_ROOT":/repo:ro \
    -v "$INTERFACES":/interfaces:ro \
    -w /repo \
    "$IMAGE" bash -lc "$inner"
