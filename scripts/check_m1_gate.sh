#!/usr/bin/env bash
# Historical entry point. M3 uses check_repo_gate.sh.
set -euo pipefail
exec bash "$(dirname "$0")/check_repo_gate.sh"
