#!/usr/bin/env bash
# Local backups: PROJECT_BACKUP_DIR/<site>/<unique-stamp>/.
# Logs go to stderr; functions returning paths/counts keep stdout machine-only.

backup::resolve_dir() {
    printf '%s\n' "${PROJECT_BACKUP_DIR:-${PROJECT_BASE_DIR}/backups}"
}

backup::stamp() { date -u +%Y%m%d%H%M%S; }

backup::site() {
    local site="${1:-${SITE_NAME:-}}"
    [[ "$site" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "Invalid site name"
    site_exists "$site" || die "Site does not exist: $site"
    local root destination staging source_dir
    root="$(backup::resolve_dir)"
    # Frappe's normal backup cleanup expects private/backups to contain files,
    # not staging directories. Keep our isolated staging alongside that folder.
    source_dir="$PROJECT_BASE_DIR/bench/sites/$site/private"
    mkdir -p "$root/$site" || return
    chmod 700 "$root/$site" || return
    destination=$(mktemp -d "$root/$site/$(backup::stamp).XXXXXX") || return
    run_as_frappe mkdir -p "$source_dir" || return
    staging=$(run_as_frappe mktemp -d "$source_dir/.dt-backup.XXXXXX") || return
    log "Backing up site: $site" >&2
    if ! bench_exec --site "$site" backup --with-files --backup-path "$staging" >&2; then
        rm -rf -- "$staging" "$destination"
        return 1
    fi
    local file database=0 public=0 private=0
    for file in "$staging"/*; do
        [ -f "$file" ] || continue
        case "${file##*/}" in
            *-database.sql.gz) database=1 ;;
            *-private-files.tar|*-private-files.tgz) private=1 ;;
            *-files.tar|*-files.tgz) public=1 ;;
        esac
        cp -- "$file" "$destination/" || return
    done
    rm -rf -- "$staging"
    if [ "$database$public$private" != "111" ]; then
        rm -rf -- "$destination"
        log "ERROR: Incomplete backup for $site" >&2
        return 1
    fi
    cp -- "$PROJECT_BASE_DIR/bench/sites/$site/site_config.json" "$destination/site_config.json" || return
    printf '%s\n' "$site" > "$destination/site_name"
    chmod -R go-rwx "$destination" || return
    log "Backup stored: $destination" >&2
    printf '%s\n' "$destination"
}

backup::all_sites() {
    local count=0 site
    while IFS= read -r site; do
        [ -n "$site" ] || continue
        backup::site "$site" >/dev/null || return 1
        count=$((count + 1))
    done < <(list_sites)
    printf '%s\n' "$count"
}

backup::prune() {
    local site="$1" keep="${2:-${BACKUP_RETENTION:-5}}" root
    [[ "$site" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "Invalid site name"
    [[ "$keep" =~ ^[1-9][0-9]*$ ]] || die "Retention must be at least one"
    root="$(backup::resolve_dir)/$site"
    [ -d "$root" ] || return 0
    local directories=() directory
    for directory in "$root"/*; do
        [ -d "$directory" ] && [ ! -L "$directory" ] && [ -f "$directory/site_name" ] || continue
        [ "$(cat "$directory/site_name")" = "$site" ] || continue
        directories+=("$directory")
    done
    local excess=$((${#directories[@]} - keep)) index
    for ((index=0; index<excess; index++)); do
        log "Pruning old backup: ${directories[index]}" >&2
        rm -rf -- "${directories[index]}" || return
    done
}

backup::run() {
    require_env PROJECT_BASE_DIR
    require_venv
    [ -d "$PROJECT_BASE_DIR/bench/sites" ] || die "Bench sites directory is missing"
    with_lock "$PROJECT_BASE_DIR/.dt-bench-lifecycle.lock" fail
    local count destination
    if [ -n "${SITE_NAME:-}" ]; then
        destination=$(backup::site "$SITE_NAME") || return
        count=1
        emit site "$SITE_NAME"
        emit site_backup_dir "$destination"
    else
        count=$(backup::all_sites) || return
    fi
    emit backups_taken "$count"
    emit sites_skipped 0
    emit backup_dir "$(backup::resolve_dir)"
}
