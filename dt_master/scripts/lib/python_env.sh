#!/usr/bin/env bash
# ============================================================
# lib/python_env.sh — virtualenv execution layer
#
#   require_venv        — ensure $PROJECT_BASE_DIR/.venv exists
#   run_in_venv CMD...  — run CMD as frappe, inside the bench venv
# ============================================================

require_venv() {
    : "${PROJECT_BASE_DIR:?}"

    VENV_DIR="$PROJECT_BASE_DIR/.venv"
    VENV_ACTIVATE="$VENV_DIR/bin/activate"

    if [ ! -f "$VENV_ACTIVATE" ]; then
        log "ERROR: Python virtualenv not found at $VENV_DIR"
        exit 1
    fi
}

run_in_venv() {
    require_venv

    # -------------------------------------------------
    # Guard rails: forbid nested shells & injection
    # -------------------------------------------------
    if [ "$#" -eq 0 ]; then
        log "ERROR: run_in_venv requires a command"
        exit 1
    fi

    case "$1" in
        bash|sh|sudo)
            log "ERROR: run_in_venv does not allow '$1'"
            log "Use direct command invocation only"
            exit 1
            ;;
    esac

    # Arguments are passed positionally through "$@", never evaluated as shell
    # code. Passwords and paths may legitimately contain shell punctuation.

    # -------------------------------------------------
    # Execute command inside venv as frappe
    # -------------------------------------------------
    run_as_frappe bash -c '
        set -e

        VENV_ACTIVATE="$PROJECT_BASE_DIR/.venv/bin/activate"

        if [ -z "$VIRTUAL_ENV" ]; then
            # shellcheck source=/dev/null
            source "$VENV_ACTIVATE"
            _DT_VENV_TEMP_ACTIVATED=1
        fi

        "$@"

        if [ "${_DT_VENV_TEMP_ACTIVATED:-}" = "1" ]; then
            # shellcheck disable=SC2115
            deactivate
        fi
    ' -- "$@"
}
