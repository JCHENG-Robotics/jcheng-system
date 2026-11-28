#!/usr/bin/env bash
# Build and test jcheng-system with the host ROS 2 Jazzy toolchain.
#
# Mirrors the Jazzy CI jobs (m3-mock, m4-readonly, m5-mock, m6-replay). The
# robot-bound m5-vendor job stays on Humble; use scripts/humble_ci.sh for it.
#
#   scripts/jazzy_ci.sh                  # m3 (default)
#   scripts/jazzy_ci.sh --job m3         # M3 gate + scenarios + lock check
#   scripts/jazzy_ci.sh --job m4         # M4 read-only gate + short probe
#   scripts/jazzy_ci.sh --job m5-mock    # M5.1 Mock transport
#   scripts/jazzy_ci.sh --job m6         # replay/plan-only (no ROS build)
#   scripts/jazzy_ci.sh --offline        # skip the remote tag check
#
# The interface package comes from ../jcheng-interfaces by default (CI clones
# the pinned v0.2.0 tag). Override with INTERFACES=/path/to/jcheng-interfaces.
# The build runs in a disposable copy, so this checkout keeps no artifacts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERFACES="${INTERFACES:-$REPO_ROOT/../jcheng-interfaces}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
JOB="m3"
REMOTE=1

usage() {
    sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
    m3|m4|m5-mock|m6) ;;
    *) echo "unknown job: $JOB (use m3, m4, m5-mock or m6)" >&2; usage >&2; exit 2 ;;
esac

[[ -d "$INTERFACES/ros2_ws/src/jcheng_interfaces" ]] || {
    echo "FAIL: interface package not found under $INTERFACES" >&2
    echo "      clone JCHENG-Robotics/jcheng-interfaces next to this repo or set INTERFACES" >&2
    exit 1
}
[[ -f "$ROS_SETUP" ]] || { echo "FAIL: missing ROS setup: $ROS_SETUP" >&2; exit 1; }
command -v colcon >/dev/null 2>&1 || { echo "FAIL: colcon is not on PATH" >&2; exit 1; }
python3 -c 'import pytest, yaml' 2>/dev/null || {
    echo "FAIL: host Python lacks pytest/PyYAML" >&2
    exit 1
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/jcheng-system-jazzy.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
tar -C "$REPO_ROOT" -cf - \
    --exclude=.git --exclude=.worktrees --exclude=build --exclude=install --exclude=log \
    . | tar -C "$WORK" -xf -
cp -r "$INTERFACES/ros2_ws/src/jcheng_interfaces" "$WORK/ros2_ws/src/"
cd "$WORK"

# ROS setup scripts reference optional variables (COLCON_TRACE, etc.), so keep
# `-u` off for the rest of the script.
set +u
# shellcheck disable=SC1090
source "$ROS_SETUP"

remote_flag=""
[[ "$REMOTE" == "1" ]] && remote_flag="--remote"

case "$JOB" in
    m3)
        bash scripts/check_m3_gate.sh
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        # shellcheck disable=SC1091
        source install/setup.bash
        python3 -m pytest -q
        ros2 run jcheng_testkit jcheng_scenarios --root . --out e2e/reports/m3-mock.json
        python3 tools/verify_lock.py --root . $remote_flag \
            --interfaces-root "$INTERFACES" --require-evidence
        ;;
    m4)
        bash scripts/check_m4_readonly.sh
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        # shellcheck disable=SC1091
        source install/setup.bash
        python3 -m pytest -q
        ros2 run jcheng_telemetry jcheng_readonly_probe \
            --root . --duration-s 3 --interval-s 0.2 \
            --out e2e/reports/m4-readonly.json \
            --evidence e2e/reports/m4-readonly.jsonl --max-gap-s 1.0
        bash scripts/check_m4_readonly.sh
        ;;
    m5-mock)
        colcon build --base-paths ros2_ws/src --event-handlers console_cohesion+
        # shellcheck disable=SC1091
        source install/setup.bash
        python3 -m pytest -q
        python3 -m pytest -q tests_ros
        bash scripts/check_m5_mock.sh
        ;;
    m6)
        python3 -m pytest -q tests/test_replay.py tests/test_moveit_plan_only.py
        bash scripts/check_m6_replay.sh
        bash scripts/check_m6_moveit.sh
        ;;
esac
