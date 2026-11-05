#!/usr/bin/env bash
# ============================================================
# lib/bench.sh — bench invocation + site knowledge
#
# Env contract: PROJECT_BASE_DIR (+ SITE_NAME for site-scoped fns)
#   bench_exec BENCH-ARGS...  — run bench inside venv as frappe
#   clear_cache_and_reload    — cache clear + supervisor reload
#   site_exists SITE          — boolean (site dir + config present)
#   list_sites                — site names in bench (excluding assets/config)
#   list_installed_apps SITE  — raw bench list-apps output
# ============================================================

BENCH_DIR="${BENCH_DIR:-"${PROJECT_BASE_DIR:-}/bench"}"

bench_exec() {
    if [ "$#" -eq 0 ]; then
        log "ERROR: bench_exec requires bench arguments"
        exit 1
    fi

    if [ "$1" = "bench" ]; then
        log "ERROR: Do not pass 'bench' to bench_exec"
        exit 1
    fi

    : "${PROJECT_BASE_DIR:?}"
    BENCH_DIR="$PROJECT_BASE_DIR/bench"

    if [ ! -d "$BENCH_DIR" ]; then
        log "ERROR: Bench directory not found at $BENCH_DIR"
        exit 1
    fi

    (
        cd "$BENCH_DIR" || exit 1
        run_in_venv bench "$@"
    )
}

clear_cache_and_reload() {
    : "${SITE_NAME:?}"

    bench_exec --site "$SITE_NAME" clear-cache
    bench_exec --site "$SITE_NAME" clear-website-cache
    reload_services
}

reload_services() {
    # Supervisor reload is ROOT responsibility
    if [ -S /var/run/supervisor.sock ] && supervisorctl pid >/dev/null 2>&1; then
        supervisorctl reread
        supervisorctl update
    else
        warn "Supervisor not available for reload"
    fi
}

site_exists() {
    local site="${1:-${SITE_NAME:-}}"

    if [ -z "$site" ]; then
        log "ERROR: site_exists requires SITE_NAME"
        return 2
    fi

    : "${PROJECT_BASE_DIR:?}"
    local bench_dir="$PROJECT_BASE_DIR/bench"

    # Site directory must exist
    [ -d "$bench_dir/sites/$site" ] || return 1

    # Basic sanity check (site config must exist)
    [ -f "$bench_dir/sites/$site/site_config.json" ] || return 1

    return 0
}

# Site names in this bench (skips assets + common_site_config.json)
list_sites() {
    : "${PROJECT_BASE_DIR:?}"
    local bench_dir="$PROJECT_BASE_DIR/bench"

    [ -d "$bench_dir/sites" ] || return 0
    local directory
    for directory in "$bench_dir/sites"/*; do
        [ -d "$directory" ] && [ ! -L "$directory" ] && [ -f "$directory/site_config.json" ] || continue
        printf '%s\n' "${directory##*/}"
    done
}

# One site per line, installed-app names only (trimmed first column)
list_installed_apps() {
    local site="$1"
    bench_exec --site "$site" list-apps | awk '{print $1}'
}
