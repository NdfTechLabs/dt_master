#!/usr/bin/env bash
# ============================================================
# lib/state.sh — process locking + machine output contract
#
#   emit KEY VALUE           — one "key=value" contract line
#   with_lock LOCKFILE MODE  — flock exclusive lock
#                              MODE=soft → exit 0 when already running
#                              MODE=fail → exit 1 when already running
# ============================================================

emit() {
    echo "$1=$2"
}

with_lock() {
    local lock_file="$1"
    local mode="${2:-fail}"

    exec 9>"$lock_file" || die "Cannot open lock file: $lock_file"

    if ! flock -n 9; then
        case "$mode" in
            soft)
                log "Already running (lock held): $lock_file"
                exit 0
                ;;
            *)
                die "Lock is held by another process: $lock_file"
                ;;
        esac
    fi

    # fd 9 stays locked for the lifetime of the shell
}
