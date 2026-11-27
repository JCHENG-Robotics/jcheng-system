#!/usr/bin/env bash
# Repository structure gate shared by all phases.

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
  configs/api.yaml \
  configs/domains.yaml \
  configs/readonly.yaml \
  configs/robots/armpi_ultra/profile.yaml \
  configs/robots/armpi_ultra/safety_overlay.yaml \
  configs/robots/armpi_ultra/readonly_mapping.yaml \
  locks/ecosystem.lock.yaml \
  docs/ARCHITECTURE.md \
  docs/SAFETY.md \
  docs/ACCEPTANCE.md \
  docs/OPERATIONS.md \
  docs/M3-SCOPE.md \
  docs/M4-SCOPE.md \
  docs/M5-SCOPE.md \
  docs/M6-SCOPE.md \
  tools/verify_lock.py \
  tools/replay_g8.py \
  scripts/check_m3_gate.sh \
  scripts/check_m4_readonly.sh \
  scripts/check_m5_mock.sh \
  scripts/check_m5_vendor.sh \
  scripts/check_m6_replay.sh \
  scripts/check_m6_moveit.sh \
  .github/workflows/ci.yml \
  .github/CODEOWNERS
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
  jcheng_testkit \
  jcheng_telemetry \
  jcheng_runtime \
  jcheng_vendor \
  jcheng_moveit
do
  require_file "ros2_ws/src/${pkg}/package.xml"
done

if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

if grep -RIlE \
  --exclude-dir=.git \
  --exclude-dir=build \
  --exclude-dir=install \
  --exclude-dir=log \
  --exclude='check_repo_gate.sh' \
  --exclude='check_m3_gate.sh' \
  --exclude='check_m4_readonly.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "Repository gate FAILED" >&2
  exit 1
fi
echo "Repository gate OK: structure, 13 packages, no LICENSE, no secret material"
