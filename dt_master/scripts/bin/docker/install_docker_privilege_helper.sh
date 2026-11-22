#!/usr/bin/env bash
# Install the reviewed, fixed Docker lifecycle helper and its root-side policy
# artifacts. This bootstrap requires pre-existing non-interactive sudo.
set -Eeuo pipefail
umask 077

readonly HELPER_NAME="docker_lifecycle_helper.py"
readonly RELEASE_NAME="docker_helper_release.json"
readonly RELEASE_VALIDATOR="validate_docker_helper_release.py"
HELPER_SHA256=""
readonly PREVIOUS_HELPER_SHA256="b9deb8114d1efde9ef23f37dc3e327a913d5f0bcbfecc7ce9a2780e426391a9b"
readonly LEGACY_HELPER_SHA256="1cf27d95c853ce1b46bbb47cc3982baa1f7dfddbe123909c7c5e564d1eaed8fe"
readonly OLDER_LEGACY_HELPER_SHA256="8ec811c5ef0ae8c6f3a7733bb17cdbd088d9b5db9ae206f942b0e9267a34bf2a"
readonly OLDEST_LEGACY_HELPER_SHA256="afd072cd7d80a0182351ea6682779f703b95beef6c8695d8cee2a96dd59e9fa9"
readonly ANCIENT_HELPER_SHA256="06aa9fa0cf7218495b0dfde5667ae50ba00ed85ed45054606939d89fae2c47a2"
readonly POLICY_NAME="docker_install_policy.py"
POLICY_SHA256=""
readonly INVENTORY_NAME="docker_install_inventory.py"
INVENTORY_SHA256=""
readonly DEST_DIR="/usr/local/lib/dt-master/docker"

fail() {
    printf 'DOCKER_PRIVILEGE_INSTALL_FAILED: %s\n' "$1" >&2
    exit 1
}

for command in sudo sha256sum stat install mktemp mv rm python3; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command unavailable: $command"
done

[[ "$(id -u)" -ne 0 ]] || fail "Run this installer as the configured SSH user, not directly as root."
sudo -n true >/dev/null 2>&1 || fail "Non-interactive sudo is required for initial bootstrap."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
for name in "$RELEASE_NAME" "$RELEASE_VALIDATOR" "$HELPER_NAME" "$POLICY_NAME" "$INVENTORY_NAME"; do
    source_file="${script_dir}/${name}"
    [[ -f "$source_file" && ! -L "$source_file" ]] || fail "Trusted helper artifact is missing or is a symlink: $name"
done

# Resolve all component versions and digests from the canonical release manifest.
release_values="$(python3 "${script_dir}/${RELEASE_VALIDATOR}" "${script_dir}/${RELEASE_NAME}" "${script_dir}" )" \
    || fail "Docker Helper Release validation failed."
mapfile -t release_values <<< "$release_values"
[[ "${#release_values[@]}" -eq 4 ]] || fail "Docker Helper Release metadata is incomplete."
HELPER_SHA256="${release_values[0]}"
POLICY_SHA256="${release_values[1]}"
INVENTORY_SHA256="${release_values[2]}"

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

# Refuse unknown pre-existing artifacts. Accept the previously deployed N3.4.8
# validation-only helper and the earlier self-test-only helper during upgrade.
check_existing() {
    local name="$1" expected="$2" previous="${3:-}" previous_legacy="${4:-}" older_legacy="${5:-}" oldest_legacy="${6:-}" ancient="${7:-}"
    local target="${DEST_DIR}/${name}" metadata digest
    if sudo -n test -e "$target"; then
        sudo -n test -f "$target" && ! sudo -n test -L "$target" \
            || fail "Existing helper artifact is not a regular file: $name"
        metadata="$(sudo -n stat -c '%u:%g:%a' "$target")"
        [[ "$metadata" == "0:0:755" ]] || fail "Existing helper artifact has unsafe ownership or mode: $name"
        digest="$(sudo -n sha256sum "$target" | awk '{print $1}')"
        if [[ "$digest" != "$expected" && "$digest" != "$previous" && "$digest" != "$previous_legacy" && "$digest" != "$older_legacy" && "$digest" != "$oldest_legacy" && "$digest" != "$ancient" ]]; then
            fail "Refusing to overwrite an unrecognized helper artifact: $name"
        fi
    fi
}

# A pre-existing manifest must be a recognized predecessor of this release.
if sudo -n test -e "${DEST_DIR}/${RELEASE_NAME}"; then
    sudo -n test -f "${DEST_DIR}/${RELEASE_NAME}" && ! sudo -n test -L "${DEST_DIR}/${RELEASE_NAME}" \
        || fail "Existing release manifest is not a regular file."
    [[ "$(sudo -n stat -c '%u:%g:%a' "${DEST_DIR}/${RELEASE_NAME}")" == "0:0:755" ]] \
        || fail "Existing release manifest has unsafe ownership or mode."
    python3 "${script_dir}/${RELEASE_VALIDATOR}" "${script_dir}/${RELEASE_NAME}" "${script_dir}" "${DEST_DIR}/${RELEASE_NAME}" \
        >/dev/null || fail "Existing helper release is not a declared upgrade predecessor."
fi

check_existing "$HELPER_NAME" "$HELPER_SHA256" "$PREVIOUS_HELPER_SHA256" "$LEGACY_HELPER_SHA256" "$OLDER_LEGACY_HELPER_SHA256" "$OLDEST_LEGACY_HELPER_SHA256" "$ANCIENT_HELPER_SHA256"
# The currently deployed 0.3.0 helper pinned this exact prior policy digest.
# Accept it only as an explicit upgrade source; unknown policy files remain protected.
check_existing "$POLICY_NAME" "$POLICY_SHA256" "3fb6c49de806d380d5101679ae0c7364957d6686df5c09ff4a60c453297797f0"
check_existing "$INVENTORY_NAME" "$INVENTORY_SHA256" "99863a6d5d78c95ba3111d6574c3ae5c3bd653de004b24c0859904f86ac641a6"

stage_dir="$(sudo -n mktemp -d "${DEST_DIR}/.helper.XXXXXX")" \
    || fail "Could not stage helper artifacts in destination directory."
cleanup() {
    if [[ -n "${stage_dir:-}" ]]; then
        sudo -n rm -rf -- "$stage_dir" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

for name in "$RELEASE_NAME" "$POLICY_NAME" "$INVENTORY_NAME" "$HELPER_NAME"; do
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
for name in "$RELEASE_NAME" "$POLICY_NAME" "$INVENTORY_NAME" "$HELPER_NAME"; do
    sudo -n mv -f -- "${stage_dir}/${name}" "${DEST_DIR}/${name}" \
        || fail "Could not activate trusted helper artifact: $name"
done
sudo -n rmdir "$stage_dir" || fail "Could not remove helper staging directory."
stage_dir=""

for name in "$RELEASE_NAME" "$HELPER_NAME" "$POLICY_NAME" "$INVENTORY_NAME"; do
    metadata="$(sudo -n stat -c '%u:%g:%a' "${DEST_DIR}/${name}")"
    [[ "$metadata" == "0:0:755" ]] || fail "Installed helper artifact ownership/mode verification failed: $name"
done
[[ "$(sudo -n sha256sum "${DEST_DIR}/${RELEASE_NAME}" | awk '{print $1}')" == "$(sha256sum "${script_dir}/${RELEASE_NAME}" | awk '{print $1}')" ]] || fail "Installed release manifest digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${HELPER_NAME}" | awk '{print $1}')" == "$HELPER_SHA256" ]] \
    || fail "Installed helper digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${POLICY_NAME}" | awk '{print $1}')" == "$POLICY_SHA256" ]] \
    || fail "Installed install policy digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${INVENTORY_NAME}" | awk '{print $1}')" == "$INVENTORY_SHA256" ]] \
    || fail "Installed install inventory digest verification failed."

printf 'DOCKER_PRIVILEGE_HELPER_INSTALLED: %s\n' "${DEST_DIR}/${HELPER_NAME}"
