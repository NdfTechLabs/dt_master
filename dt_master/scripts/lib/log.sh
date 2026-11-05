#!/usr/bin/env bash
# ============================================================
# lib/log.sh — logging primitives (single definition only)
#
# Contract:
#   log, warn, error, die, section     — emit to stdout/stderr
#   init_stream_log <file>             — tee all output into <file>
#   init_site_log                      — site-scoped log file
#                                        (PROJECT_LOGS_DIR/tenant_sites/<SITE_NAME>.log)
# ============================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

warn() {
    log "WARN: $*"
}

error() {
    log "ERROR: $*"
}

die() {
    error "$@"
    exit 1
}

section() {
    log "---- $* ----"
}

# ------------------------------------------------------------
# Streamed file logging (node-level scripts).
# All stdout+stderr is tee'd into the given file (root-owned).
# ------------------------------------------------------------
init_stream_log() {
    local file="$1"

    [ -n "$file" ] || die "init_stream_log requires a log file path"

    exec > >(tee -a "$file") 2>&1
}

# ------------------------------------------------------------
# Site-scoped logging (idempotent). Used by site lifecycle.
# Requires: PROJECT_LOGS_DIR, SITE_NAME
# ------------------------------------------------------------
_DT_SITE_LOGGING_INITIALIZED="${_DT_SITE_LOGGING_INITIALIZED:-0}"

init_site_log() {
    # Idempotent: never redirect twice (would loop the tee pipe)
    [ "$_DT_SITE_LOGGING_INITIALIZED" = "1" ] && return 0

    : "${PROJECT_LOGS_DIR:?}"
    : "${SITE_NAME:?}"

    _DT_SITE_LOGGING_INITIALIZED=1
    export _DT_SITE_LOGGING_INITIALIZED

    local site_log_dir="${PROJECT_LOGS_DIR}/tenant_sites"
    mkdir -p "$site_log_dir"

    init_stream_log "${site_log_dir}/${SITE_NAME}.log"
}
