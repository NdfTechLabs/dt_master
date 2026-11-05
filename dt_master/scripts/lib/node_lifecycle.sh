#!/usr/bin/env bash
# ============================================================
# lib/node_lifecycle.sh — node bootstrap & operations domain
#
# Entry points (used by bin/node/* wrappers):
#   node::install_deps          node::install_bench
#   node::init_project          node::prepare_production
#   node::start_services        node::stop_services
#   node::verify
#
# Supervisor groups are declared ONCE here and reused by every
# consuming function (previously duplicated in four scripts).
# ============================================================

NODE_GROUPS=(bench-redis bench-web)      # hard requirement at node level
SITE_GROUPS=(bench-workers)              # soft requirement (no site yet OK)

# ============================================================
# node::install_deps — dependency resolution entry point
# Env: SYSTEM_DEPENDENCIES PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
node::install_deps() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR SYSTEM_DEPENDENCIES

    # -------------------------------------------------
    # Create project directories as frappe
    # -------------------------------------------------
    SKIP_NVM=1 run_as_frappe mkdir -p "$PROJECT_BASE_DIR" "$PROJECT_LOGS_DIR"
    chown -R frappe:frappe "$PROJECT_BASE_DIR" "$PROJECT_LOGS_DIR"

    # -------------------------------------------------
    # Logging
    # -------------------------------------------------
    init_stream_log "$PROJECT_LOGS_DIR/install_deps.log"

    deps::resolve_all
}

# ============================================================
# node::install_bench — venv + frappe-bench install
# Env: PROJECT_BASE_DIR PROJECT_LOGS_DIR
#      + PYTHON_VERSION (or FRAMEWORK_CONTEXT_B64 with python_version)
#      + BENCH_PACKAGE (default frappe-bench)
# ============================================================
node::install_bench() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    # -------------------------------------------------
    # Resolve framework context (base64 → JSON)
    # -------------------------------------------------
    decode_framework_context

    # -------------------------------------------------
    # Resolve runtime (JSON → fallback → defaults)
    # -------------------------------------------------
    local python_version_json bench_package_json=""

    if command -v jq >/dev/null 2>&1 && json_valid "$FRAMEWORK_CONTEXT"; then
        python_version_json="$(ctx_get python_version)"
        bench_package_json="$(ctx_get bench_package)"
    else
        log "WARNING: FRAMEWORK_CONTEXT missing or invalid"
    fi

    PYTHON_VERSION="${python_version_json:-${PYTHON_VERSION:-}}"
    BENCH_PACKAGE="${bench_package_json:-${BENCH_PACKAGE:-}}"

    require_var PYTHON_VERSION "PYTHON_VERSION is required"
    : "${BENCH_PACKAGE:=frappe-bench}"

    # -------------------------------------------------
    # Logging
    # -------------------------------------------------
    init_stream_log "$PROJECT_LOGS_DIR/install_bench.log"

    echo "== Installing Bench (dynamic) =="
    echo ">> Python version: $PYTHON_VERSION"
    echo ">> Bench package: $BENCH_PACKAGE"

    local venv_dir="$PROJECT_BASE_DIR/.venv"
    local python_bin="python${PYTHON_VERSION}"

    # -------------------------------------------------
    # Validate Python
    # -------------------------------------------------
    if ! command -v "$python_bin" >/dev/null 2>&1; then
        log "ERROR: $python_bin not found. Required Python version is missing."
        exit 1
    fi

    echo ">> Using Python: $python_bin"

    # -------------------------------------------------
    # Create virtual environment
    # -------------------------------------------------
    if ! run_as_frappe test -d "$venv_dir"; then
        echo ">> Creating virtual environment at $venv_dir"
        run_as_frappe "$python_bin" -m venv "$venv_dir"
    else
        echo ">> Virtual environment already exists"
    fi

    # -------------------------------------------------
    # Install bench dynamically
    # -------------------------------------------------
    run_as_frappe env BENCH_PACKAGE="$BENCH_PACKAGE" bash -c "
        set -e

        # shellcheck source=/dev/null
        source '$venv_dir/bin/activate'

        echo '>> Upgrading pip toolchain'
        pip install --upgrade pip setuptools wheel

        echo '>> Installing bench package: $BENCH_PACKAGE'
        pip install $BENCH_PACKAGE

        echo '>> Verifying installation'
        python - <<'EOF'
import os
from importlib.metadata import version

pkg = os.environ.get('BENCH_PACKAGE', 'frappe-bench')
print(f'{pkg} version:', version(pkg))
EOF
    "

    echo "== Bench installed successfully =="
}

# ============================================================
# node::init_project — swap + bench init (framework-aware)
# Env: PROJECT_BASE_DIR PROJECT_LOGS_DIR
#      + PYTHON_VERSION (or FRAMEWORK_CONTEXT_B64)
# ============================================================
node::init_project() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    # -------------------------------------------------
    # Decode framework context (base64)
    # -------------------------------------------------
    decode_framework_context

    # -------------------------------------------------
    # Extract runtime values (safe)
    # -------------------------------------------------
    local framework_name framework_version
    framework_name=""
    framework_version=""

    if command -v jq >/dev/null 2>&1 && json_valid "$FRAMEWORK_CONTEXT"; then
        framework_name="$(ctx_get framework)"
        framework_version="$(ctx_get version)"
    fi

    # Fallbacks (single-level, explicit)
    framework_name="${framework_name:-${FRAMEWORK_NAME:-frappe}}"
    framework_version="${framework_version:-${FRAPPE_VERSION:-}}"
    PYTHON_VERSION="${PYTHON_VERSION:-}"

    require_var PYTHON_VERSION "PYTHON_VERSION is required"

    local python_bin="python${PYTHON_VERSION}"

    echo ">> Framework: $framework_name"
    echo ">> Version: $framework_version"
    echo ">> Python: $python_bin"

    # -------------------------------------------------
    # Resolve branch (framework-aware)
    # -------------------------------------------------
    local frappe_branch=""

    if [ "$framework_name" = "Frappe" ]; then
        if [ -n "$framework_version" ]; then
            frappe_branch="version-${framework_version}"
            echo ">> Using Frappe branch: $frappe_branch"
        else
            echo ">> Using latest Frappe version"
        fi
    fi

    # -------------------------------------------------
    # Logging
    # -------------------------------------------------
    init_stream_log "$PROJECT_LOGS_DIR/init_project.log"

    echo "== Initializing Project =="

    local venv_dir="$PROJECT_BASE_DIR/.venv"
    local bench_dir="$PROJECT_BASE_DIR/bench"

    # -------------------------------------------------
    # Validate Python
    # -------------------------------------------------
    if ! command -v "$python_bin" >/dev/null 2>&1; then
        log "ERROR: $python_bin is not installed"
        exit 1
    fi

    # -------------------------------------------------
    # Preconditions: verify tools (nvm once, then check)
    # -------------------------------------------------
    echo ">> Verifying system dependencies"

    require_var NODE_VERSION "NODE_VERSION is required to verify node/npm/yarn"

    nvm_load_root
    nvm use "$NODE_VERSION"

    local cmd
    for cmd in git node npm yarn; do
        command -v "$cmd" >/dev/null || {
            log "ERROR: $cmd is not installed"
            exit 1
        }
    done

    if ! command -v wkhtmltopdf >/dev/null 2>&1; then
        log "WARNING: wkhtmltopdf not installed"
    fi

    # -------------------------------------------------
    # Stop supervisor if running
    # -------------------------------------------------
    if command -v supervisorctl >/dev/null 2>&1; then
        if pgrep -x supervisord >/dev/null 2>&1; then
            echo ">> Stopping supervisor"
            systemctl stop supervisor || systemctl stop supervisord || true
            sleep 2
        fi
    fi

    # -------------------------------------------------
    # Validate venv
    # -------------------------------------------------
    if ! run_as_frappe test -d "$venv_dir"; then
        log "ERROR: Virtual environment missing"
        exit 1
    fi

    # -------------------------------------------------
    # Idempotency
    # -------------------------------------------------
    if run_as_frappe test -d "$bench_dir"; then
        echo ">> Bench already exists"
        exit 0
    fi

    # -------------------------------------------------
    # Ensure swap (idempotent)
    # -------------------------------------------------
    ensure_swap 4096

    # -------------------------------------------------
    # Node memory tuning
    # -------------------------------------------------
    export NODE_OPTIONS="--max-old-space-size=1024"

    # -------------------------------------------------
    # Initialize project (framework-aware)
    # -------------------------------------------------
    echo ">> Initializing project"

    run_as_frappe bash -c "
        set -e
        source '$venv_dir/bin/activate'
        cd '$PROJECT_BASE_DIR'

        if [ '$framework_name' = 'Frappe' ]; then
            if [ -n '$frappe_branch' ]; then
                bench init bench --frappe-branch '$frappe_branch' --python $python_bin
            else
                bench init bench --python $python_bin
            fi
        else
            echo 'ERROR: Unsupported framework: $framework_name'
            exit 1
        fi
    "

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------
    if ! run_as_frappe test -f "$bench_dir/Procfile"; then
        log "ERROR: Bench init failed"
        exit 1
    fi

    echo "== Project initialized successfully =="
}

# ============================================================
# node::prepare_production — supervisor + redis + nginx wiring
# Env: PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
node::prepare_production() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    local venv_activate="$PROJECT_BASE_DIR/.venv/bin/activate"
    local bench_dir="$PROJECT_BASE_DIR/bench"
    local bench_bin="${PROJECT_BASE_DIR}/.venv/bin/bench"
    local frappe_user="frappe"
    local config_file="$bench_dir/sites/common_site_config.json"

    [ -f "$venv_activate" ] || { log "$venv_activate missing"; exit 1; }
    [ -x "$bench_bin" ] || { log "Bench binary not found at $bench_bin"; exit 1; }
    [ -f "$config_file" ] || { log "Missing common_site_config.json"; exit 1; }

    # -------------------------------------------------
    # Global lock (node-level)
    # -------------------------------------------------
    with_lock "/var/lock/dt_prepare_production_node.lock" soft

    # -------------------------------------------------
    # Logging (root-owned)
    # -------------------------------------------------
    init_stream_log "$PROJECT_LOGS_DIR/prepare_production_node.log"

    echo "== Preparing node for production (NODE LEVEL) =="

    # -------------------------------------------------
    # Ensure Supervisor is installed and running
    # -------------------------------------------------
    echo ">> Ensuring Supervisor is installed and running"

    if ! command -v supervisorctl >/dev/null 2>&1; then
        echo ">> Supervisor not installed — installing"
        apt-get update -y
        apt_install supervisor
    fi

    if ! systemctl is-enabled supervisor >/dev/null 2>&1; then
        systemctl enable supervisor
    fi

    if ! systemctl is-active supervisor >/dev/null 2>&1; then
        echo ">> Supervisor not running — starting"
        systemctl start supervisor
    fi

    # -------------------------------------------------
    # Sanity check: supervisor control plane only
    # -------------------------------------------------
    echo ">> Verifying Supervisor control plane"
    sleep 4

    if ! systemctl is-active supervisor >/dev/null 2>&1; then
        echo "ERROR: Supervisor service is not active"
        systemctl status supervisor --no-pager || true
        exit 1
    fi

    if ! supervisorctl status >/dev/null 2>&1; then
        echo "ERROR: supervisorctl cannot communicate with supervisord"
        exit 1
    fi

    echo ">> Supervisor control plane is healthy"
    echo ">> Supervisor process summary:"
    supervisorctl status || true
    echo ">> Supervisor is installed and running"

    # -------------------------------------------------
    # Redis setup (bench-level, as frappe)
    # -------------------------------------------------
    if [ ! -f "$bench_dir/config/redis_queue.conf" ]; then
        echo ">> Setting up Redis configuration"
        run_as_frappe bash -c "
            # shellcheck source=/dev/null
            source '$venv_activate'
            cd '$bench_dir'
            bench setup redis
        "
    else
        echo ">> Redis already configured"
    fi

    # -------------------------------------------------
    # Supervisor setup (bench config as frappe)
    # -------------------------------------------------
    if [ ! -f "$bench_dir/config/supervisor.conf" ]; then
        echo ">> Setting up Supervisor configuration"
        run_as_frappe bash -c "
            # shellcheck source=/dev/null
            source '$venv_activate'
            cd '$bench_dir'
            bench setup supervisor --yes
        "
    else
        echo ">> Supervisor already configured (bench config exists)"
    fi

    # -------------------------------------------------
    # Nginx production setup (requires root)
    # -------------------------------------------------
    if [ ! -f "$bench_dir/config/nginx.conf" ]; then
        echo ">> Setting up Nginx production config"
        bash -c "
            # shellcheck source=/dev/null
            source '$venv_activate'
            cd '$bench_dir'
            bench setup production '$frappe_user' --yes
        "
    else
        echo ">> Nginx already configured"
    fi

    # -------------------------------------------------
    # Reload Supervisor configs
    # -------------------------------------------------
    echo ">> Reloading Supervisor configuration"
    supervisorctl reread
    supervisorctl update

    # -------------------------------------------------
    # Start Supervisor-managed services (shared group constants)
    # -------------------------------------------------
    echo ">> Starting Supervisor-managed services"

    local group
    for group in "${NODE_GROUPS[@]}"; do     # HARD requirement
        if supervisorctl start "${group}:*"; then
            echo ">> Started Supervisor group: $group"
        else
            echo "ERROR: Failed to start required Supervisor group: $group"
            exit 1
        fi
    done

    for group in "${SITE_GROUPS[@]}"; do     # SOFT requirement
        if supervisorctl start "${group}:*"; then
            echo ">> Started Supervisor group: $group"
        else
            echo "WARN: Supervisor group '$group' not fully started (likely no site exists yet)"
        fi
    done

    # -------------------------------------------------
    # Redis health check (config-driven)
    # -------------------------------------------------
    echo ">> Verifying Redis endpoints from config"

    local redis_ports port
    redis_ports="$(
        jq -r '.redis_cache?,.redis_queue?,.redis_socketio?' "$config_file" \
        | grep -v null \
        | sed -E 's|.*:([0-9]+)$|\1|' \
        | sort -u
    )"

    for port in $redis_ports; do
        if port_listening "$port"; then
            echo ">> Redis running on port $port"
        else
            echo "ERROR: Redis not running on required port $port"
            exit 1
        fi
    done

    # -------------------------------------------------
    # Reload Nginx
    # -------------------------------------------------
    echo ">> Reloading Nginx"
    systemctl reload nginx

    echo "== Node production preparation complete =="
}

# ============================================================
# node::start_services / node::stop_services
# Env: PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
node::start_services() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    local venv_activate="$PROJECT_BASE_DIR/.venv/bin/activate"
    [ -f "$venv_activate" ] || { log "Virtualenv activate file missing"; exit 1; }

    init_stream_log "$PROJECT_LOGS_DIR/start_services.log"

    echo "== Starting production services =="

    # Supervisor control-plane sanity (NOT process health)
    if [ ! -S /var/run/supervisor.sock ]; then
        log "ERROR: Supervisor socket missing"
        exit 1
    fi

    if ! supervisorctl pid >/dev/null 2>&1; then
        log "ERROR: supervisorctl cannot communicate with supervisord"
        exit 1
    fi

    echo ">> Supervisor control plane is healthy"

    echo ">> Reloading Supervisor configuration"
    supervisorctl reread
    supervisorctl update

    echo ">> Starting Supervisor-managed services"

    local group
    for group in "${NODE_GROUPS[@]}"; do      # HARD requirement
        if supervisorctl start "${group}:*"; then
            echo ">> Started Supervisor group: $group"
        else
            echo "ERROR: Failed to start required Supervisor group: $group"
            exit 1
        fi
    done

    for group in "${SITE_GROUPS[@]}"; do      # SOFT requirement
        if supervisorctl start "${group}:*"; then
            echo ">> Started Supervisor group: $group"
        else
            echo "WARN: Supervisor group '$group' not started (likely no site exists yet)"
        fi
    done

    # -------------------------------------------------
    # Reload Nginx (safe)
    # -------------------------------------------------
    echo ">> Reloading Nginx"
    systemctl reload nginx

    echo "== Production services start sequence complete =="
}

node::stop_services() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    local bench_dir="$PROJECT_BASE_DIR/bench"
    [ -d "$bench_dir" ] || { log "Bench directory missing"; exit 1; }

    init_stream_log "$PROJECT_LOGS_DIR/stop_services.log"

    echo "== Stopping production services for this bench =="

    # Supervisor control-plane sanity
    if [ ! -S /var/run/supervisor.sock ]; then
        log "Supervisor socket not present — nothing to stop"
        exit 0
    fi

    if ! supervisorctl pid >/dev/null 2>&1; then
        log "Supervisor not responding — nothing to stop"
        exit 0
    fi

    echo ">> Supervisor control plane is healthy"

    echo ">> Stopping Supervisor-managed services for this bench"

    local group
    for group in "${NODE_GROUPS[@]}" "${SITE_GROUPS[@]}"; do
        if supervisorctl stop "${group}:*"; then
            echo ">> Stopped Supervisor group: $group"
        else
            echo "WARN: Supervisor group '$group' may already be stopped"
        fi
    done

    # -------------------------------------------------
    # Reload Nginx (do NOT stop)
    # -------------------------------------------------
    if systemctl is-active --quiet nginx; then
        echo ">> Reloading nginx"
        systemctl reload nginx || warn "nginx reload failed"
    else
        echo "nginx not running — skipping reload"
    fi

    echo "== Bench production services stopped safely =="
}

# ============================================================
# node::verify — full node health report
# Env: PROJECT_BASE_DIR PROJECT_LOGS_DIR
# ============================================================
node::verify() {
    require_root
    require_env PROJECT_BASE_DIR PROJECT_LOGS_DIR

    local bench_dir="$PROJECT_BASE_DIR/bench"
    local venv_activate="$PROJECT_BASE_DIR/.venv/bin/activate"
    local sup_conf="$bench_dir/config/supervisor.conf"
    local nginx_conf="$bench_dir/config/nginx.conf"
    local redis_conf_dir="$bench_dir/config"
    local config_file="$bench_dir/sites/common_site_config.json"

    # -------------------------------------------------
    # Logging
    # -------------------------------------------------
    local log_file=""

    if [ -d "$PROJECT_BASE_DIR" ]; then
        mkdir -p "$PROJECT_LOGS_DIR"
        log_file="$PROJECT_LOGS_DIR/verify_production.log"
        [ -f "$log_file" ] || touch "$log_file"
        init_stream_log "$log_file"
    else
        echo "WARN: Project base directory not present yet: $PROJECT_BASE_DIR"
        echo "WARN: Logging to file disabled until project base exists"
    fi

    echo "== Verifying Frappe production environment =="
    echo "Timestamp: $(date -Is)"
    echo "Node: $(hostname)"
    echo ""

    # -------------------------------------------------
    # Node specifications
    # -------------------------------------------------
    echo ">> Node specifications"

    echo "Hostname: $(hostname)"
    echo "Uptime: $(uptime -p)"
    echo "Kernel: $(uname -r)"
    echo "OS:"
    lsb_release -ds 2>/dev/null || grep PRETTY_NAME /etc/os-release || true

    echo ""
    echo "CPU:"
    lscpu | grep -E 'Model name|Socket|Thread|Core|CPU\(s\)' || true

    echo ""
    echo "Memory:"
    free -h

    echo ""
    echo "Swap:"
    swapon --show || echo "No swap configured"

    echo ""
    echo "Disk usage:"
    df -hT /

    # -------------------------------------------------
    # Ownership & permissions
    # -------------------------------------------------
    echo ""
    echo ">> Ownership & permissions"

    _node_check_path() {
        local path="$1"
        if [ -e "$path" ]; then
            stat -c "INFO: %n → owner=%U group=%G perms=%a" "$path"
        else
            echo "WARN: $path missing"
        fi
    }

    _node_check_path "$PROJECT_BASE_DIR"
    _node_check_path "$PROJECT_BASE_DIR/.venv"
    _node_check_path "$bench_dir"
    _node_check_path "$bench_dir/apps"
    _node_check_path "$bench_dir/sites"
    _node_check_path "$bench_dir/config"

    # -------------------------------------------------
    # Bench directory structure (depth 2)
    # -------------------------------------------------
    echo ""
    echo ">> Bench directory structure (depth 2)"

    if [ -d "$bench_dir" ]; then
        find "$bench_dir" -maxdepth 2 -type d -print | sed 's/^/DIR: /'
    else
        echo "ERROR: Bench directory missing"
    fi

    # -------------------------------------------------
    # Critical file ownership checks
    # -------------------------------------------------
    echo ""
    echo ">> Critical file ownership"

    local critical_files=(
        "$sup_conf"
        "$nginx_conf"
        "$PROJECT_BASE_DIR/.venv/bin/bench"
    )

    local f
    for f in "${critical_files[@]}"; do
        if [ -f "$f" ]; then
            stat -c "INFO: %n → owner=%U group=%G perms=%a" "$f"
        else
            echo "WARN: Missing critical file: $f"
        fi
    done

    # -------------------------------------------------
    # System services
    # -------------------------------------------------
    echo ">> System services status"

    local svc
    for svc in nginx supervisor; do
        if systemctl is-active --quiet "$svc"; then
            echo "INFO: $svc is running"
        else
            echo "ERROR: $svc is NOT running"
        fi
    done

    echo ""

    # -------------------------------------------------
    # Supervisor control plane
    # -------------------------------------------------
    echo ">> Supervisor control plane"

    if [ -S /var/run/supervisor.sock ]; then
        echo "INFO: supervisor socket exists"
    else
        echo "ERROR: supervisor socket missing"
    fi

    if supervisorctl pid >/dev/null 2>&1; then
        echo "INFO: supervisorctl communication OK"
    else
        echo "ERROR: supervisorctl cannot communicate with supervisord"
    fi

    echo ""

    # -------------------------------------------------
    # Bench + virtualenv integrity
    # -------------------------------------------------
    echo ">> Bench & virtualenv integrity"

    if [ -f "$venv_activate" ]; then
        echo "INFO: virtualenv present"
    else
        echo "ERROR: virtualenv missing at $venv_activate"
    fi

    if [ -d "$bench_dir" ]; then
        echo "INFO: bench directory present"
    else
        echo "ERROR: bench directory missing"
    fi

    echo ""

    # -------------------------------------------------
    # Bench configuration files
    # -------------------------------------------------
    echo ">> Bench configuration files"

    for f in "$sup_conf" \
        "$nginx_conf" \
        "$redis_conf_dir/redis_queue.conf" \
        "$redis_conf_dir/redis_cache.conf"
    do
        if [ -f "$f" ]; then
            echo "INFO: Found $(basename "$f")"
        else
            echo "WARN: Missing $(basename "$f")"
        fi
    done

    echo ""

    # -------------------------------------------------
    # Redis runtime verification (from config)
    # -------------------------------------------------
    echo ">> Redis runtime verification"

    if [ -f "$config_file" ]; then
        local redis_ports port
        redis_ports="$(
            jq -r '.redis_cache?,.redis_queue?,.redis_socketio?' "$config_file" \
            | grep -v null \
            | sed -E 's|.*:([0-9]+)$|\1|' \
            | sort -u
        )"

        for port in $redis_ports; do
            if port_listening "$port"; then
                echo "INFO: Redis listening on port $port"
            else
                echo "ERROR: Redis NOT listening on port $port"
            fi
        done
    else
        echo "WARN: common_site_config.json missing — cannot derive Redis ports"
    fi

    echo ""

    # -------------------------------------------------
    # Supervisor-managed groups & processes (group-based)
    # -------------------------------------------------
    echo ">> Supervisor-managed processes (by group)"

    _node_inspect_group() {
        local group="$1"

        if supervisorctl status "${group}:*" >/dev/null 2>&1; then
            supervisorctl status "${group}:*" | while read -r line; do
                case "$line" in
                    *FATAL*|*EXITED*)
                        echo "WARN: $line"
                        ;;
                    *)
                        echo "INFO: $line"
                        ;;
                esac
            done
        else
            echo "WARN: Supervisor group '$group' not available or has no processes"
        fi
    }

    local group
    for group in "${NODE_GROUPS[@]}"; do
        _node_inspect_group "$group"
    done

    for group in "${SITE_GROUPS[@]}"; do
        _node_inspect_group "$group"
    done

    echo ""

    # -------------------------------------------------
    # Gunicorn / socket / port inspection (informational)
    # -------------------------------------------------
    echo ">> Gunicorn / web listeners"

    if ss -tulpn | grep -q gunicorn; then
        ss -tulpn | grep gunicorn | sed 's/^/INFO: /'
    else
        echo "WARN: No gunicorn listeners detected (acceptable if no site exists)"
    fi

    echo ""

    # -------------------------------------------------
    # Nginx bench server block inspection
    # -------------------------------------------------
    echo ">> Nginx bench server blocks"

    if nginx -t >/dev/null 2>&1; then
        echo "INFO: nginx configuration syntax is valid"
    else
        echo "ERROR: nginx configuration syntax is INVALID"
    fi

    echo ""

    local nginx_sites_dir="/etc/nginx/conf.d"

    # -------------------------------------------------
    # Bench-generated nginx config inspection
    # -------------------------------------------------
    echo ">> Bench nginx configuration (generated by bench)"

    if [ -f "$nginx_conf" ]; then
        echo "INFO: Bench nginx config found"

        stat -c "INFO: Path=%n | Owner=%U | Group=%G | Perms=%a | Modified=%y" \
            "$nginx_conf"

        echo ""

        echo "INFO: Nginx upstream blocks (bench-generated)"

        awk '
            $1 == "upstream" {in_block=1}
            in_block {print}
            in_block && /}/ {in_block=0; print ""}
        ' "$nginx_conf" \
        | sed 's/^/  /' \
        || echo "  WARN: No upstream blocks found"

        echo ""

        echo "INFO: Nginx server blocks (bench-generated)"

        awk '
            $1 == "server" && $2 == "{" {in_block=1}
            in_block {print}
            in_block && /}/ {in_block=0; print ""}
        ' "$nginx_conf" \
        | sed 's/^/  /' \
        || echo "  WARN: No server blocks found"
    else
        echo "WARN: Bench nginx config missing at $nginx_conf"
    fi

    echo ""

    # -------------------------------------------------
    # Active nginx server blocks referencing this bench
    # -------------------------------------------------
    echo ">> Active nginx server blocks for this bench"

    local found=0

    for f in "$nginx_sites_dir"/*.conf; do
        if [ -f "$f" ] && grep -q "$bench_dir" "$f"; then
            found=1
            echo "INFO: Server block file: $f"
            echo "----- BEGIN $f -----"
            sed 's/^/  /' "$f"
            echo "----- END $f -----"
            echo ""
        fi
    done

    if [ "$found" -eq 0 ]; then
        echo "WARN: No active nginx server blocks found for this bench"
    fi

    echo ""

    echo "== Production verification completed =="
}
