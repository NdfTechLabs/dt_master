#!/usr/bin/env bash
set -euo pipefail

# Read-only observation only. Do not add sudo, service mutations, package
# installation, Docker commands, or caller-controlled command execution here.

hostname_value="$(hostname -f 2>/dev/null || hostname)"
os_id="unknown"
os_version_id="unknown"
os_version_codename="unknown"
os_pretty_name="unknown"
os_id_like="unknown"
if [ -r /etc/os-release ]; then
    # /etc/os-release is the host's root-owned OS identity metadata.
    . /etc/os-release
    os_id="${ID:-unknown}"
    os_version_id="${VERSION_ID:-unknown}"
    os_version_codename="${VERSION_CODENAME:-${UBUNTU_CODENAME:-unknown}}"
    os_pretty_name="${PRETTY_NAME:-unknown}"
    os_id_like="${ID_LIKE:-unknown}"
fi
raw_architecture="$(uname -m)"
case "$raw_architecture" in
    x86_64|x64) architecture="amd64" ;;
    aarch64|arm64) architecture="arm64" ;;
    armv7l|armv7*) architecture="armv7" ;;
    i386|i486|i586|i686) architecture="386" ;;
    *) architecture="unknown" ;;
esac

package_manager="unknown"
if command -v apt-get >/dev/null 2>&1; then
    package_manager="apt"
elif command -v dnf >/dev/null 2>&1; then
    package_manager="dnf"
elif command -v yum >/dev/null 2>&1; then
    package_manager="yum"
elif command -v zypper >/dev/null 2>&1; then
    package_manager="zypper"
elif command -v apk >/dev/null 2>&1; then
    package_manager="apk"
elif command -v pacman >/dev/null 2>&1; then
    package_manager="pacman"
fi

service_manager="unknown"
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    service_manager="systemd"
elif command -v rc-service >/dev/null 2>&1; then
    service_manager="openrc"
elif [ -d /etc/init.d ] && command -v service >/dev/null 2>&1; then
    service_manager="sysvinit"
fi

cpu_cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '0')"
memory_bytes="$(awk '/^MemTotal:/ {print $2 * 1024; found=1} END {if (!found) print 0}' /proc/meminfo)"
root_available_bytes="$(df -B1 --output=avail / 2>/dev/null | tail -n 1 | tr -d ' ')"
bench_available="false"
if command -v bench >/dev/null 2>&1; then
    bench_available="true"
fi

swap_total_bytes="$(awk '/^SwapTotal:/ {print $2 * 1024; found=1} END {if (!found) print 0}' /proc/meminfo)"
command_available() {
    if command -v "$1" >/dev/null 2>&1; then printf 'true'; else printf 'false'; fi
}
python3_available="$(command_available python3)"
node_available="$(command_available node)"
npm_available="$(command_available npm)"
yarn_available="$(command_available yarn)"
nginx_available="$(command_available nginx)"
supervisorctl_available="$(command_available supervisorctl)"
redis_cli_available="$(command_available redis-cli)"
mariadb_available="false"
if command -v mariadb >/dev/null 2>&1 || command -v mysql >/dev/null 2>&1; then
    mariadb_available="true"
fi

printf 'hostname=%s\n' "$hostname_value"
printf 'os=%s\n' "$os_pretty_name"
printf 'os_id=%s\n' "$os_id"
printf 'os_version_id=%s\n' "$os_version_id"
printf 'os_version_codename=%s\n' "$os_version_codename"
printf 'os_id_like=%s\n' "$os_id_like"
printf 'raw_architecture=%s\n' "$raw_architecture"
printf 'architecture=%s\n' "$architecture"
printf 'package_manager=%s\n' "$package_manager"
printf 'service_manager=%s\n' "$service_manager"
printf 'cpu_cores=%s\n' "$cpu_cores"
printf 'memory_bytes=%s\n' "$memory_bytes"
printf 'root_available_bytes=%s\n' "$root_available_bytes"
printf 'bench_available=%s\n' "$bench_available"
