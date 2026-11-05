#!/usr/bin/env bash
# ============================================================
# lib/runtime.sh — privilege layer (single definition)
#
#   require_root          — root or passwordless sudo
#   ensure_frappe_user    — create 'frappe' service user
#   ensure_frappe_sudo    — passwordless sudoers entry (idempotent)
#   run_as_frappe CMD...  — run CMD as frappe user, optionally via nvm
#
# nvm resolution order inside run_as_frappe:
#   1. $PROJECT_BASE_DIR/.nvm   (project-local nvm)
#   2. $HOME/.nvm               (/home/frappe)
# Environment preserved across sudo: PROJECT_BASE_DIR, PROJECT_LOGS_DIR,
# NODE_VERSION, SKIP_NVM.
# ============================================================

FRAPPE_USER="frappe"
FRAPPE_SHELL="/bin/bash"
SUDOERS_FILE="/etc/sudoers.d/frappe"

require_root() {
    if [ "$(id -u)" -eq 0 ]; then
        return 0
    fi

    if sudo -n true 2>/dev/null; then
        return 0
    fi

    log "ERROR: This script requires root privileges or passwordless sudo"
    exit 1
}

ensure_frappe_user() {
    if ! id "$FRAPPE_USER" &>/dev/null; then
        log "INFO: Creating user '$FRAPPE_USER'"
        sudo useradd -m -s "$FRAPPE_SHELL" "$FRAPPE_USER"
    fi
}

ensure_frappe_sudo() {
    if [ ! -f "$SUDOERS_FILE" ]; then
        log "INFO: Granting passwordless sudo to '$FRAPPE_USER'"
        echo "$FRAPPE_USER ALL=(ALL) NOPASSWD:ALL" | sudo tee "$SUDOERS_FILE" >/dev/null
        sudo chmod 440 "$SUDOERS_FILE"
    fi
}

run_as_frappe() {
    require_root
    ensure_frappe_user
    ensure_frappe_sudo

    if [ "$#" -eq 0 ]; then
        log "ERROR: run_as_frappe requires a command"
        exit 1
    fi

    sudo -u "$FRAPPE_USER" \
      --preserve-env=PROJECT_BASE_DIR,PROJECT_LOGS_DIR,NODE_VERSION,SKIP_NVM,NODE_OPTIONS,FRAPPE_NODE_OPTIONS \
      env HOME="/home/$FRAPPE_USER" \
      bash -lc '
        set -e

        # -------------------------------------------------
        # Conditionally load NVM (project-local first, then HOME)
        # -------------------------------------------------
        if [ "${SKIP_NVM:-0}" != "1" ]; then

          if [ -n "${PROJECT_BASE_DIR:-}" ] && [ -s "$PROJECT_BASE_DIR/.nvm/nvm.sh" ]; then
            export NVM_DIR="$PROJECT_BASE_DIR/.nvm"
          else
            export NVM_DIR="$HOME/.nvm"
          fi

          if [ -s "$NVM_DIR/nvm.sh" ]; then
            # shellcheck source=/dev/null
            source "$NVM_DIR/nvm.sh"

            if [ -n "${NODE_VERSION:-}" ]; then
              nvm use "$NODE_VERSION" >/dev/null || {
                echo "ERROR: Failed to activate Node $NODE_VERSION via nvm" >&2
                exit 1
              }
            fi
          fi

          # Validate Node exists (NO stdout pollution)
          if ! command -v node >/dev/null 2>&1; then
            echo "ERROR: Node not available in frappe environment" >&2
            exit 1
          fi

        fi

        # -------------------------------------------------
        # Execute command (stdout = pure result)
        # -------------------------------------------------
        "$@"
      ' dt-run "$@"
}

# ------------------------------------------------------------
# Load nvm into the CURRENT (root) shell for direct npm/node use.
# Preference: PROJECT_BASE_DIR/.nvm → $HOME/.nvm
# ------------------------------------------------------------
nvm_load_root() {
    : "${PROJECT_BASE_DIR:?PROJECT_BASE_DIR is required}"

    local nvm_dir
    if [ -s "$PROJECT_BASE_DIR/.nvm/nvm.sh" ]; then
        nvm_dir="$PROJECT_BASE_DIR/.nvm"
    elif [ -s "/home/$FRAPPE_USER/.nvm/nvm.sh" ]; then
        nvm_dir="/home/$FRAPPE_USER/.nvm"
    elif [ -s "$HOME/.nvm/nvm.sh" ]; then
        nvm_dir="$HOME/.nvm"
    else
        log "ERROR: nvm not found (tried $PROJECT_BASE_DIR/.nvm and $HOME/.nvm)"
        exit 1
    fi

    export NVM_DIR="$nvm_dir"
    # shellcheck source=/dev/null
    source "$nvm_dir/nvm.sh"

    if ! command -v nvm >/dev/null 2>&1; then
        log "ERROR: failed to load nvm from $nvm_dir"
        exit 1
    fi
}
