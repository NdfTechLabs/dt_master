#!/usr/bin/env bash
# -------------------------------------------------
# remove_bench.sh — NEW: full bench teardown.
#
# Policy (as agreed):
#   - services stopped (supervisor groups)
#   - sites dropped via bench / site dirs removed
#   - DATABASES ARE KEPT (recoverable; DB removal is a separate op)
#   - supervisor bench link + tenant nginx confs removed
#   - bench dir + venv deleted (.nvm and logs preserved)
#
# Env: PROJECT_BASE_DIR
# Opt: REMOVE_STOP_ONLY=true → stop services, remove nothing
#
# Output:
#   bench_removed=true   databases_kept=true   sites_dropped=N
#   nginx_confs_removed=N
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/backup.sh"
source "$SCRIPT_LIB_DIR/bench_lifecycle.sh"

bench::remove
