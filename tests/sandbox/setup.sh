#!/usr/bin/env bash
set -euo pipefail
[ "${DT_DOCKER_SANDBOX:-}" = 1 ] || { echo 'Sandbox flag missing' >&2; exit 1; }
cd /home/frappe/bench
ls -1 apps > sites/apps.txt
bench set-config -g db_host db
bench set-config -gp db_port 3306
bench set-config -g redis_cache redis://redis-cache:6379
bench set-config -g redis_queue redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379
bench set-config -gp socketio_port 9000
bench set-config -g restart_supervisor_on_update false
ssh-keyscan -H runner > /home/frappe/.ssh/known_hosts
chmod 600 /home/frappe/.ssh/known_hosts
for site in "$MASTER_SITE" "$TENANT_SITE"; do
    if [ ! -f "sites/$site/site_config.json" ]; then
        bench new-site "$site" --db-root-username root --db-root-password "$DB_ROOT_PASSWORD" \
            --mariadb-user-host-login-scope='%' --admin-password "$ADMIN_PASSWORD" --install-app erpnext
    fi
    if ! bench --site "$site" list-apps | awk '{print $1}' | grep -Fxq erpnext; then
        bench --site "$site" install-app erpnext
    fi
done
if ! bench --site "$MASTER_SITE" list-apps | awk '{print $1}' | grep -Fxq dt_master; then
    bench --site "$MASTER_SITE" install-app dt_master
fi
bench --site "$MASTER_SITE" migrate
bench --site "$MASTER_SITE" set-config host_name "http://master.localhost:${SANDBOX_PORT:-8088}"
bench --site "$TENANT_SITE" set-config host_name "http://tenant.localhost:${SANDBOX_PORT:-8088}"
bench --site "$MASTER_SITE" execute dt_master.testing.docker_sandbox.seed
echo 'Sandbox sites and demo DocTypes are ready.'
