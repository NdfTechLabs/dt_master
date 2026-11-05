#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

initialize() {
    python3 - "$DIR/.env" <<'PY'
import os, secrets, sys
path = sys.argv[1]
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('Keeping existing sandbox .env')
else:
    with os.fdopen(fd, 'w') as f:
        f.write('SANDBOX_PORT=8088\n')
        f.write('DB_ROOT_PASSWORD=' + secrets.token_hex(24) + '\n')
        f.write('ADMIN_PASSWORD=' + secrets.token_hex(16) + '\n')
    print('Created private sandbox credentials')
PY
}

COMPOSE=(docker compose --project-directory "$DIR" --env-file "$DIR/.env" -f "$DIR/compose.yaml")
compose() { "${COMPOSE[@]}" "$@"; }

case "${1:-help}" in
    init) initialize ;;
    up) initialize; exec "${COMPOSE[@]}" up --build -d --wait --wait-timeout 900 ;;
    status) compose ps -a ;;
    logs) compose logs --tail=100 "${2:-setup}" ;;
    stop) compose stop ;;
    down) compose down ;; # Keeps all sandbox data. Never deletes volumes.
    shell) compose exec backend bash ;;
    credentials) grep '^ADMIN_PASSWORD=' "$DIR/.env"; echo 'Username: Administrator' ;;
    smoke) exec "${COMPOSE[@]}" exec -T backend bench --site master.localhost execute dt_master.testing.docker_sandbox.smoke ;;
    integration)
        # Run the current API working copy in fresh CLI processes, without
        # requiring an image rebuild for each contract fix during development.
        for file in "$DIR/../../dt_master/api/"*.py; do
            compose cp "$file" "backend:/home/frappe/bench/apps/dt_master/dt_master/api/$(basename "$file")"
        done
        for relative in \
            dt_master/dt_master/doctype/tenant_installed_app/tenant_installed_app.json \
            dt_master/dt_master/doctype/tenant_site/tenant_site.py \
            dt_master/dt_master/doctype/tenant_site/tenant_site.js; do
            for service in backend runner; do
                compose cp "$DIR/../../$relative" "$service:/home/frappe/bench/apps/dt_master/$relative"
            done
        done
        compose exec -T backend bench --site master.localhost reload-doc dt_master doctype tenant_installed_app
        compose cp "$DIR/../../dt_master/testing/integration.py" backend:/home/frappe/bench/apps/dt_master/dt_master/testing/integration.py
        if [ -n "${2:-}" ]; then
            [[ "$2" =~ ^[a-f0-9]{10}$ ]] || { echo 'Invalid integration run ID' >&2; exit 1; }
            exec "${COMPOSE[@]}" exec -T backend bench --site master.localhost execute dt_master.testing.integration.run --kwargs "{\"resume_run_id\":\"$2\"}"
        else
            exec "${COMPOSE[@]}" exec -T backend bench --site master.localhost execute dt_master.testing.integration.run
        fi
        ;;
    *) echo 'Usage: sandbox.sh {init|up|status|logs [service]|shell|smoke|integration|credentials|stop|down}'; exit 1 ;;
esac
