#!/usr/bin/env bash
# Install the reviewed, fixed Docker lifecycle helper and its root-side policy
# artifacts. This bootstrap requires pre-existing non-interactive sudo.
set -Eeuo pipefail
umask 077

readonly HELPER_NAME="docker_lifecycle_helper.py"
readonly HELPER_SHA256="643cbb59ddad6db29e2f95c97247ee524002d18db5857231472d394f81e968a7"
readonly PREVIOUS_HELPER_SHA256="afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9"
readonly POLICY_NAME="docker_install_policy.py"
readonly POLICY_SHA256="e04879b20e150b909921cd94e37b9d2e4263ca7873a657978ec3de7a8b74ad15"
readonly INVENTORY_NAME="docker_install_inventory.py"
readonly INVENTORY_SHA256="e0f45ecaa72aa3dd0492528a0cf1dbf0cf2d7073a9196efbeca4ef37c4a6ce95"
readonly DEST_DIR="/usr/local/lib/dt-master/docker"

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
for name in "$HELPER_NAME" "$POLICY_NAME" "$INVENTORY_NAME"; do
    source_file="${script_dir}/${name}"
    [[ -f "$source_file" && ! -L "$source_file" ]] || fail "Trusted helper artifact is missing or is a symlink: $name"
done

[[ "$(sha256sum "${script_dir}/${HELPER_NAME}" | awk '{print $1}')" == "$HELPER_SHA256" ]] \
    || fail "Trusted helper digest does not match the installer-pinned digest."
[[ "$(sha256sum "${script_dir}/${POLICY_NAME}" | awk '{print $1}')" == "$POLICY_SHA256" ]] \
    || fail "Install policy digest does not match the installer-pinned digest."
[[ "$(sha256sum "${script_dir}/${INVENTORY_NAME}" | awk '{print $1}')" == "$INVENTORY_SHA256" ]] \
    || fail "Install inventory digest does not match the installer-pinned digest."

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

# Refuse unknown pre-existing artifacts. The only accepted helper predecessor
# is the previously deployed self-test-only N2.4 helper.
check_existing() {
    local name="$1" expected="$2" previous="${3:-}"
    local target="${DEST_DIR}/${name}" metadata digest
    if sudo -n test -e "$target"; then
        sudo -n test -f "$target" && ! sudo -n test -L "$target" \
            || fail "Existing helper artifact is not a regular file: $name"
        metadata="$(sudo -n stat -c '%u:%g:%a' "$target")"
        [[ "$metadata" == "0:0:755" ]] || fail "Existing helper artifact has unsafe ownership or mode: $name"
        digest="$(sudo -n sha256sum "$target" | awk '{print $1}')"
        [[ "$digest" == "$expected" || ( -n "$previous" && "$digest" == "$previous" ) ]] \
            || fail "Refusing to overwrite an unrecognized helper artifact: $name"
    fi
}

check_existing "$HELPER_NAME" "$HELPER_SHA256" "$PREVIOUS_HELPER_SHA256"
check_existing "$POLICY_NAME" "$POLICY_SHA256"
check_existing "$INVENTORY_NAME" "$INVENTORY_SHA256"

stage_dir="$(sudo -n mktemp -d "${DEST_DIR}/.helper.XXXXXX")" \
    || fail "Could not stage helper artifacts in destination directory."
cleanup() {
    if [[ -n "${stage_dir:-}" ]]; then
        sudo -n rm -rf -- "$stage_dir" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

for name in "$POLICY_NAME" "$INVENTORY_NAME" "$HELPER_NAME"; do
    sudo -n install -o root -g root -m 0755 "${script_dir}/${name}" "${stage_dir}/${name}" \
        || fail "Could not stage trusted helper artifact: $name"
done

[[ "$(sudo -n sha256sum "${stage_dir}/${HELPER_NAME}" | awk '{print $1}')" == "$HELPER_SHA256" ]] \
    || fail "Staged helper digest verification failed."
[[ "$(sudo -n sha256sum "${stage_dir}/${POLICY_NAME}" | awk '{print $1}')" == "$POLICY_SHA256" ]] \
    || fail "Staged install policy digest verification failed."
[[ "$(sudo -n sha256sum "${stage_dir}/${INVENTORY_NAME}" | awk '{print $1}')" == "$INVENTORY_SHA256" ]] \
    || fail "Staged install inventory digest verification failed."

# Install support artifacts first and activate the new helper last. A failed
# partial staging can never expose the new helper without its dependencies.
for name in "$POLICY_NAME" "$INVENTORY_NAME" "$HELPER_NAME"; do
    sudo -n mv -f -- "${stage_dir}/${name}" "${DEST_DIR}/${name}" \
        || fail "Could not activate trusted helper artifact: $name"
done
sudo -n rmdir "$stage_dir" || fail "Could not remove helper staging directory."
stage_dir=""

for name in "$HELPER_NAME" "$POLICY_NAME" "$INVENTORY_NAME"; do
    metadata="$(sudo -n stat -c '%u:%g:%a' "${DEST_DIR}/${name}")"
    [[ "$metadata" == "0:0:755" ]] || fail "Installed helper artifact ownership/mode verification failed: $name"
done
[[ "$(sudo -n sha256sum "${DEST_DIR}/${HELPER_NAME}" | awk '{print $1}')" == "$HELPER_SHA256" ]] \
    || fail "Installed helper digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${POLICY_NAME}" | awk '{print $1}')" == "$POLICY_SHA256" ]] \
    || fail "Installed install policy digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${INVENTORY_NAME}" | awk '{print $1}')" == "$INVENTORY_SHA256" ]] \
    || fail "Installed install inventory digest verification failed."

printf 'DOCKER_PRIVILEGE_HELPER_INSTALLED: %s\n' "${DEST_DIR}/${HELPER_NAME}"
