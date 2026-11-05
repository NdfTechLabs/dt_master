#!/usr/bin/env bash
# Bench-wide operations. Removing a bench NEVER calls drop-site or drops SQL.

bench::validate_paths() {
    require_env PROJECT_BASE_DIR
    [[ "$PROJECT_BASE_DIR" = /* ]] || die "PROJECT_BASE_DIR must be absolute"
    local base
    base=$(realpath -m -- "$PROJECT_BASE_DIR")
    case "$base" in /|/home|/root|/tmp|/var|/etc|/usr|/opt|/srv) die "Unsafe project root: $base" ;; esac
    [ ! -L "$base/bench" ] && [ ! -L "$base/.venv" ] || die "Bench and virtualenv must not be symlinks"
    PROJECT_BASE_DIR="$base"
    [ -d "$base/bench/sites" ] || die "Bench sites directory is missing"
    local backups
    backups=$(realpath -m -- "${PROJECT_BACKUP_DIR:-$base/backups}")
    case "$backups/" in "$base/bench/"*|"$base/.venv/"*) die "Backups must be outside bench and virtualenv" ;; esac
    with_lock "$base/.dt-bench-lifecycle.lock" fail
}

bench::update() {
    bench::validate_paths
    require_venv
    local backup_dir=skipped sites_backed_up=0 site
    if [ "${BENCH_UPDATE_SKIP_BACKUP:-false}" != true ]; then
        backup_dir=$(backup::resolve_dir)
        sites_backed_up=$(backup::all_sites) || die "Pre-update backup failed; update aborted"
    fi
    emit backup_dir "$backup_dir"
    emit bench_update_sites "$sites_backed_up"
    NODE_OPTIONS=--max-old-space-size=4096 FRAPPE_NODE_OPTIONS=--max-old-space-size=4096 \
        bench_exec update --no-backup || die "Update failed; restore from $backup_dir"
    while IFS= read -r site; do
        [ -n "$site" ] || continue
        bench_exec --site "$site" migrate || die "Migration failed for $site; restore from $backup_dir"
    done < <(list_sites)
    if [ -S /var/run/supervisor.sock ] && supervisorctl pid >/dev/null 2>&1; then
        supervisorctl reread
        supervisorctl update
        supervisorctl restart 'bench-web:*' 'bench-workers:*'
    fi
    if [ "$backup_dir" != skipped ]; then
        while IFS= read -r site; do
            backup::prune "$site" "${BENCH_UPDATE_PRUNE_KEEP:-5}"
        done < <(list_sites)
    fi
    emit bench_updated true
    emit bench_update_rollback_hint "$backup_dir"
}

bench::migrate() {
    bench::validate_paths
    if [ "${BENCH_MIGRATE_SKIP_BACKUP:-false}" != true ]; then
        if [ -n "${SITE_NAME:-}" ]; then
            backup::site "$SITE_NAME" >/dev/null || return
        else
            backup::all_sites >/dev/null || return
        fi
    fi
    local site count=0
    local sites
    if [ -n "${SITE_NAME:-}" ]; then sites="$SITE_NAME"; else sites=$(list_sites); fi
    while IFS= read -r site; do
        [ -n "$site" ] || continue
        site_exists "$site" || die "Site does not exist: $site"
        bench_exec --site "$site" migrate || return
        count=$((count + 1))
    done <<< "$sites"
    emit bench_migrated true
    emit sites_migrated "$count"
}

bench::restore() {
    require_env PROJECT_BASE_DIR SITE_NAME RESTORE_DIR MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    bench::validate_paths
    site_exists "$SITE_NAME" || die "Site does not exist: $SITE_NAME"
    [ "${RESTORE_FILES_ONLY:-false}" != true ] || die "Files-only restore is not supported by this Bench CLI"
    [ -f "$RESTORE_DIR/site_name" ] && [ "$(cat "$RESTORE_DIR/site_name")" = "$SITE_NAME" ] \
        || die "Backup manifest does not match SITE_NAME"
    local databases=() public_files=() private_files=() file
    for file in "$RESTORE_DIR"/*; do
        [ -f "$file" ] || continue
        case "$file" in
            *-database.sql.gz) databases+=("$file") ;;
            *-private-files.tar|*-private-files.tgz) private_files+=("$file") ;;
            *-files.tar|*-files.tgz) public_files+=("$file") ;;
        esac
    done
    [ "${#databases[@]}:${#public_files[@]}:${#private_files[@]}" = 1:1:1 ] || die "Backup must contain exactly one database and both file archives"
    [ -f "$RESTORE_DIR/site_config.json" ] || die "Backup site configuration is missing"
    local encryption_key
    encryption_key=$(jq -r '.encryption_key // empty' "$RESTORE_DIR/site_config.json") || return
    [ -n "$encryption_key" ] || die "Backup encryption_key is missing"
    if [ "${RESTORE_SKIP_BACKUP:-false}" != true ]; then
        backup::site "$SITE_NAME" >/dev/null || return
    fi
    # Root-owned local backups need a private, frappe-readable staging copy.
    local staging
    staging=$(run_as_frappe mktemp -d "$PROJECT_BASE_DIR/bench/sites/$SITE_NAME/private/.dt-restore.XXXXXX") || return
    local args=(--site "$SITE_NAME" restore "$staging/${databases[0]##*/}")
    for file in "${databases[0]}" "${public_files[0]}" "${private_files[0]}"; do
        install -o frappe -g frappe -m 600 -- "$file" "$staging/" || return
    done
    args+=(--with-public-files "$staging/${public_files[0]##*/}" --with-private-files "$staging/${private_files[0]##*/}")
    args+=(--db-root-username "$MYSQL_ROOT_USER" --db-root-password "$MYSQL_ROOT_PASSWORD")
    if ! bench_exec "${args[@]}"; then
        rm -rf -- "$staging"
        return 1
    fi
    rm -rf -- "$staging"
    # Restore the original key used to encrypt Password fields in the dump.
    bench_exec --site "$SITE_NAME" set-config encryption_key "$encryption_key"
    bench_exec --site "$SITE_NAME" migrate
    clear_cache_and_reload
    emit site_restored true
    emit restore_dir "$RESTORE_DIR"
}

bench::remove() {
    bench::validate_paths
    require_root
    local bench_dir="$PROJECT_BASE_DIR/bench" supervisor_link="${SUPERVISOR_CONF_DIR:-/etc/supervisor/conf.d}/bench.conf"
    if [ -e "$supervisor_link" ] || [ -L "$supervisor_link" ]; then
        [ "$(readlink -f "$supervisor_link")" = "$bench_dir/config/supervisor.conf" ] \
            || die "Supervisor configuration belongs to another bench"
    fi
    if [ -S /var/run/supervisor.sock ] && supervisorctl pid >/dev/null 2>&1; then
        supervisorctl stop 'bench-web:*' 'bench-workers:*' 'bench-redis:*' || return
    fi
    if [ "${REMOVE_STOP_ONLY:-false}" = true ]; then
        emit bench_removed false
        emit stop_only true
        return 0
    fi
    # Preserve connection credentials and encryption keys for the retained DBs.
    local recovery site dropped=0 removed_nginx=0 conf
    recovery="$(backup::resolve_dir)/removed-bench/$(backup::stamp)"
    mkdir -p "$recovery" || return
    chmod 700 "$recovery"
    while IFS= read -r site; do
        [ -n "$site" ] || continue
        cp -- "$bench_dir/sites/$site/site_config.json" "$recovery/$site.json" || return
        dropped=$((dropped + 1))
        log "Removing site files; keeping database: $site"
    done < <(list_sites)
    chmod -R go-rwx "$recovery"
    if [ -L "$supervisor_link" ]; then
        rm -f -- "$supervisor_link"
        supervisorctl reread || return
        supervisorctl update || return
    fi
    for conf in "${NGINX_CONF_DIR:-/etc/nginx/conf.d}"/*.conf; do
        [ -f "$conf" ] || continue
        grep -Fq -- "$bench_dir/" "$conf" || continue
        rm -f -- "$conf" || return
        removed_nginx=$((removed_nginx + 1))
    done
    if [ "$removed_nginx" -gt 0 ]; then nginx -t && systemctl reload nginx || return; fi
    rm -rf -- "$bench_dir" "$PROJECT_BASE_DIR/.venv"
    emit bench_removed true
    emit databases_kept true
    emit sites_dropped "$dropped"
    emit nginx_confs_removed "$removed_nginx"
    emit recovery_dir "$recovery"
}
