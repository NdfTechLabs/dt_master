#!/usr/bin/env bash
# ============================================================
# lib/db.sh — MariaDB helpers
#
# MySQL credentials come from the standard contract vars:
#   MYSQL_ROOT_USER, MYSQL_ROOT_PASSWORD
# MYSQL_PWD is used instead of '-p<pass>' so the password is
# never visible in the process list.
#
#   mysql_exec SQL-ARGs...   — mysql as configured root
#   mysql_query_db DB SQL    — run SQL against a specific database
#   db_ensure_admin_user     — create/pw+grant MySQL admin user
# ============================================================

_db_mysql_base() {
    : "${MYSQL_ROOT_USER:?MYSQL_ROOT_USER not set}"
    : "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD not set}"

    env MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql -u"${MYSQL_ROOT_USER}" "$@"
}

mysql_exec() {
    _db_mysql_base "$@"
}

mysql_query_db() {
    local database="$1"
    shift
    _db_mysql_base "$database" "$@"
}

# ------------------------------------------------------------
# Ensure a MySQL superuser exists with password + grants
# (used by site provisioning; name only, idempotent rewrite)
# ------------------------------------------------------------
db_ensure_admin_user() {
    : "${MYSQL_ROOT_USER:?MYSQL_ROOT_USER not set}"
    : "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD not set}"

    # Credentials belong to the orchestrator. Never DROP/replace the account
    # we just authenticated with (especially root).
    mysql_exec -N -e 'SELECT 1' >/dev/null
    log "MariaDB admin credentials verified"
}

# ------------------------------------------------------------
# Drop a database and its same-named user (defensive cleanup)
# ------------------------------------------------------------
db_drop_database_and_user() {
    local db_name="$1"
    local db_user="${2:-$1}"
    [[ "$db_name" =~ ^[A-Za-z0-9_]+$ ]] || die "Invalid database name"
    [[ "$db_user" =~ ^[A-Za-z0-9_]+$ ]] || die "Invalid database user"
    [ "$db_user" != root ] && [ "$db_user" != "${MYSQL_ROOT_USER:-}" ] || die "Refusing to drop an administrative user"

    mysql_exec <<SQL
DROP DATABASE IF EXISTS \`$db_name\`;
DROP USER IF EXISTS '$db_user'@'localhost';
FLUSH PRIVILEGES;
SQL
}
