#!/usr/bin/env bash
# -------------------------------------------------
# restore_backup.sh — NEW: restore a stamped site backup.
#
# Env: PROJECT_BASE_DIR SITE_NAME RESTORE_DIR
# Opt: RESTORE_FILES_ONLY=true  (only private files tar)
#
# Output:
#   site_restored=true   restore_dir=...
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/backup.sh"
source "$SCRIPT_LIB_DIR/bench_lifecycle.sh"

bench::restore
