#!/usr/bin/env bash
# -------------------------------------------------
# update_bench.sh — NEW: safe rolling bench update.
#   1. backs up every site (PROJECT_BACKUP_DIR, default
#      $PROJECT_BASE_DIR/backups) — aborts update if backup fails
#   2. bench update --no-backup (own backup is authoritative)
#   3. migrates every site
#   4. supervisor reread/update + restart groups
#
# Env: PROJECT_BASE_DIR
# Opt: BENCH_UPDATE_SKIP_BACKUP=true  (skip pre-update backup)
#      BENCH_UPDATE_PRUNE_KEEP=N      (backup retention, default 5)
#
# Output:
#   bench_updated=true   backup_dir=...   bench_update_sites=N
#   bench_update_rollback_hint=...
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/backup.sh"
source "$SCRIPT_LIB_DIR/bench_lifecycle.sh"

bench::update
