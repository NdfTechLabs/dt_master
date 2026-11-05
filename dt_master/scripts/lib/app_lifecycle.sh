#!/usr/bin/env bash
# ============================================================
# lib/app_lifecycle.sh — app lifecycle domain
#
# Shared detection primitives are defined ONCE here:
#   app::detect_git_state      → GIT_COMMIT/GIT_BRANCH/GIT_TAG/GIT_REF/HEAD_STATE/DIRTY
#   app::detect_installed_version
#   app::reset_state
#
# Lifecycle entry points (used by bin/app/* wrappers):
#   app::install        app::uninstall      app::remove
#   app::check          app::check_version
#   app::upgrade        app::downgrade
#
# Output contract is identical to the legacy scripts:
# key=value lines; exit codes and env inputs unchanged.
# ============================================================

# ------------------------------------------------------------
# App git state (single definition)
# Sets: GIT_COMMIT GIT_BRANCH GIT_TAG GIT_REF HEAD_STATE DIRTY
# ------------------------------------------------------------
app::reset_state() {
    GIT_COMMIT=""
    GIT_BRANCH=""
    GIT_TAG=""
    GIT_REF=""
    GIT_HEAD_STATE="unknown"
    DIRTY="false"
}

app::detect_git_state() {
    local app_dir="$1"

    app::reset_state

    [ -d "$app_dir" ] || return 0

    local state
    state="$(git_head_state "$app_dir")"

    case "$state" in
        no-git)
            GIT_HEAD_STATE="no-git"
            ;;
        branch)
            GIT_HEAD_STATE="branch"
            GIT_COMMIT="$(git_short_head "$app_dir")"
            GIT_BRANCH="$(git_current_branch "$app_dir")"
            GIT_REF="$GIT_BRANCH"
            DIRTY="$(git_is_dirty "$app_dir")"
            ;;
        detached)
            GIT_HEAD_STATE="detached"
            GIT_COMMIT="$(git_short_head "$app_dir")"
            DIRTY="$(git_is_dirty "$app_dir")"
            GIT_TAG="$(git_tag_exact "$app_dir")"
            GIT_REF="${GIT_TAG:-detached}"
            ;;
    esac
}

# ------------------------------------------------------------
# Version-resolution chain (single definition).
# Priority: exact git tag → runtime package __version__
#           → pyproject.toml → legacy __init__.py → branch name.
# Prints the best version ("" if none found).
# ------------------------------------------------------------
_runtime_pkg_version() {
    local app_name="$1"
    local python="${BENCH_DIR:-${PROJECT_BASE_DIR:-}/bench}/env/bin/python"
    [ -x "$python" ] || python="${PROJECT_BASE_DIR:-}/.venv/bin/python"
    [ -x "$python" ] || python=python3
    run_as_frappe env DT_APP_NAME="$app_name" "$python" - <<'EOF'
import importlib, os
try:
    mod = importlib.import_module(os.environ["DT_APP_NAME"])
    print(getattr(mod, "__version__", ""))
except Exception:
    pass
EOF
}

_pyproject_project_version() {
    local py_file="$1"
    run_as_frappe env PY_PROJECT_FILE="$py_file" python3 - <<'PYEOF'
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python <= 3.10

import os

try:
    with open(os.environ["PY_PROJECT_FILE"], "rb") as f:
        data = tomllib.load(f)
    print(data.get("project", {}).get("version", ""))
except Exception:
    pass
PYEOF
}

_legacy_init_version() {
    local init_file="$1"
    run_as_frappe env PY_FILE="$init_file" python3 - <<'PYEOF'
import re, os

try:
    with open(os.environ["PY_FILE"]) as f:
        for line in f:
            if "__version__" in line:
                m = re.search(r"['\"]([^'\"]+)['\"]", line)
                if m:
                    print(m.group(1))
except Exception:
    pass
PYEOF
}

app::detect_installed_version() {
    local app_name="$1"
    local app_dir="$2"
    local version="" v=""

    # 1. Exact git tag (strongest)
    if [ -n "${GIT_TAG:-}" ]; then
        version="$GIT_TAG"
    fi

    # 2. Runtime Python package version
    if [ -z "$version" ]; then
        v="$(_runtime_pkg_version "$app_name")" || true
        [ -n "$v" ] && version="$v"
    fi

    # 3. Static pyproject.toml [project].version
    if [ -z "$version" ] && [ -f "$app_dir/pyproject.toml" ]; then
        v="$(_pyproject_project_version "$app_dir/pyproject.toml")" || true
        [ -n "$v" ] && version="$v"
    fi

    # 4. Static __init__.py __version__ (legacy apps)
    if [ -z "$version" ] && [ -f "$app_dir/$app_name/__init__.py" ]; then
        v="$(_legacy_init_version "$app_dir/$app_name/__init__.py")" || true
        [ -n "$v" ] && version="$v"
    fi

    # 5. Branch fallback (last resort)
    if [ -z "$version" ] && [ "${GIT_HEAD_STATE:-}" = "branch" ]; then
        version="$GIT_REF"
    fi

    echo "$version"
}

# ------------------------------------------------------------
# SSH deploy key bootstrap (idempotent) — used by install only
# ------------------------------------------------------------
app::_ensure_ssh_key() {
    : "${FRAPPE_HOME:?FRAPPE_HOME not set}"
    local ssh_key="${FRAPPE_HOME}/.ssh/id_ed25519"

    mkdir -p "${FRAPPE_HOME}/.ssh"
    chown -R frappe:frappe "${FRAPPE_HOME}/.ssh"
    chmod 700 "${FRAPPE_HOME}/.ssh"

    if [[ ! -f "$ssh_key" ]]; then
        log "action=generate_ssh_deploy_key"
        run_as_frappe ssh-keygen \
            -t ed25519 \
            -f "$ssh_key" \
            -N "" \
            -C "dtsaas-deploy"
    fi

    run_as_frappe touch "${FRAPPE_HOME}/.ssh/known_hosts"
    run_as_frappe chmod 600 "${FRAPPE_HOME}/.ssh/known_hosts"

    # IMPORTANT: no shell redirection outside run_as_frappe
    run_as_frappe ssh-keyscan github.com 2>/dev/null | \
    run_as_frappe tee -a "${FRAPPE_HOME}/.ssh/known_hosts" >/dev/null
}

# ------------------------------------------------------------
# Switch github.com HTTPS URL to SSH (returns via REPOSITORY_URL)
# ------------------------------------------------------------
app::_switch_repo_to_ssh() {
    REPOSITORY_URL="$1"

    app::_ensure_ssh_key
    local ssh_repo
    ssh_repo="$(echo "$REPOSITORY_URL" | sed -E 's#https://github.com/#git@github.com:#')"

    if ! run_as_frappe git ls-remote "$ssh_repo" >/dev/null 2>&1; then
        log "error=ssh_deploy_key_not_authorized"
        log "action=add_public_key_to_github_deploy_keys"
        : "${FRAPPE_HOME:?}"
        local pub_key
        pub_key="$(run_as_frappe cat "${FRAPPE_HOME}/.ssh/id_ed25519.pub")"
        log "public_key=${pub_key}"
        return 1
    fi

    REPOSITORY_URL="$ssh_repo"
    log "repo_switched_to_ssh=${REPOSITORY_URL}"
    return 0
}

# ============================================================
# app::install
# Env: BENCH_DIR APP_NAME SITE_NAME INSTALL_MODE REF_TYPE
#      REPOSITORY_URL FRAPPE_HOME + SOURCE_REF | TARGET_VERSION
# ============================================================
app::install() {
    require_env BENCH_DIR APP_NAME SITE_NAME
    require_env INSTALL_MODE REF_TYPE REPOSITORY_URL
    : "${FRAPPE_HOME:?FRAPPE_HOME not set}"

    local app="$APP_NAME"
    local apps_dir="${BENCH_DIR}/apps"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    # ------------------------------------------------------------
    # Guards (contract enforcement)
    # ------------------------------------------------------------
    case "$INSTALL_MODE" in
        rolling|fixed) ;;
        *)
            log "error=invalid_install_mode"
            log "expected=rolling|fixed"
            log "got=${INSTALL_MODE}"
            exit 1
            ;;
    esac

    case "$INSTALL_MODE:$REF_TYPE" in
        rolling:branch|fixed:tag) ;;
        *)
            log "error=install_mode_ref_mismatch"
            log "install_mode=$INSTALL_MODE"
            log "ref_type=$REF_TYPE"
            exit 1
            ;;
    esac

    # ------------------------------------------------------------
    # GitHub HTTPS → SSH with deploy key
    # ------------------------------------------------------------
    if [[ "$REPOSITORY_URL" == https://github.com/* ]]; then
        app::_switch_repo_to_ssh "$REPOSITORY_URL" || exit 1
    fi

    # ------------------------------------------------------------
    # Fetch app using BENCH ONLY
    # ------------------------------------------------------------
    if [[ ! -d "$app_dir" ]]; then
        log "Fetching app via bench: $app"

        if [[ "$REF_TYPE" == "branch" ]]; then
            require_env SOURCE_REF
            bench_exec get-app \
                "$app" \
                "$REPOSITORY_URL" \
                --branch "$SOURCE_REF"
        else
            require_env TARGET_VERSION
            bench_exec get-app \
                "$app" \
                "$REPOSITORY_URL" \
                --branch "$TARGET_VERSION"
        fi
    fi

    # ------------------------------------------------------------
    # Install app on site (bench-authoritative)
    # ------------------------------------------------------------
    log "Installing app on site via bench: $app"

    if ! bench_exec --site "$SITE_NAME" install-app "$app"; then
        log "error=bench_install_failed"
        log "app=$app"
        bench_exec --site "$SITE_NAME" uninstall-app "$app" --yes || true
        exit 1
    fi

    # ------------------------------------------------------------
    # Bench lifecycle
    # ------------------------------------------------------------
    export NODE_OPTIONS="--max-old-space-size=4096"
    export FRAPPE_NODE_OPTIONS="$NODE_OPTIONS"

    bench_exec build
    clear_cache_and_reload

    # ------------------------------------------------------------
    # Detect final state
    # ------------------------------------------------------------
    app::detect_git_state "$app_dir"

    local installed_version
    installed_version="$(app::detect_installed_version "$app" "$app_dir")"

    # ------------------------------------------------------------
    # Output (machine contract — legacy-identical)
    # ------------------------------------------------------------
    emit "app_name" "$app"
    emit "installed" "true"
    emit "installed_version" "$installed_version"
    emit "git_commit" "$GIT_COMMIT"
    emit "git_branch" "$GIT_BRANCH"
    emit "git_ref" "$GIT_REF"
    emit "git_tag" "$GIT_TAG"
    emit "git_head_state" "$GIT_HEAD_STATE"
    emit "dirty" "$DIRTY"
    emit "install_mode" "$INSTALL_MODE"
    emit "ref_type" "$REF_TYPE"
    emit "source_ref" "${SOURCE_REF:-}"
    emit "target_version" "${TARGET_VERSION:-}"
}

# ============================================================
# app::uninstall
# Env: BENCH_DIR APP_NAME SITE_NAME
# ============================================================
app::uninstall() {
    require_env BENCH_DIR APP_NAME SITE_NAME

    local app="$APP_NAME"
    local site="$SITE_NAME"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    # ------------------------------------------------------------
    # Guards (non-fatal)
    # ------------------------------------------------------------
    if [[ ! -d "$app_dir" ]]; then
        log "warning=app_not_present_in_bench"
        log "app=${app}"

        log "available_apps_in_bench:"
        ls -1 "${BENCH_DIR}/apps" 2>/dev/null | while read -r a; do
            log " - ${a}"
        done

        emit "app_name" "$app"
        emit "site" "$site"
        emit "uninstalled" "false"
        exit 0
    fi

    # Check if app is installed on site
    local installed_apps
    installed_apps=$(list_installed_apps "$site") || return
    if ! grep -Fxq "$app" <<< "$installed_apps"; then
        log "warning=app_not_installed_on_site"
        log "app=${app}"
        log "site=${site}"

        log "installed_apps_on_site:"
        list_installed_apps "$site" | while read -r a; do
            log " - ${a}"
        done

        emit "app_name" "$app"
        emit "site" "$site"
        emit "uninstalled" "false"
        emit "installed" "false"
        exit 0
    fi

    # ------------------------------------------------------------
    # Uninstall app (non-interactive)
    # ------------------------------------------------------------
    bench_exec --site "$site" uninstall-app "$app" --yes

    # ------------------------------------------------------------
    # Post-uninstall hygiene
    # ------------------------------------------------------------
    clear_cache_and_reload

    # ------------------------------------------------------------
    # Output (machine contract)
    # ------------------------------------------------------------
    emit "app_name" "$app"
    emit "site" "$site"
    emit "uninstalled" "true"
    emit "installed" "false"
}

# ============================================================
# gc::remove_missing_app — remove dangling references for an app
# that is no longer present in the bench filesystem.
# Prints "true"/"false" (same contract as legacy gc_missing_app).
# ============================================================
gc::remove_missing_app() {
    local app="$1"
    local bench="$2"

    local found="false"

    local site
    for site in "$bench"/sites/*; do
        site="$(basename "$site")"
        [[ -f "$bench/sites/$site/apps.txt" ]] || continue

        if grep -qx "$app" "$bench/sites/$site/apps.txt"; then
            sed -i "/^${app}$/d" "$bench/sites/$site/apps.txt"
            log "gc_removed_from_site=$site app=$app"
            found="true"
        fi
    done

    local cfg="$bench/sites/common_site_config.json"
    if [[ -f "$cfg" ]] && grep -q "\"$app\"" "$cfg"; then
        GC_APP="$app" GC_CFG="$cfg" python3 - <<'PYEOF'
import json, os

app = os.environ["GC_APP"]
cfg = os.environ["GC_CFG"]

with open(cfg) as f:
    d = json.load(f)
apps = d.get("installed_apps")
if isinstance(apps, list) and app in apps:
    apps.remove(app)
    d["installed_apps"] = apps
    with open(cfg, "w") as f:
        json.dump(d, f, indent=2)
PYEOF
        log "gc_removed_from_common_site_config app=$app"
        found="true"
    fi

    echo "$found"
}

# ============================================================
# app::remove — remove app code + references from the bench
# Env: BENCH_DIR APP_NAME (+ optional SKIP_BUILD)
# ============================================================
app::remove() {
    require_env BENCH_DIR APP_NAME
    local app="$APP_NAME"
    [[ "$app" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Invalid app name"
    [ "$app" != frappe ] || die "Cannot remove Frappe from its own bench"
    local app_dir="${BENCH_DIR}/apps/${app}"
    cd "$BENCH_DIR"
    local site installed
    while read -r site; do
        [ -z "$site" ] && continue
        if [ -d "$app_dir" ]; then
            installed=$(list_installed_apps "$site") || die "Cannot inspect installed apps on $site; refusing removal"
            if grep -Fxq "$app" <<< "$installed"; then
                log "Uninstalling $app and its data from $site before removing code"
                bench_exec --site "$site" uninstall-app "$app" --yes --force || return
            fi
        else
            # With missing code, keep cleanup DB-authoritative, never just edit
            # apps.txt and claim that database references have disappeared.
            log "Removing stale installed-app reference for $app on $site"
            bench_exec --site "$site" remove-from-installed-apps "$app" || return
        fi
    done < <(list_sites)
    log "Removing app code: $app_dir"
    rm -rf -- "$app_dir"
    if [ -f "$BENCH_DIR/sites/apps.txt" ]; then
        sed -i "/^${app}$/d" "$BENCH_DIR/sites/apps.txt"
    fi

    # ------------------------------------------------------------
    # Cleanup Node artifacts (best-effort)
    # ------------------------------------------------------------
    rm -rf "node_modules/.cache" || true
    rm -rf "apps/.vite" || true

    # ------------------------------------------------------------
    # Optional: rebuild assets (safe default)
    # ------------------------------------------------------------
    if [[ "${SKIP_BUILD:-false}" != "true" ]]; then
        export NODE_OPTIONS="--max-old-space-size=4096"
        bench_exec build
    fi

    # ------------------------------------------------------------
    # Reload bench
    # ------------------------------------------------------------
    while read -r site; do
        [ -z "$site" ] && continue
        SITE_NAME="$site" clear_cache_and_reload
    done < <(list_sites)

    # ------------------------------------------------------------
    # Output (machine contract)
    # ------------------------------------------------------------
    emit "app_name" "$app"
    emit "removed" "true"
}

# ============================================================
# app::check — detect installation + git state + version
# Env: BENCH_DIR APP_NAME SITE_NAME
# ============================================================
app::check() {
    require_env BENCH_DIR APP_NAME SITE_NAME

    local app="$APP_NAME"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    local installed="false" installed_version=""

    if [[ -d "$app_dir" ]]; then
        local site_apps
        site_apps=$(list_installed_apps "$SITE_NAME") || return
        if grep -Fxq "$app" <<< "$site_apps"; then installed="true"; fi
        app::detect_git_state "$app_dir"
        installed_version="$(app::detect_installed_version "$app" "$app_dir")"
    else
        app::reset_state
    fi

    # Output (machine contract)
    emit "app_name" "$app"
    emit "installed" "$installed"
    emit "installed_version" "$installed_version"
    emit "git_commit" "$GIT_COMMIT"
    emit "git_branch" "$GIT_BRANCH"
    emit "git_ref" "$GIT_REF"
    emit "git_tag" "$GIT_TAG"
    emit "head_state" "$GIT_HEAD_STATE"
    emit "git_head_state" "$GIT_HEAD_STATE"
    emit "dirty" "$DIRTY"
}

# ============================================================
# app::check_version — like check + update/upgrade analysis
# Env: BENCH_DIR APP_NAME SITE_NAME (+ INSTALL_MODE etc.)
# ============================================================
app::check_version() {
    require_env BENCH_DIR APP_NAME

    local app="$APP_NAME"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    # Marketplace metadata (exported by executor)
    INSTALL_MODE="${INSTALL_MODE:-}"
    REPOSITORY_URL="${REPOSITORY_URL:-}"
    REF_TYPE="${REF_TYPE:-}"
    SOURCE_REF="${SOURCE_REF:-}"
    TARGET_VERSION="${TARGET_VERSION:-}"

    local installed="false" installed_version=""

    if [[ -d "$app_dir" ]]; then
        installed="true"
        if [ -n "${SITE_NAME:-}" ]; then
            local site_apps
            site_apps=$(list_installed_apps "$SITE_NAME") || return
            grep -Fxq "$app" <<< "$site_apps" || installed=false
        fi
        app::detect_git_state "$app_dir"
        installed_version="$(app::detect_installed_version "$app" "$app_dir")"
    else
        app::reset_state
    fi

    # ------------------------------------------------------------
    # Marketplace-aware update detection
    # ------------------------------------------------------------
    local update_available="unknown"

    if [[ "$INSTALL_MODE" == "rolling" && "$REF_TYPE" == "branch" ]]; then
        if [[ -n "$REPOSITORY_URL" && -n "$SOURCE_REF" && -n "$GIT_COMMIT" ]]; then
            local remote_commit
            remote_commit="$(
                run_as_frappe git ls-remote "$REPOSITORY_URL" "refs/heads/${SOURCE_REF}" 2>/dev/null \
                | awk '{print substr($1,1,7)}'
            )"

            if [[ -n "$remote_commit" ]]; then
                if [[ "$remote_commit" != "$GIT_COMMIT" ]]; then
                    update_available="true"
                else
                    update_available="false"
                fi
            fi
        fi
    fi

    # ------------------------------------------------------------
    # Branch-based major upgrade detection
    # ------------------------------------------------------------
    local upgrade_available="false" upgrade_target="" upgrade_latest=""

    if [[ "$INSTALL_MODE" == "rolling" && "$REF_TYPE" == "branch" && "$SOURCE_REF" =~ ^version-([0-9]+)$ ]]; then
        local current_major="${BASH_REMATCH[1]}"

        local remote_majors
        remote_majors="$(
            run_as_frappe git ls-remote "$REPOSITORY_URL" "refs/heads/version-*" \
            | awk -F/ '{print $NF}' \
            | sed 's/version-//' \
            | grep -E '^[0-9]+$' \
            | sort -n
        )"

        local v
        for v in $remote_majors; do
            if (( v > current_major )); then
                upgrade_available="true"
                upgrade_target="version-${v}"
                break
            fi
        done

        upgrade_latest="version-$(echo "$remote_majors" | tail -n1)"
    fi

    # ------------------------------------------------------------
    # Semantic version discovery (FULL versions from tags)
    # ------------------------------------------------------------
    local next_major_version="" latest_major_version=""
    local next_minor_version="" latest_minor_version=""
    local next_patch_version="" latest_patch_version=""

    local semver_tags
    semver_tags="$(
        run_as_frappe git ls-remote --tags "$REPOSITORY_URL" 2>/dev/null \
        | awk -F/ '{print $NF}' \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | sed 's/^v//' \
        | sort -V
    )"

    if [[ "${installed_version#v}" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        local cur_major="${BASH_REMATCH[1]}"
        local cur_minor="${BASH_REMATCH[2]}"
        local cur_patch="${BASH_REMATCH[3]}"

        # LATEST MAJOR (highest X.Y.Z overall)
        latest_major_version="$(echo "$semver_tags" | tail -n1)"

        # NEXT MAJOR (first tag with X > current major)
        next_major_version="$(
            echo "$semver_tags" | awk -F. -v m="$cur_major" '$1 > m' | head -n1
        )"

        # LATEST MINOR (same major, highest Y.Z)
        latest_minor_version="$(
            echo "$semver_tags" | awk -F. -v m="$cur_major" '$1 == m' | tail -n1
        )"

        # NEXT MINOR (same major, Y > current minor)
        next_minor_version="$(
            echo "$semver_tags" | awk -F. -v m="$cur_major" -v n="$cur_minor" '$1 == m && $2 > n' | head -n1
        )"

        # LATEST PATCH (same major+minor)
        latest_patch_version="$(
            echo "$semver_tags" | awk -F. -v m="$cur_major" -v n="$cur_minor" '$1 == m && $2 == n' | tail -n1
        )"

        # NEXT PATCH (same major+minor, Z > current patch)
        next_patch_version="$(
            echo "$semver_tags" \
            | awk -F. -v m="$cur_major" -v n="$cur_minor" -v p="$cur_patch" \
                '$1 == m && $2 == n && $3 > p' \
            | head -n1
        )"
    fi

    # ------------------------------------------------------------
    # Fallbacks → resolve to installed version if empty
    # ------------------------------------------------------------
    next_major_version="${next_major_version:-$installed_version}"
    latest_major_version="${latest_major_version:-$installed_version}"
    next_minor_version="${next_minor_version:-$installed_version}"
    latest_minor_version="${latest_minor_version:-$installed_version}"
    next_patch_version="${next_patch_version:-$installed_version}"
    latest_patch_version="${latest_patch_version:-$installed_version}"

    # Output (machine contract)
    emit "app_name" "$app"
    emit "installed" "$installed"
    emit "installed_version" "$installed_version"
    emit "git_commit" "$GIT_COMMIT"
    emit "git_branch" "$GIT_BRANCH"
    emit "git_ref" "$GIT_REF"
    emit "git_tag" "$GIT_TAG"
    emit "git_head_state" "$GIT_HEAD_STATE"
    emit "dirty" "$DIRTY"
    emit "install_mode" "$INSTALL_MODE"
    emit "ref_type" "$REF_TYPE"
    emit "source_ref" "$SOURCE_REF"
    emit "update_available" "$update_available"
    emit "upgrade_available" "$upgrade_available"
    emit "upgrade_target" "$upgrade_target"
    emit "upgrade_latest" "$upgrade_latest"

    emit "next_major_version" "$next_major_version"
    emit "latest_major_version" "$latest_major_version"
    emit "next_minor_version" "$next_minor_version"
    emit "latest_minor_version" "$latest_minor_version"
    emit "next_patch_version" "$next_patch_version"
    emit "latest_patch_version" "$latest_patch_version"
}

# ============================================================
# app::upgrade — rolling branch upgrade
# Env: BENCH_DIR APP_NAME SITE_NAME INSTALL_MODE REF_TYPE SOURCE_REF
#      (+ optional REPOSITORY_URL)
# ============================================================
app::upgrade() {
    require_env BENCH_DIR APP_NAME SITE_NAME
    require_env INSTALL_MODE REF_TYPE
    if [ "$INSTALL_MODE:$REF_TYPE" = fixed:tag ]; then
        app::downgrade
        return
    fi
    require_env SOURCE_REF

    local app="$APP_NAME"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    # ------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------
    if [[ "$INSTALL_MODE" != "rolling" ]]; then
        log "error=upgrade_not_supported_for_install_mode"
        exit 1
    fi

    if [[ "$REF_TYPE" != "branch" ]]; then
        log "error=upgrade_only_supported_for_branch_install"
        exit 1
    fi

    if [[ ! -d "$app_dir" ]]; then
        log "error=app_not_installed"
        exit 1
    fi

    cd "$app_dir"

    local current_branch
    current_branch="$(git_current_branch "$app_dir")"

    # ------------------------------------------------------------
    # Rule A: upgrades must run from a branch
    # ------------------------------------------------------------
    if [[ -z "$current_branch" ]]; then
        log "state=detached_head"
        log "action=reattach_branch"
        log "target_branch=$SOURCE_REF"

        if run_as_frappe git -C "$app_dir" show-ref --verify --quiet "refs/heads/${SOURCE_REF}"; then
            run_as_frappe git -C "$app_dir" checkout "${SOURCE_REF}"
        elif run_as_frappe git -C "$app_dir" show-ref --verify --quiet "refs/remotes/origin/${SOURCE_REF}"; then
            run_as_frappe git -C "$app_dir" checkout -B "${SOURCE_REF}" "origin/${SOURCE_REF}"
        else
            log "error=expected_branch_not_found"
            exit 1
        fi

        current_branch="$SOURCE_REF"
    fi

    if [[ "$current_branch" != "$SOURCE_REF" ]]; then
        log "error=branch_mismatch"
        log "current_branch=$current_branch"
        log "expected_branch=$SOURCE_REF"
        exit 1
    fi

    # ------------------------------------------------------------
    # Resolve git remote (single remote)
    # ------------------------------------------------------------
    local remote_name
    remote_name="$(git_remote_name "$app_dir")" || {
        log "error=unable_to_resolve_git_remote"
        exit 1
    }

    # ------------------------------------------------------------
    # 🔐 Ensure correct remote URL (ENFORCEMENT)
    # ------------------------------------------------------------
    if [[ -n "${REPOSITORY_URL:-}" ]]; then
        if [[ "$(git_remote_url "$app_dir" "$remote_name")" != "$REPOSITORY_URL" ]]; then
            log "action=fix_remote_url"
            run_as_frappe git -C "$app_dir" remote set-url "$remote_name" "$REPOSITORY_URL"
        fi
    fi

    # ------------------------------------------------------------
    # 🔑 SSH failure handling
    # ------------------------------------------------------------
    GIT_ERROR_FILE="$(mktemp /tmp/dt_git_fetch_error.XXXXXX)"

    if ! run_as_frappe git -C "$app_dir" fetch "$remote_name" "$SOURCE_REF" 2>"$GIT_ERROR_FILE"; then

        if grep -q "Permission denied (publickey)" "$GIT_ERROR_FILE"; then
            log "error=ssh_auth_failed"
            log "action=deploy_key_required"

            local key_path="/home/frappe/.ssh/id_rsa"

            if [ ! -f "$key_path" ]; then
                log "action=generating_deploy_key"
                run_as_frappe mkdir -p /home/frappe/.ssh
                run_as_frappe ssh-keygen -t rsa -b 4096 -N "" -f "$key_path"
            fi

            local pub_key
            pub_key="$(run_as_frappe cat "${key_path}.pub")"

            emit "deploy_key_required" "true"
            emit "deploy_key" "$pub_key"
            rm -f "$GIT_ERROR_FILE"
            exit 1
        fi

        log "error=git_fetch_failed"
        rm -f "$GIT_ERROR_FILE"
        exit 1
    fi
    rm -f "$GIT_ERROR_FILE"

    # ------------------------------------------------------------
    # Fast-forward
    # ------------------------------------------------------------
    [ "$(git_is_dirty "$app_dir")" = "false" ] || die "Working tree is dirty; refusing to discard local changes"
    run_as_frappe git -C "$app_dir" merge --ff-only FETCH_HEAD

    # ------------------------------------------------------------
    # Bench lifecycle
    # ------------------------------------------------------------
    export NODE_OPTIONS="--max-old-space-size=4096"
    export FRAPPE_NODE_OPTIONS="$NODE_OPTIONS"

    bench_exec setup requirements
    bench_exec --site "$SITE_NAME" migrate
    bench_exec build
    clear_cache_and_reload

    # ------------------------------------------------------------
    # Detect final state
    # ------------------------------------------------------------
    app::detect_git_state "$app_dir"
    local installed_version
    installed_version="$(app::detect_installed_version "$app" "$app_dir")"

    # Output (machine contract)
    emit "app_name" "$app"
    emit "installed" "true"
    emit "installed_version" "$installed_version"
    emit "git_commit" "$GIT_COMMIT"
    emit "git_branch" "$GIT_BRANCH"
    emit "git_ref" "$GIT_BRANCH"
    emit "git_tag" ""
    emit "git_head_state" "branch"
    emit "dirty" "$DIRTY"
    emit "install_mode" "$INSTALL_MODE"
    emit "ref_type" "branch"
    emit "source_ref" "$SOURCE_REF"
}

# ============================================================
# app::downgrade — fixed tag downgrade
# Env: BENCH_DIR APP_NAME SITE_NAME INSTALL_MODE REF_TYPE TARGET_VERSION
#      (+ optional REPOSITORY_URL)
# ============================================================
app::downgrade() {
    require_env BENCH_DIR APP_NAME SITE_NAME
    require_env INSTALL_MODE REF_TYPE TARGET_VERSION

    local app="$APP_NAME"
    local app_dir="${BENCH_DIR}/apps/${app}"

    cd "$BENCH_DIR"

    # ------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------
    if [[ "$INSTALL_MODE" != "rolling" && "$INSTALL_MODE" != "fixed" ]]; then
        log "error=downgrade_not_supported_for_install_mode"
        exit 1
    fi

    if [[ "$REF_TYPE" != "tag" ]]; then
        log "error=downgrade_only_supported_for_tag_ref"
        exit 1
    fi

    if [[ ! -d "$app_dir" ]]; then
        log "error=app_not_installed"
        exit 1
    fi

    if [[ "$(git_is_dirty "$app_dir")" == "true" ]]; then
        log "error=working_tree_dirty"
        exit 1
    fi

    # ------------------------------------------------------------
    # Resolve git remote safely (URL match preferred, else single)
    # ------------------------------------------------------------
    local remote_name=""

    if run_as_frappe git -C "$app_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        if [[ -n "${REPOSITORY_URL:-}" ]]; then
            local name url
            while read -r name url _; do
                if [[ "$url" == "$REPOSITORY_URL" ]]; then
                    remote_name="$name"
                    break
                fi
            done < <(run_as_frappe git -C "$app_dir" remote -v | awk '{print $1, $2}')
        fi

        if [ -z "$remote_name" ]; then
            remote_name="$(git_remote_name "$app_dir")" || remote_name=""
        fi
    fi

    if [ -z "$remote_name" ]; then
        log "error=unable_to_resolve_git_remote"
        exit 1
    fi

    # ------------------------------------------------------------
    # Fetch & checkout target tag (downgrade)
    # ------------------------------------------------------------
    log "Fetching tags from remote=${remote_name}"
    run_as_frappe git -C "$app_dir" fetch --tags "$remote_name"

    if ! run_as_frappe git -C "$app_dir" rev-parse "refs/tags/${TARGET_VERSION}" >/dev/null 2>&1; then
        log "error=target_version_not_found"
        log "target_version=${TARGET_VERSION}"
        exit 1
    fi

    log "Checking out tag=${TARGET_VERSION}"
    run_as_frappe git -C "$app_dir" checkout -f "refs/tags/${TARGET_VERSION}"

    # ------------------------------------------------------------
    # Bench lifecycle (venv-safe, memory-safe, build-safe)
    # ------------------------------------------------------------
    export NODE_OPTIONS="--max-old-space-size=4096"
    export FRAPPE_NODE_OPTIONS="$NODE_OPTIONS"

    bench_exec setup requirements
    bench_exec --site "$SITE_NAME" migrate
    bench_exec build
    clear_cache_and_reload

    # ------------------------------------------------------------
    # Detect final state (authoritative)
    # ------------------------------------------------------------
    app::detect_git_state "$app_dir"
    local installed_version
    installed_version="$(app::detect_installed_version "$app" "$app_dir")"

    # Output (machine contract)
    emit "app_name" "$app"
    emit "installed" "true"
    emit "installed_version" "$installed_version"
    emit "git_commit" "$GIT_COMMIT"
    emit "git_branch" ""
    emit "git_ref" "$TARGET_VERSION"
    emit "git_tag" "$TARGET_VERSION"
    emit "git_head_state" "detached"
    emit "dirty" "$DIRTY"
    emit "install_mode" "$INSTALL_MODE"
    emit "ref_type" "tag"
    emit "target_version" "$TARGET_VERSION"
}
