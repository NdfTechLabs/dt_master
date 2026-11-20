#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
readonly POLICY_FILE="/etc/sudoers.d/dt-master-docker"
readonly HELPER_PATH="/usr/local/lib/dt-master/docker/docker_lifecycle_helper.py"
fail() { printf 'DOCKER_SUDOERS_INSTALL_FAILED: %s\n' "$1" >&2; exit 1; }
for command in sudo visudo python3 stat mktemp mv rm tee cat chown chmod; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command unavailable: $command"
done
[[ "$(id -u)" -ne 0 ]] || fail "Run this installer as the configured SSH user, not directly as root."
username="$(id -un)"
[[ "$username" =~ ^[a-z_][a-z0-9_-]{0,31}\$?$ ]] || fail "SSH username cannot be safely represented in sudoers."
sudo -n true >/dev/null 2>&1 || fail "Non-interactive sudo is required for policy installation."
sudo -n test -x "$HELPER_PATH" || fail "Trusted Docker helper is not installed and executable."
sudo -n test -f /etc/sudoers || fail "Main sudoers policy is unavailable."
sudo -n test -d /etc/sudoers.d && ! sudo -n test -L /etc/sudoers.d || fail "Sudoers include directory is unsafe."
sudo -n test -x "$(command -v visudo)" || fail "visudo is unavailable to the privileged environment."
script_dir="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd -P)"
policy_generator="\${script_dir}/docker_sudoers_policy.py"
[[ -f "$policy_generator" && ! -L "$policy_generator" ]] || fail "Policy generator is missing or is a symlink."
expected_policy="$(python3 "$policy_generator" "$username")"
if sudo -n test -e "$POLICY_FILE"; then
    sudo -n test -f "$POLICY_FILE" && ! sudo -n test -L "$POLICY_FILE" || fail "Existing managed policy is not a regular file."
    existing_meta="$(sudo -n stat -c '%u:%g:%a' "$POLICY_FILE")"
    [[ "$existing_meta" == "0:0:440" ]] || fail "Existing managed policy has unexpected ownership or permissions."
    existing_content="$(sudo -n cat "$POLICY_FILE")"
    if [[ "$existing_content"$'\n' == "$expected_policy" ]]; then
        sudo -n visudo -cf "$POLICY_FILE" >/dev/null || fail "Existing managed policy is syntactically invalid."
        sudo -n -l -U "$username" "$HELPER_PATH" >/dev/null 2>&1 || fail "Existing policy does not grant the exact helper command."
        printf 'DOCKER_SUDOERS_POLICY_ALREADY_CURRENT: %s\n' "$POLICY_FILE"
        exit 0
    fi
    fail "Refusing to overwrite an unrecognized sudoers policy."
fi
tmp_file="$(sudo -n mktemp /etc/sudoers.d/.dt-master-docker.XXXXXX)" || fail "Could not stage sudoers policy."
activated=0
cleanup() {
    if [[ -n "\${tmp_file:-}" ]]; then sudo -n rm -f -- "$tmp_file" >/dev/null 2>&1 || true; fi
    if [[ "$activated" -eq 1 ]]; then sudo -n rm -f -- "$POLICY_FILE" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
printf '%s' "$expected_policy" | sudo -n tee "$tmp_file" >/dev/null || fail "Could not write staged policy."
sudo -n chown root:root "$tmp_file" || fail "Could not set staged policy ownership."
sudo -n chmod 0440 "$tmp_file" || fail "Could not set staged policy permissions."
sudo -n visudo -cf "$tmp_file" >/dev/null || fail "Candidate sudoers policy failed syntax validation."
sudo -n mv -- "$tmp_file" "$POLICY_FILE" || fail "Could not atomically activate sudoers policy."
tmp_file=""
activated=1
installed_meta="$(sudo -n stat -c '%u:%g:%a' "$POLICY_FILE")"
[[ "$installed_meta" == "0:0:440" ]] || fail "Installed sudoers ownership/mode verification failed."
installed_content="$(sudo -n cat "$POLICY_FILE")"
[[ "$installed_content"$'\n' == "$expected_policy" ]] || fail "Installed sudoers content verification failed."
sudo -n visudo -cf "$POLICY_FILE" >/dev/null || fail "Installed sudoers policy failed syntax validation."
sudo -n -l -U "$username" "$HELPER_PATH" >/dev/null 2>&1 || fail "Effective policy does not grant the exact helper command."
activated=0
printf 'DOCKER_SUDOERS_POLICY_INSTALLED: %s\n' "$POLICY_FILE"
\n' || fail "Could not generate a safe sudoers rule."
if sudo -n test -e "$POLICY_FILE"; then
    sudo -n test -f "$POLICY_FILE" && ! sudo -n test -L "$POLICY_FILE" || fail "Existing managed policy is not a regular file."
    existing_meta="$(sudo -n stat -c '%u:%g:%a' "$POLICY_FILE")"
    [[ "$existing_meta" == "0:0:440" ]] || fail "Existing managed policy has unexpected ownership or permissions."
    existing_content="$(sudo -n cat "$POLICY_FILE")"
    if [[ "$existing_content"$'\n' == "$expected_policy" ]]; then
        sudo -n visudo -cf "$POLICY_FILE" >/dev/null || fail "Existing managed policy is syntactically invalid."
        sudo -n -l -U "$username" "$HELPER_PATH" >/dev/null 2>&1 || fail "Existing policy does not grant the exact helper command."
        printf 'DOCKER_SUDOERS_POLICY_ALREADY_CURRENT: %s\n' "$POLICY_FILE"
        exit 0
    fi
    fail "Refusing to overwrite an unrecognized sudoers policy."
fi
tmp_file="$(sudo -n mktemp /etc/sudoers.d/.dt-master-docker.XXXXXX)" || fail "Could not stage sudoers policy."
activated=0
cleanup() {
    if [[ -n "\${tmp_file:-}" ]]; then sudo -n rm -f -- "$tmp_file" >/dev/null 2>&1 || true; fi
    if [[ "$activated" -eq 1 ]]; then sudo -n rm -f -- "$POLICY_FILE" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
printf '%s' "$expected_policy" | sudo -n tee "$tmp_file" >/dev/null || fail "Could not write staged policy."
sudo -n chown root:root "$tmp_file" || fail "Could not set staged policy ownership."
sudo -n chmod 0440 "$tmp_file" || fail "Could not set staged policy permissions."
sudo -n visudo -cf "$tmp_file" >/dev/null || fail "Candidate sudoers policy failed syntax validation."
sudo -n mv -- "$tmp_file" "$POLICY_FILE" || fail "Could not atomically activate sudoers policy."
tmp_file=""
activated=1
installed_meta="$(sudo -n stat -c '%u:%g:%a' "$POLICY_FILE")"
[[ "$installed_meta" == "0:0:440" ]] || fail "Installed sudoers ownership/mode verification failed."
installed_content="$(sudo -n cat "$POLICY_FILE")"
[[ "$installed_content"$'\n' == "$expected_policy" ]] || fail "Installed sudoers content verification failed."
sudo -n visudo -cf "$POLICY_FILE" >/dev/null || fail "Installed sudoers policy failed syntax validation."
sudo -n -l -U "$username" "$HELPER_PATH" >/dev/null 2>&1 || fail "Effective policy does not grant the exact helper command."
activated=0
printf 'DOCKER_SUDOERS_POLICY_INSTALLED: %s\n' "$POLICY_FILE"
