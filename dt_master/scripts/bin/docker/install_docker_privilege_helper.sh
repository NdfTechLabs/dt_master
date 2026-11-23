#!/usr/bin/env bash
# Install the reviewed, fixed Docker lifecycle helper and its root-side policy
# artifacts. This bootstrap requires pre-existing non-interactive sudo.
set -Eeuo pipefail
umask 077

readonly HELPER_NAME="docker_lifecycle_helper.py"
readonly RELEASE_NAME="docker_helper_release.json"
readonly RELEASE_VALIDATOR="validate_docker_helper_release.py"
HELPER_SHA256=""
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
manifest_temp=""
stage_dir=""
cleanup() {
    if [[ -n "${stage_dir:-}" ]]; then
        sudo -n rm -rf -- "$stage_dir" >/dev/null 2>&1 || true
    fi
    if [[ -n "${manifest_temp:-}" ]]; then
        rm -f -- "$manifest_temp" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
: "${EXPECTED_RELEASE_MANIFEST_B64:?The active node-assigned Docker Helper Release manifest is required.}"
for name in "$RELEASE_VALIDATOR" "$HELPER_NAME" "$POLICY_NAME" "$INVENTORY_NAME"; do
    source_file="${script_dir}/${name}"
    [[ -f "$source_file" && ! -L "$source_file" ]] || fail "Trusted helper artifact is missing or is a symlink: $name"
done

# Materialize the active node-scoped manifest. Never depend on a shared,
# checked-in manifest because Infrastructure Nodes may run different releases.
manifest_temp="$(mktemp "${TMPDIR:-/tmp}/dt-master-docker-release.XXXXXX")" \
    || fail "Could not stage the active Docker Helper Release manifest."
python3 - "$manifest_temp" <<'PY'
import base64
import json
import os
import sys

payload = base64.b64decode(os.environ["EXPECTED_RELEASE_MANIFEST_B64"], validate=True)
json.loads(payload)
with open(sys.argv[1], "wb") as manifest_file:
    manifest_file.write(payload + b"\n")
PY
manifest_source="$manifest_temp"

# Resolve all component versions and digests from the active node-assigned manifest.
release_values="$(python3 "${script_dir}/${RELEASE_VALIDATOR}" "$manifest_source" "${script_dir}")" \
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

# Refuse unknown pre-existing artifacts. The release manifest is the sole
# source of current and explicitly accepted predecessor component digests.
check_existing() {
    local name="$1" component_id="$2" expected="$3"
    local target="${DEST_DIR}/${name}" metadata digest candidate matched=false
    local accepted_output
    local -a accepted_hashes=()
    accepted_output="$(python3 "${script_dir}/${RELEASE_VALIDATOR}" "$manifest_source" "${script_dir}" --accepted-hashes "$component_id")" \
        || fail "Could not load accepted hashes for $name"
    mapfile -t accepted_hashes <<< "$accepted_output"
    if sudo -n test -e "$target"; then
        sudo -n test -f "$target" && ! sudo -n test -L "$target" \
            || fail "Existing helper artifact is not a regular file: $name"
        metadata="$(sudo -n stat -c '%u:%g:%a' "$target")"
        [[ "$metadata" == "0:0:755" ]] || fail "Existing helper artifact has unsafe ownership or mode: $name"
        digest="$(sudo -n sha256sum "$target" | awk '{print $1}')"
        for candidate in "${accepted_hashes[@]}"; do
            [[ "$digest" == "$candidate" ]] && matched=true && break
        done
        [[ "$matched" == true ]] || fail "Refusing to overwrite an unrecognized helper artifact: $name"
    fi
}

# A pre-existing manifest must be a recognized predecessor of this release.
if sudo -n test -e "${DEST_DIR}/${RELEASE_NAME}"; then
    sudo -n test -f "${DEST_DIR}/${RELEASE_NAME}" && ! sudo -n test -L "${DEST_DIR}/${RELEASE_NAME}" \
        || fail "Existing release manifest is not a regular file."
    [[ "$(sudo -n stat -c '%u:%g:%a' "${DEST_DIR}/${RELEASE_NAME}")" == "0:0:755" ]] \
        || fail "Existing release manifest has unsafe ownership or mode."
    python3 "${script_dir}/${RELEASE_VALIDATOR}" "$manifest_source" "${script_dir}" "${DEST_DIR}/${RELEASE_NAME}" \
        >/dev/null || fail "Existing helper release is not a declared upgrade predecessor."
fi

check_existing "$HELPER_NAME" lifecycle_helper "$HELPER_SHA256"
check_existing "$POLICY_NAME" install_policy "$POLICY_SHA256"
check_existing "$INVENTORY_NAME" host_inventory "$INVENTORY_SHA256"

stage_dir="$(sudo -n mktemp -d "${DEST_DIR}/.helper.XXXXXX")" \
    || fail "Could not stage helper artifacts in destination directory."
for name in "$RELEASE_NAME" "$POLICY_NAME" "$INVENTORY_NAME" "$HELPER_NAME"; do
    source_file="$manifest_source"
    if [[ "$name" != "$RELEASE_NAME" ]]; then source_file="${script_dir}/${name}"; fi
    sudo -n install -o root -g root -m 0755 "$source_file" "${stage_dir}/${name}" \
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
[[ "$(sudo -n sha256sum "${DEST_DIR}/${RELEASE_NAME}" | awk '{print $1}')" == "$(sha256sum "$manifest_source" | awk '{print $1}')" ]] || fail "Installed release manifest digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${HELPER_NAME}" | awk '{print $1}')" == "$HELPER_SHA256" ]] \
    || fail "Installed helper digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${POLICY_NAME}" | awk '{print $1}')" == "$POLICY_SHA256" ]] \
    || fail "Installed install policy digest verification failed."
[[ "$(sudo -n sha256sum "${DEST_DIR}/${INVENTORY_NAME}" | awk '{print $1}')" == "$INVENTORY_SHA256" ]] \
    || fail "Installed install inventory digest verification failed."

printf 'DOCKER_PRIVILEGE_HELPER_INSTALLED: %s\n' "${DEST_DIR}/${HELPER_NAME}"
