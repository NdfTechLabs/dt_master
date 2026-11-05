#!/usr/bin/env bash
# ============================================================
# lib/deps.sh — dependency resolution engine
#
# Resolves a SYSTEM_DEPENDENCIES JSON contract into installed
# packages, idempotently, in declared order. Never double-installs.
#
# Env contract:
#   SYSTEM_DEPENDENCIES   JSON array:
#     [ { "name": "...", "install_method": "apt|pip|npm|nvm|script",
#         "group": "...", "version": "x.y.z", "repository": "..." } ]
#   PROJECT_BASE_DIR      project root (hosts .nvm)
#   PROJECT_LOGS_DIR      for logging redirection by caller
#   NODE_VERSION          required for npm/nvm methods
#
# Output contract:
#   deps_resolved=true
#   deps_total=N
#   deps_skipped=N
#   deps_<name>=<method:action>      for each resolved dependency
# ============================================================

deps::_node_version_required() {
    require_var NODE_VERSION "NODE_VERSION is required for npm/nvm dependencies"
}

# ------------------------------------------------------------
# Method resolvers. $1=name, $2=pkg JSON line (jq -c row)
# Globals available: NAME VERSION REPO GROUP METHOD
# Every resolver must be idempotent.
# ------------------------------------------------------------

deps::_resolve_apt() {
    local name="$1"
    local repo="${2:-}"
    local version="${3:-}" package="$1"
    [ -z "$version" ] || package="$name=$version"

    if ensure_dpkg "$name"; then
        if [ -z "$version" ] || [ "$(dpkg-query -W -f='${Version}' "$name" 2>/dev/null)" = "$version" ]; then
            DEPS_ACTION=already_installed
            return 0
        fi
    fi

    if [ -n "$repo" ]; then
        log ">> Adding repository: $repo"
        add-apt-repository -y "$repo" || return
        apt-get update -y || return
    fi

    log ">> Installing via apt: $name"
    apt_install "$package"
}

deps::_resolve_pip() {
    local name="$1"
    local version="${2:-}" package="$1"
    [ -z "$version" ] || package="$name==$version"
    log ">> Installing via pip: $name"
    pip install "$package"
}

deps::_resolve_npm() {
    local name="$1"
    local version="${2:-}" package="$1"
    [ -z "$version" ] || package="$name@$version"
    deps::_node_version_required

    if run_as_frappe npm ls -g "$package" >/dev/null 2>&1; then
        DEPS_ACTION=already_installed
        return 0
    fi

    log ">> Installing via npm: $name"
    run_as_frappe npm install -g "$package"
}

deps::_resolve_nvm() {
    local version="$1"
    deps::_node_version_required

    local nvm_root="$PROJECT_BASE_DIR/.nvm"

    if [ -d "$nvm_root/versions/node/v$version" ]; then
        DEPS_ACTION=already_installed
        return 0
    fi

    log ">> Installing Node via nvm: $version"

    SKIP_NVM=1 run_as_frappe bash -c '
        set -e
        if [ -s "$PROJECT_BASE_DIR/.nvm/nvm.sh" ]; then
            export NVM_DIR="$PROJECT_BASE_DIR/.nvm"
        else
            export NVM_DIR="$HOME/.nvm"
        fi
        [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
        command -v nvm >/dev/null || exit 1
        nvm install "$1"
        nvm use "$1"
    ' dt-nvm "$version"
}

deps::_resolve_script() {
    local name="$1"
    local repo="$2"

    if [ -z "$repo" ]; then
        die "dependency '${name}' uses 'script' method but has no repository"
    fi

    log ">> Running script for: $name"

    SKIP_NVM=1 run_as_frappe bash -c '
        set -euo pipefail
        curl -fsSL "$1" | bash
    ' dt-dependency "$repo"
}

# ------------------------------------------------------------
# Main entry: resolve the full dependency list.
# Loop runs in the PARENT shell (process substitution) so
# `exit`/`return` behave correctly and state is real state.
# Requires: jq present (bootstrap by caller if not).
# ------------------------------------------------------------
deps::resolve_all() {
    : "${SYSTEM_DEPENDENCIES:?SYSTEM_DEPENDENCIES not set}"
    : "${PROJECT_BASE_DIR:?PROJECT_BASE_DIR not set}"
    : "${PROJECT_LOGS_DIR:?PROJECT_LOGS_DIR not set}"

    # Bootstrap jq if missing (needed before anything else)
    if ! command -v jq >/dev/null 2>&1; then
        log ">> Installing jq (bootstrap)"
        apt-get update -y
        apt_install jq
    fi

    log "== Installing system dependencies (dynamic) =="
    printf '%s' "$SYSTEM_DEPENDENCIES" | jq -e '
        type == "array" and all(.[];
            (.name | type == "string" and length > 0 and (startswith("-") | not) and (test("[\\n\\r]") | not)) and
            (.install_method | IN("apt", "pip", "npm", "nvm", "script")) and
            (if .install_method == "nvm" then (.version | type == "string" and length > 0) else true end)
        )
    ' >/dev/null || die "Invalid SYSTEM_DEPENDENCIES contract"
    apt-get update -y

    local total=0 skipped=0 DEPS_ACTION

    while read -r dep; do
        [ -z "$dep" ] && continue

        local name method group version repo action
        name=$(echo "$dep" | jq -r '.name')
        method=$(echo "$dep" | jq -r '.install_method // empty')
        group=$(echo "$dep" | jq -r '.group // empty')
        version=$(echo "$dep" | jq -r '.version // empty')
        repo=$(echo "$dep" | jq -r '.repository // empty')
        DEPS_ACTION=installed

        [ -z "$name" ] && continue
        total=$((total + 1))

        echo "-------------------------------------------------"
        echo ">> Processing: $name  (method=$method group=$group version=${version:-n/a})"

        case "$method" in

            apt)
                deps::_resolve_apt "$name" "$repo" "$version" >&2 || die "apt dependency failed: $name"
                ;;

            pip)
                deps::_resolve_pip "$name" "$version" >&2 || die "pip dependency failed: $name"
                ;;

            npm)
                deps::_resolve_npm "$name" "$version" >&2 || die "npm dependency failed: $name"
                ;;

            nvm)
                deps::_resolve_nvm "$version" >&2 || die "nvm dependency failed: $name"
                ;;

            script)
                deps::_resolve_script "$name" "$repo" >&2 || die "script dependency failed: $name"
                ;;

            *)
                die "Unknown install method: $method (dependency: $name)"
                ;;
        esac

        case "$DEPS_ACTION" in
            already_installed)
                skipped=$((skipped + 1))
                emit "deps_${name}" "${method}:already_installed"
                ;;
            *)
                emit "deps_${name}" "${method}:installed"
                ;;
        esac

    done < <(echo "$SYSTEM_DEPENDENCIES" | jq -c '.[]?')

    echo "-------------------------------------------------"
    log ">> Ensuring core services are running"
    systemctl enable redis-server mariadb || true
    systemctl start redis-server mariadb || true

    echo "== Dependency resolution complete =="
    emit "deps_resolved" "true"
    emit "deps_total" "$total"
    emit "deps_skipped" "$skipped"
}
