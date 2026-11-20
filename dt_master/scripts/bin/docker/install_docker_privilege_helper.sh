#!/usr/bin/env bash
# Install the reviewed, fixed Docker lifecycle helper into a root-owned path.
# This is a bootstrap operation and requires pre-existing non-interactive sudo.
set -Eeuo pipefail
umask 077

readonly HELPER_NAME="docker_lifecycle_helper.py"
readonly HELPER_SHA256="afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9"
readonly DEST_DIR="/usr/local/lib/dt-master/docker"
readonly DEST_FILE="${DEST_DIR}/${HELPER_NAME}"

fail() {
    printf 'DOCKER_PRIVILEGE_INSTALL_FAILED: %s\n' "$1" >&2
    exit 1
}

for command in sudo sha256sum stat install mktemp mv rm; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command unavailable: $command"
done

[[ "$(id -u)" -ne 0 ]] || fail "Run this installer as the configured SSH user, not directly as root."
sudo -n true >/dev/null 2>&1 || fail "Non-interactive sudo is required for initial bootstrap."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_file="${script_dir}/${HELPER_NAME}"
[[ -f "$source_file" && ! -L "$source_file" ]] || fail "Trusted helper source is missing or is a symlink."

actual_sha="$(sha256sum "$source_file" | awk '{print $1}')"
[[ "$actual_sha" == "$HELPER_SHA256" ]] || fail "Trusted helper digest does not match the installer-pinned digest."

# Trusted parent directories must not be symlinks, non-root-owned, or writable
# by group/other. Do not silently repair an unsafe system path.
for path in /usr /usr/local /usr/local/lib; do
    [[ ! -L "$path" ]] || fail "Trusted parent path is a symlink: $path"
    [[ -d "$path" ]] || fail "Trusted parent directory is missing: $path"
    owner="$(stat -c '%u' "$path")"
    mode="$(stat -c '%a' "$path")"
    [[ "$owner" == "0" ]] || fail "Trusted parent directory is not root-owned: $path"
    permissions=$((8#$mode))
    (( (permissions & 0022) == 0 )) || fail "Trusted parent directory is group/world writable: $path"
done

# Create only the application-owned directory. Existing paths are accepted
# only when they are real directories with the required ownership and mode.
if sudo -n test -e /usr/local/lib/dt-master; then
    sudo -n test -d /usr/local/lib/dt-master && ! sudo -n test -L /usr/local/lib/dt-master \
        || fail "Managed parent exists but is not a real directory."
    [[ "$(sudo -n stat -c '%u:%g:%a' /usr/local/lib/dt-master)" == "0:0:755" ]] \
        || fail "Managed parent directory has unexpected ownership or permissions."
else
    sudo -n install -d -o root -g root -m 0755 /usr/local/lib/dt-master \
        || fail "Could not create managed parent directory."
fi

if sudo -n test -e "$DEST_DIR"; then
    sudo -n test -d "$DEST_DIR" && ! sudo -n test -L "$DEST_DIR" \
        || fail "Helper destination exists but is not a real directory."
    [[ "$(sudo -n stat -c '%u:%g:%a' "$DEST_DIR")" == "0:0:755" ]] \
        || fail "Helper directory has unexpected ownership or permissions."
else
    sudo -n install -d -o root -g root -m 0755 "$DEST_DIR" \
        || fail "Could not create helper directory."
fi

tmp_file="$(sudo -n mktemp "${DEST_DIR}/.helper.XXXXXX")" \
    || fail "Could not stage helper in destination directory."
cleanup() {
    if [[ -n "${tmp_file:-}" ]]; then
        sudo -n rm -f -- "$tmp_file" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

if sudo -n test -e "$DEST_FILE"; then
    sudo -n test -f "$DEST_FILE" && ! sudo -n test -L "$DEST_FILE" \
        || fail "Existing helper is not a regular file."
    existing_owner="$(sudo -n stat -c '%u:%g:%a' "$DEST_FILE")"
    existing_sha="$(sudo -n sha256sum "$DEST_FILE" | awk '{print $1}')"
    [[ "$existing_owner" == "0:0:755" ]] || fail "Existing helper has unexpected ownership or permissions."
    [[ "$existing_sha" == "$HELPER_SHA256" ]] || fail "Refusing to overwrite an unrecognized helper version."
    printf 'DOCKER_PRIVILEGE_HELPER_ALREADY_CURRENT: %s\n' "$DEST_FILE"
    exit 0
fi

sudo -n install -o root -g root -m 0755 "$source_file" "$tmp_file" \
    || fail "Could not stage trusted helper."
staged_sha="$(sudo -n sha256sum "$tmp_file" | awk '{print $1}')"
[[ "$staged_sha" == "$HELPER_SHA256" ]] || fail "Staged helper digest verification failed."

# Atomic rename within the same destination filesystem.
sudo -n mv -- "$tmp_file" "$DEST_FILE" || fail "Could not activate trusted helper."
tmp_file=""

installed_owner="$(sudo -n stat -c '%u:%g:%a' "$DEST_FILE")"
installed_sha="$(sudo -n sha256sum "$DEST_FILE" | awk '{print $1}')"
[[ "$installed_owner" == "0:0:755" ]] || fail "Installed helper ownership/mode verification failed."
[[ "$installed_sha" == "$HELPER_SHA256" ]] || fail "Installed helper digest verification failed."

printf 'DOCKER_PRIVILEGE_HELPER_INSTALLED: %s\n' "$DEST_FILE"
