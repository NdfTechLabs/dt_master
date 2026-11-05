#!/usr/bin/env bash
# ============================================================
# lib/site_lifecycle.sh — tenant site domain
#
# Entry points (used by bin/site/* wrappers):
#   site::create            site::install_apps
#   site::suspend           site::resume
#   site::destroy           site::verify
#   site::configure_proxy
# ============================================================

# ============================================================
# site::create
# Env: SITE_NAME DB_NAME ADMIN_PASSWORD MYSQL_ROOT_USER
#      MYSQL_ROOT_PASSWORD PROJECT_BASE_DIR PROJECT_LOGS_DIR
#      FRAPPE_UPSTREAM_PORT
# ============================================================
site::create() {
    require_env SITE_NAME \
        DB_NAME \
        ADMIN_PASSWORD \
        MYSQL_ROOT_USER \
        MYSQL_ROOT_PASSWORD \
        PROJECT_BASE_DIR \
        PROJECT_LOGS_DIR \
        FRAPPE_UPSTREAM_PORT

    local bench_dir="${PROJECT_BASE_DIR}/bench"
    local supervisor_link="/etc/supervisor/conf.d/bench.conf"
    local target_supervisor_conf="$bench_dir/config/supervisor.conf"

    log "Starting site setup: $SITE_NAME"
    log "Using Frappe upstream port: $FRAPPE_UPSTREAM_PORT"

    # -------------------------------------------------
    # Ensure MariaDB admin user exists (safety step)
    # -------------------------------------------------
    db_ensure_admin_user

    # -------------------------------------------------
    # Idempotency check (repair partial installs)
    # -------------------------------------------------
    _site_repair_partial() {
        local bench_dir="$1" site_name="$2" db_name="$3"

        log "Repairing partial site installation"

        rm -rf "$bench_dir/sites/$site_name"

        db_drop_database_and_user "$db_name"

        log "Partial site removed — clean installation will proceed"
    }

    _site_check_partial() {
        local bench_dir="$1" site_name="$2"

        local site_path="$bench_dir/sites/$site_name"
        local site_config="$site_path/site_config.json"

        if [ ! -d "$site_path" ]; then
            return 1
        fi

        if [ ! -f "$site_config" ]; then
            log "Site config missing — partial installation detected"
            return 0
        fi

        local db_user db_pass db_name_local
        db_user=$(json_get "$(cat "$site_config")" '.db_user // .db_name')
        db_pass=$(json_get "$(cat "$site_config")" '.db_password')
        db_name_local=$(json_get "$(cat "$site_config")" '.db_name')

        if [ -z "$db_user" ] || [ -z "$db_name_local" ]; then
            log "DB credentials missing in site_config.json"
            return 0
        fi

        if ! env MYSQL_PWD="$db_pass" mysql -u"$db_user" -e "USE \`$db_name_local\`;" >/dev/null 2>&1; then
            log "Database authentication failed — partial site detected"
            return 0
        fi

        return 1
    }

    local create_site=1

    if [ -d "$bench_dir/sites/$SITE_NAME" ]; then

        if _site_check_partial "$bench_dir" "$SITE_NAME"; then
            log "Partial site installation detected"
            _site_repair_partial "$bench_dir" "$SITE_NAME" "$DB_NAME"
            create_site=1
        else
            log "Site already exists and is healthy"
            create_site=0
        fi

    fi

    # -------------------------------------------------
    # Create site (only if missing)
    # -------------------------------------------------
    if [ "$create_site" -eq 1 ]; then
        log "Creating new site: $SITE_NAME"

        bench_exec new-site "$SITE_NAME" \
            --db-name "$DB_NAME" \
            --admin-password "$ADMIN_PASSWORD" \
            --mariadb-root-username "$MYSQL_ROOT_USER" \
            --mariadb-root-password "$MYSQL_ROOT_PASSWORD"
    else
        log "Using existing site: $SITE_NAME"
    fi

    # -------------------------------------------------
    # Post-creation / post-existence site setup
    # -------------------------------------------------
    log "Running post-creation site setup"
    bench_exec --site "$SITE_NAME" migrate
    bench_exec --site "$SITE_NAME" enable-scheduler

    # -------------------------------------------------
    # 🌐 Set public site URL (CRITICAL)
    # -------------------------------------------------
    log "Setting public site_url for Frappe"
    bench_exec set-config -g site_url "https://$SITE_NAME"

    # -------------------------------------------------
    # 🔐 Lock Gunicorn port at Bench level (CRITICAL)
    # -------------------------------------------------
    log "Configuring Bench webserver port: $FRAPPE_UPSTREAM_PORT"
    log "Temporarily setting webserver_port for supervisor generation"
    bench_exec set-config -g webserver_port "$FRAPPE_UPSTREAM_PORT"

    # -------------------------------------------------
    # Ensure Supervisor is wired to THIS bench
    # -------------------------------------------------
    log "Ensuring Supervisor is bound to current bench path"

    if [ -L "$supervisor_link" ]; then
        local current_target
        current_target="$(readlink -f "$supervisor_link")"
        if [ "$current_target" != "$target_supervisor_conf" ]; then
            log "Updating Supervisor bench.conf symlink"
            rm -f "$supervisor_link"
        fi
    elif [ -e "$supervisor_link" ]; then
        log "WARNING: bench.conf exists but is not a symlink — replacing"
        rm -f "$supervisor_link"
    fi

    if [ ! -e "$supervisor_link" ]; then
        log "Linking Supervisor to bench config"
        ln -s "$target_supervisor_conf" "$supervisor_link"
    fi

    # -------------------------------------------------
    # Regenerate Supervisor config (non-interactive)
    # -------------------------------------------------
    log "Reconfiguring Supervisor for site-level services"

    log "Stopping Supervisor-managed Bench services"
    supervisorctl stop bench-web:* bench-workers:* || true

    CI=1 bench_exec setup supervisor --yes

    log "Removing webserver_port to prevent public URL leakage"
    bench_exec set-config -g webserver_port ''

    log "Reloading Supervisor configuration"
    supervisorctl reread
    supervisorctl update

    log "Starting Supervisor-managed Bench services"
    supervisorctl start bench-redis:* bench-web:* bench-workers:* || true

    log "Verifying public URL does not include internal port"
    if bench_exec --site "$SITE_NAME" execute frappe.utils.get_url \
      | grep -q ":$FRAPPE_UPSTREAM_PORT"; then
        log "ERROR: Public URL still leaking internal port"
        exit 1
    fi

    log "Verifying Gunicorn is bound to expected port"
    if ! ss -ltnp | grep -q "127.0.0.1:$FRAPPE_UPSTREAM_PORT"; then
        log "ERROR: Gunicorn not bound to expected port $FRAPPE_UPSTREAM_PORT"
        exit 1
    fi

    # -------------------------------------------------
    # Cache clear + reload
    # -------------------------------------------------
    clear_cache_and_reload

    log "Site setup completed successfully"
}

# ============================================================
# site::install_apps
# Env: SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR
#      + APPS (legacy csv) | APPS_JSON (structured)
# ============================================================
site::install_apps() {
    require_env SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR

    local bench_dir="${PROJECT_BASE_DIR}/bench"
    local apps_dir="${bench_dir}/apps"
    local site_dir="${bench_dir}/sites/${SITE_NAME}"

    log "Installing apps on site: $SITE_NAME"

    # -------------------------------------------------
    # Input handling (structured apps support)
    # -------------------------------------------------
    local apps_json="${APPS_JSON:-}"
    local app_list=()

    if [ -n "$apps_json" ]; then
        log "Using structured apps input (APPS_JSON)"
        printf '%s' "$apps_json" | jq -e 'type == "array" and length > 0 and all(.[]; .app_name | type == "string" and test("^[A-Za-z_][A-Za-z0-9_]*$"))' >/dev/null \
            || die "APPS_JSON must be a non-empty array of app descriptors"
        mapfile -t app_list < <(echo "$apps_json" | jq -c '.[]')
    else
        require_env APPS
        log "Using legacy apps input (APPS): $APPS"

        local raw_list
        IFS=',' read -ra raw_list <<< "$APPS"

        local raw
        for raw in "${raw_list[@]}"; do
            raw="$(echo "$raw" | xargs)"
            [ -z "$raw" ] && continue

            app_list+=("{\"app_name\":\"$raw\",\"repo_url\":null,\"ref\":null,\"ref_type\":\"branch\",\"install_strategy\":\"git_branch\"}")
        done
    fi

    # -------------------------------------------------
    # Disable Bench automatic Supervisor reload
    # -------------------------------------------------
    log "Disabling automatic Supervisor reload in Bench"
    bench_exec set-config -g restart_supervisor_on_update false

    # -------------------------------------------------
    # Node.js memory tuning
    # -------------------------------------------------
    export NODE_OPTIONS="--max-old-space-size=1536"
    log "NODE_OPTIONS set to $NODE_OPTIONS"

    # -------------------------------------------------
    # Detect installed Frappe version (major)
    # -------------------------------------------------
    local frappe_version_raw
    frappe_version_raw="$(bench_exec version | grep -i '^frappe' || true)"

    [ -z "$frappe_version_raw" ] && {
        log "ERROR: Unable to determine installed Frappe version"
        exit 1
    }

    local frappe_version_major
    frappe_version_major="$(echo "$frappe_version_raw" | awk '{print $2}' | cut -d. -f1)"

    [[ ! "$frappe_version_major" =~ ^[0-9]+$ ]] && {
        log "ERROR: Invalid Frappe version detected: $frappe_version_raw"
        exit 1
    }

    local app_branch="version-${frappe_version_major}"

    log "Detected Frappe version: $frappe_version_major"
    log "Default branch: $app_branch"

    # -------------------------------------------------
    # Installed apps snapshot
    # -------------------------------------------------
    local installed_apps
    installed_apps="$(list_installed_apps "$SITE_NAME")" || return

    # -------------------------------------------------
    # Per-app processing
    # -------------------------------------------------
    local app_json app_name repo_url ref ref_type strategy
    local site_installed fs_present

    for app_json in "${app_list[@]}"; do
        app_name=$(json_get "$app_json" '.app_name')
        repo_url=$(json_get "$app_json" '.repo_url')
        ref=$(json_get "$app_json" '.ref')
        ref_type=$(json_get "$app_json" '.ref_type')
        strategy=$(json_get "$app_json" '.install_strategy')

        [ -z "$app_name" ] && continue

        log "---- Processing app: $app_name ----"

        site_installed=false
        fs_present=false

        echo "$installed_apps" | grep -qx "$app_name" && site_installed=true
        [ -d "$apps_dir/$app_name" ] && fs_present=true

        # -------------------------------------------------
        # Skip healthy
        # -------------------------------------------------
        if $site_installed && $fs_present; then
            log "OK: Already installed: $app_name"
            continue
        fi

        log "WARN: Inconsistent state detected"
        log "  site_installed : $site_installed"
        log "  fs_present     : $fs_present"

        # Cleanup
        if $site_installed; then
            log "Uninstalling from site"
            bench_exec --site "$SITE_NAME" uninstall-app "$app_name" --yes || true
        fi

        # -------------------------------------------------
        # Fetch logic
        # -------------------------------------------------
        if $fs_present; then
            log "Reusing bench app code shared by other sites: $app_name"
        elif [ -n "$repo_url" ] && [ "$repo_url" != "null" ]; then
            log "Custom app detected: $repo_url"

            case "$strategy" in
                git_branch|git_tag)
                    if [ -n "$ref" ] && [ "$ref" != "null" ]; then
                        bench_exec get-app "$repo_url" --branch "$ref"
                    else
                        bench_exec get-app "$repo_url"
                    fi
                    ;;
                git_commit)
                    bench_exec get-app "$repo_url"
                    run_as_frappe git -C "$apps_dir/$app_name" checkout "$ref"
                    ;;
                *)
                    log "ERROR: Unknown install strategy: $strategy"
                    exit 1
                    ;;
            esac

        else
            log "Frappe ecosystem app: $app_name"

            if bench_exec get-app "$app_name" --branch "$app_branch"; then
                log "Fetched using $app_branch"
            else
                log "Fallback to main"
                bench_exec get-app "$app_name" --branch main
            fi
        fi

        # -------------------------------------------------
        # Install
        # -------------------------------------------------
        log "Installing app on site: $app_name"
        bench_exec --site "$SITE_NAME" install-app "$app_name"

        log "OK: Installed: $app_name"
    done

    # -------------------------------------------------
    # Verification
    # -------------------------------------------------
    log "Verifying site boot"

    bench_exec --site "$SITE_NAME" console <<EOF
import frappe
frappe.init(site="$SITE_NAME")
frappe.connect()
print("SITE_BOOT_OK")
exit()
EOF

    # -------------------------------------------------
    # Cache + reload
    # -------------------------------------------------
    clear_cache_and_reload

    log "App installation completed successfully"
}

# ============================================================
# site::suspend / site::resume
# Env: SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
site::suspend() {
    init_site_log
    require_env SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR

    log "Suspending site: $SITE_NAME"

    bench_exec --site "$SITE_NAME" disable-scheduler
    bench_exec --site "$SITE_NAME" set-maintenance-mode on

    clear_cache_and_reload

    log "Site suspended successfully"
}

site::resume() {
    init_site_log
    require_env SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR

    log "Resuming site: $SITE_NAME"

    bench_exec --site "$SITE_NAME" enable-scheduler
    bench_exec --site "$SITE_NAME" set-maintenance-mode off

    clear_cache_and_reload

    log "Site resumed successfully"
}

# ============================================================
# site::destroy — bench drop-site + defensive leftover cleanup
# Env: SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
site::destroy() {
    init_site_log
    require_env SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    [[ "$SITE_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "Invalid site name"

    local bench_dir="${PROJECT_BASE_DIR}/bench"
    local site_dir="$bench_dir/sites/${SITE_NAME}"
    local bench_log_dir="$bench_dir/logs"
    local database="${DB_NAME:-}" db_user="${DB_NAME:-}"
    local dropped=0
    if [ -f "$site_dir/site_config.json" ]; then
        database=$(jq -r '.db_name // empty' "$site_dir/site_config.json")
        db_user=$(jq -r '.db_user // .db_name // empty' "$site_dir/site_config.json")
    fi

    log "Destroying site: $SITE_NAME"

    # -------------------------------------------------
    # Ensure frappe owns required paths (CRITICAL)
    # -------------------------------------------------
    log "Ensuring frappe ownership for bench paths"

    local path
    for path in "$bench_dir" "$bench_log_dir"; do
        if [ -e "$path" ]; then
            chown frappe:frappe "$path"
        fi
    done
    [ ! -d "$site_dir" ] || chown -R frappe:frappe "$site_dir"

    # -------------------------------------------------
    # Drop site via Bench (authoritative)
    # -------------------------------------------------
    if [ -d "$site_dir" ]; then
        log "Dropping site via bench"
        if bench_exec drop-site "$SITE_NAME" --force --db-root-username "$MYSQL_ROOT_USER" --db-root-password "$MYSQL_ROOT_PASSWORD"; then
            dropped=1
        else
            warn "Bench teardown failed; performing explicit DB and filesystem cleanup"
        fi
    else
        log "Site directory not found, skipping bench drop"
    fi

    # -------------------------------------------------
    # Defensive filesystem cleanup
    # -------------------------------------------------
    if [ "$dropped" -eq 0 ] && [ -n "$database" ]; then
        db_drop_database_and_user "$database" "$db_user"
    fi
    if [ -d "$site_dir" ]; then
        log "Removing residual site directory"
        rm -rf "$site_dir"
    fi

    # -------------------------------------------------
    # Reload services (safe)
    # -------------------------------------------------
    reload_services

    log "Site destroyed permanently"
}

# ============================================================
# site::verify — deep health check (diagnostics, never blocks)
# Env: SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
site::verify() {
    require_env SITE_NAME PROJECT_BASE_DIR PROJECT_LOGS_DIR

    # -------------------------------------------------
    # Verifier crash guard (script bugs ≠ site bugs)
    # -------------------------------------------------
    trap 'log "FATAL: verifier crashed at line $LINENO"; exit 2' ERR

    local bench_dir="$PROJECT_BASE_DIR/bench"
    local sites_dir="$bench_dir/sites"
    local site_dir="$sites_dir/$SITE_NAME"
    local site_log_dir="$site_dir/logs"

    log "Verifying site: $SITE_NAME"

    # -------------------------------------------------
    # HEALTH (authoritative)
    # -------------------------------------------------
    local system_healthy=1

    # -------------------------------------------------
    # DIAGNOSTICS (informational)
    # -------------------------------------------------
    local log_errors=0 app_errors=0 import_errors=0 http_errors=0 nginx_errors=0

    # -------------------------------------------------
    # Preconditions (hard failures)
    # -------------------------------------------------
    command -v jq >/dev/null || {
        log "FATAL: jq is required but not installed"
        exit 2
    }

    [ -d "$site_dir" ] || { log "FATAL: Site directory not found"; exit 2; }
    [ -d "$site_log_dir" ] || { log "FATAL: Site log directory missing"; exit 2; }

    local site_config="$site_dir/site_config.json"
    local common_config="$sites_dir/common_site_config.json"

    # -------------------------------------------------
    # Site identity
    # -------------------------------------------------
    section "Site Identity"
    jq -r '
      "Site Name        : '"$SITE_NAME"'",
      "Database         : \(.db_name // "unknown")",
      "Administrator    : \(.administrator // "unknown")",
      "Developer Mode   : \(.developer_mode // false)",
      "Maintenance Mode : \(.maintenance_mode // false)"
    ' "$site_config"

    # -------------------------------------------------
    # Installed apps (informational)
    # -------------------------------------------------
    section "Installed Applications"
    bench_exec --site "$SITE_NAME" list-apps || warn "list-apps failed"

    # -------------------------------------------------
    # Redis endpoints (runtime health)
    # -------------------------------------------------
    section "Redis Endpoints"
    local ep port
    while read -r ep; do
        [ -z "$ep" ] && continue
        port="$(echo "$ep" | sed -E 's|.*:([0-9]+)$|\1|')"
        if port_listening "$port"; then
            log "Redis OK        : $ep"
        else
            log "ERROR: Redis NOT RUNNING: $ep"
            system_healthy=0
        fi
    done < <(jq -r '.redis_cache?,.redis_queue?,.redis_socketio?' "$common_config" | grep -v null)

    # -------------------------------------------------
    # Pre-flight remediation (SAFE SELF-HEAL)
    # -------------------------------------------------
    log ">> Running pre-flight remediation (cache/build/migrate)"

    local remediation_failed=0

    if ! bench_exec --site "$SITE_NAME" clear-cache; then
        warn "clear-cache failed"
        remediation_failed=1
    fi

    if ! bench_exec --site "$SITE_NAME" migrate; then
        warn "migrate failed"
        remediation_failed=1
    fi

    if ! bench_exec build; then
        warn "build failed"
        remediation_failed=1
    fi

    if [ "$remediation_failed" -eq 1 ]; then
        log "INFO: Remediation encountered issues (continuing verification)"
    else
        log "OK: Remediation completed"
    fi

    # -------------------------------------------------
    # Supervisor checks (PRIMARY HEALTH SIGNAL)
    # -------------------------------------------------
    log ">> Verifying Supervisor-managed services"
    local group line
    for group in bench-redis bench-web bench-workers; do
        while read -r line; do
            case "$line" in
                *RUNNING*)
                    log "OK: $line"
                    ;;
                *FATAL*|*EXITED*|*STOPPED*)
                    log "ERROR: $line"
                    system_healthy=0
                    ;;
            esac
        done < <(supervisorctl status "${group}:*" 2>/dev/null)
    done

    # -------------------------------------------------
    # Log inspection helper (DIAGNOSTIC ONLY)
    # -------------------------------------------------
    _site_check_log_file() {
        local file="$1"
        [ -f "$file" ] || return 0

        local content
        content="$(tail -n 400 "$file" | grep -vE 'background_jobs|/assets/|directory index of')"

        if echo "$content" | grep -qiE \
            'ModuleNotFoundError|ImportError|Traceback \(most recent call last\)|Internal Server Error'; then
            log "INFO: Diagnostic issues found in $(basename "$file")"
            echo "$content" | tail -n 20
            return 1
        fi
        return 0
    }

    # -------------------------------------------------
    # Site logs (diagnostics only)
    # -------------------------------------------------
    log ">> Inspecting site logs (diagnostic)"
    local log_file
    for log_file in frappe.log database.log scheduler.log ipython.log; do
        _site_check_log_file "$site_log_dir/$log_file" || log_errors=1
    done

    # -------------------------------------------------
    # Bench logs (diagnostics only)
    # -------------------------------------------------
    log ">> Inspecting bench logs (diagnostic)"
    local bench_log_dir="$bench_dir/logs"
    for log_file in web.error.log worker.error.log; do
        local file="$bench_log_dir/$log_file"
        [ -f "$file" ] || continue

        if tail -n 300 "$file" | grep -F "$SITE_NAME" | grep -qiE \
            'Traceback|ModuleNotFoundError|ImportError'; then
            log "INFO: Diagnostic bench issue in $log_file"
            log_errors=1
        fi
    done

    # -------------------------------------------------
    # App loadability (diagnostic only)
    # -------------------------------------------------
    log ">> Diagnosing app loadability"
    local app
    while read -r app; do
        [ -z "$app" ] && continue
        if [ ! -d "$bench_dir/apps/$app" ]; then
            log "INFO: App directory missing (diagnostic): $app"
            app_errors=1
        else
            log "OK: App folder present: $app"
        fi
    done < <(bench_exec --site "$SITE_NAME" list-apps 2>/dev/null | awk '{print $1}')

    # -------------------------------------------------
    # Python import check (diagnostic only)
    # -------------------------------------------------
    log ">> Checking Python imports (diagnostic)"
    if bench_exec --site "$SITE_NAME" console <<EOF
import frappe
frappe.init(site="$SITE_NAME")
frappe.connect()
print("Python imports OK")
exit()
EOF
    then
        log "OK: Python imports successful"
    else
        log "INFO: Python import issues detected (diagnostic)"
        import_errors=1
    fi

    # -------------------------------------------------
    # HTTP check (PRIMARY HEALTH SIGNAL)
    # -------------------------------------------------
    log ">> Verifying HTTP response"
    local http_status
    http_status="$(curl -s -o /dev/null -w "%{http_code}" "http://$SITE_NAME" || true)"

    if [ "$http_status" -ge 500 ]; then
        log "ERROR: HTTP $http_status returned"
        http_errors=1
        system_healthy=0
    elif [ "$http_status" -ge 400 ]; then
        warn "HTTP $http_status returned"
    else
        log "OK: HTTP $http_status returned"
    fi

    # -------------------------------------------------
    # Nginx upstream errors (runtime health)
    # -------------------------------------------------
    if [ -f /var/log/nginx/error.log ]; then
        if tail -n 300 /var/log/nginx/error.log \
           | grep -F "server: $SITE_NAME" \
           | grep -qiE 'connect\(\) failed|upstream prematurely closed'; then
            log "ERROR: Nginx upstream errors detected"
            nginx_errors=1
            system_healthy=0
        fi
    fi

    # -------------------------------------------------
    # Final summary (ALWAYS SUCCESS)
    # -------------------------------------------------
    section "Verification Summary"
    log "  Log issues      : $log_errors"
    log "  App issues      : $app_errors"
    log "  Import issues   : $import_errors"
    log "  HTTP issues     : $http_errors"
    log "  Nginx issues    : $nginx_errors"

    if [ "$system_healthy" -eq 1 ]; then
        log "STATE: HEALTHY (runtime OK)"
    else
        log "STATE: DEGRADED (runtime issues detected)"
    fi

    log "Verification completed (non-blocking)"
    trap - ERR
    exit 0
}

# ============================================================
# site::configure_proxy — nginx server block + optional certbot
# Env: SITE_NAME FRAPPE_UPSTREAM_PORT PROJECT_BASE_DIR TENANT_PROTOCOL
#      (+ LOCAL_HOSTS_ENTRY dev flag)
# ============================================================
site::configure_proxy() {
    require_env SITE_NAME FRAPPE_UPSTREAM_PORT PROJECT_BASE_DIR TENANT_PROTOCOL

    TENANT_PROTOCOL="$(echo "$TENANT_PROTOCOL" | tr '[:upper:]' '[:lower:]')"

    case "$TENANT_PROTOCOL" in
        http|https) ;;
        *)
            log "ERROR: TENANT_PROTOCOL must be 'http' or 'https'"
            exit 1
            ;;
    esac

    local nginx_conf_dir="/etc/nginx/conf.d"
    local nginx_conf_file="${nginx_conf_dir}/tenant_${SITE_NAME}.conf"

    local frappe_upstream="127.0.0.1:${FRAPPE_UPSTREAM_PORT}"
    local assets_path="${PROJECT_BASE_DIR}/bench/sites/assets"
    local certbot_bin
    certbot_bin="$(command -v certbot || true)"
    local le_live_dir="/etc/letsencrypt/live/${SITE_NAME}"

    log "Configuring Nginx for tenant"
    log "Protocol    : ${TENANT_PROTOCOL}"
    log "FQDN        : ${SITE_NAME}"
    log "Upstream    : ${frappe_upstream}"
    log "Assets path : ${assets_path}"

    # -------------------------------------------------
    # Preconditions
    # -------------------------------------------------
    if [ ! -d "$assets_path" ]; then
        log "ERROR: Assets directory not found: $assets_path"
        exit 1
    fi

    if [ "$TENANT_PROTOCOL" = "https" ] && [ -z "$certbot_bin" ]; then
        log "ERROR: certbot not installed but HTTPS requested"
        exit 1
    fi

    # -------------------------------------------------
    # Ensure asset directory permissions for Nginx
    # -------------------------------------------------
    log "Ensuring asset directory permissions"

    chmod o+x /home || true
    chmod o+x "$(dirname "$PROJECT_BASE_DIR")" || true
    chmod o+x "$PROJECT_BASE_DIR" || true
    chmod o+x "$PROJECT_BASE_DIR/bench" || true
    chmod o+x "$PROJECT_BASE_DIR/bench/sites" || true
    chmod -R o+rX "$assets_path" || true

    # -------------------------------------------------
    # Ensure WebSocket upgrade map exists (global)
    # -------------------------------------------------
    local nginx_map_file="${nginx_conf_dir}/websocket_map.conf"

    if [ ! -f "$nginx_map_file" ]; then
        log "Creating Nginx WebSocket map config"

        cat >"$nginx_map_file" <<EOF
# WebSocket connection upgrade mapping
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}
EOF
    else
        log "WebSocket map config already exists"
    fi

    _site_write_http_server_block() {
        local file="$1" site="$2" upstream="$3" assets="$4" project="$5"
        cat >"$file" <<EOF
# -------------------------------------------------
# Auto-generated tenant proxy (HTTP)
# Tenant: ${site}
# -------------------------------------------------

server {
    listen 80;
    server_name ${site};

    client_max_body_size 50m;

    location /assets/ {
        alias ${assets}/;
        expires 1y;
        add_header Cache-Control "public";
        access_log off;
    }

    location /socket.io {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;

        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        proxy_set_header Host \$host;
        proxy_set_header Origin \$scheme://\$host;

        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location /files/ {
        alias ${project}/bench/sites/${site}/public/files/;

        try_files \$uri =404;

        expires 1y;
        add_header Cache-Control "public";
        access_log off;
    }

    location / {
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Port 80;
        proxy_set_header X-Real-IP \$remote_addr;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 120;
        proxy_connect_timeout 120;

        proxy_pass http://${upstream};
    }
}
EOF
    }

    # -------------------------------------------------
    # Write HTTP server block (always)
    # -------------------------------------------------
    log "Writing Nginx HTTP config: $nginx_conf_file"

    _site_write_http_server_block "$nginx_conf_file" "$SITE_NAME" "$frappe_upstream" "$assets_path" "$PROJECT_BASE_DIR"

    # -------------------------------------------------
    # Validate & reload Nginx (HTTP)
    # -------------------------------------------------
    log "Validating Nginx (HTTP)"
    nginx -t

    log "Reloading Nginx (HTTP)"
    systemctl reload nginx

    # -------------------------------------------------
    # Optional: Add local /etc/hosts entry (dev only)
    # -------------------------------------------------
    if [ "${LOCAL_HOSTS_ENTRY:-false}" = "true" ]; then
        log "Ensuring local /etc/hosts entry for ${SITE_NAME}"

        local hosts_line="127.0.0.1 ${SITE_NAME}"

        if grep -qE "^[[:space:]]*127\.0\.0\.1[[:space:]]+${SITE_NAME}(\$|[[:space:]])" /etc/hosts; then
            log "/etc/hosts already contains entry for ${SITE_NAME}"
        else
            log "Adding /etc/hosts entry: ${hosts_line}"
            echo "${hosts_line}" >> /etc/hosts
        fi
    else
        log "LOCAL_HOSTS_ENTRY disabled — skipping /etc/hosts modification"
    fi

    # -------------------------------------------------
    # HTTPS setup (ONLY if requested)
    # -------------------------------------------------
    if [ "$TENANT_PROTOCOL" = "https" ]; then
        log "HTTPS enabled for tenant"

        if [ ! -d "$le_live_dir" ]; then
            log "Issuing Let's Encrypt certificate for ${SITE_NAME}"

            certbot certonly \
                --nginx \
                --non-interactive \
                --agree-tos \
                --register-unsafely-without-email \
                -d "${SITE_NAME}"
        else
            log "SSL already exists for ${SITE_NAME}, skipping certificate issuance"
        fi

        if ! grep -q "listen 443 ssl" "$nginx_conf_file"; then
            log "Adding HTTPS server block"

            cat >>"$nginx_conf_file" <<EOF

# -------------------------------------------------
# Auto-generated tenant proxy (HTTPS)
# Tenant: ${SITE_NAME}
# -------------------------------------------------

server {
    listen 443 ssl http2;
    server_name ${SITE_NAME};

    ssl_certificate     /etc/letsencrypt/live/${SITE_NAME}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${SITE_NAME}/privkey.pem;

    client_max_body_size 50m;

    location /assets/ {
        alias ${assets_path}/;
        expires 1y;
        add_header Cache-Control "public";
        access_log off;
    }

    location /socket.io {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;

        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;

        proxy_set_header Host \$host;
        proxy_set_header Origin \$scheme://\$host;

        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location /files/ {
        alias ${PROJECT_BASE_DIR}/bench/sites/${SITE_NAME}/public/files/;

        try_files \$uri =404;

        expires 1y;
        add_header Cache-Control "public";
        access_log off;
    }

    location / {
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header X-Real-IP \$remote_addr;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 120;
        proxy_connect_timeout 120;

        proxy_pass http://${frappe_upstream};
    }
}
EOF
        fi

        log "Validating Nginx (HTTPS)"
        nginx -t

        log "Reloading Nginx (HTTPS)"
        systemctl reload nginx
    else
        log "Protocol is HTTP only — skipping HTTPS and certbot"
    fi

    log "Tenant Nginx configuration completed successfully"
}
