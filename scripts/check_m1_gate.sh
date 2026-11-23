#!/usr/bin/env bash
# M1 gate for jcheng-system.
# M1 must not contain runtime code, ROS packages, deployment units or a license.

set -euo pipefail

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
  docs/M1-SCOPE.md \
  docs/ARCHITECTURE.md \
  docs/SAFETY.md \
  docs/ACCEPTANCE.md \
  docs/OPERATIONS.md \
  .github/workflows/ci.yml \
  .github/CODEOWNERS
do
  require_file "$f"
done

if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

found_pkg="$(find . -path ./.git -prune -o -name 'package.xml' -print -quit | grep . || true)"
if [[ -n "${found_pkg}" ]]; then
  echo "ERROR: package.xml found; M1 must not create ROS packages." >&2
  fail=1
fi

found_idl="$(find . -path ./.git -prune -o -type f \( -name '*.msg' -o -name '*.srv' -o -name '*.action' \) -print -quit | grep . || true)"
if [[ -n "${found_idl}" ]]; then
  echo "ERROR: IDL found; contracts belong to jcheng-interfaces." >&2
  fail=1
fi

found_code="$(find . -path ./.git -prune -o -type f \
  \( -name '*.py' -o -name '*.cpp' -o -name '*.hpp' -o -name '*.h' -o -name 'Dockerfile' -o -name '*.service' -o -name '*.launch.py' \) \
  -print -quit | grep . || true)"
if [[ -n "${found_code}" ]]; then
  echo "ERROR: runtime/deployment code found in M1: ${found_code}" >&2
  fail=1
fi

for d in ros2_ws/src configs deploy e2e/scenarios locks tools; do
  extra="$(find "$d" -type f ! -name '.gitkeep' ! -name 'README.md' -print -quit 2>/dev/null || true)"
  if [[ -n "${extra}" ]]; then
    echo "ERROR: M1 directory ${d} must stay empty: ${extra}" >&2
    fail=1
  fi
done

if grep -RIlE \
  --exclude-dir=.git \
  --exclude='check_m1_gate.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . >/dev/null 2>&1; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "M1 system gate FAILED" >&2
  exit 1
fi
echo "M1 system gate OK (no runtime code, no packages, no LICENSE)"
