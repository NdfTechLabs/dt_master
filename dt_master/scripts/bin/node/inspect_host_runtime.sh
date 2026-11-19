#!/usr/bin/env bash
set -euo pipefail

# Read-only observation only. Do not add sudo, service mutations, package
# installation, Docker commands, or caller-controlled command execution here.

hostname_value="$(hostname -f 2>/dev/null || hostname)"
os_value="$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
architecture_value="$(uname -m)"
cpu_cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '0')"
memory_bytes="$(awk '/^MemTotal:/ {print $2 * 1024; found=1} END {if (!found) print 0}' /proc/meminfo)"
root_available_bytes="$(df -B1 --output=avail / 2>/dev/null | tail -n 1 | tr -d ' ')"
bench_available="false"
if command -v bench >/dev/null 2>&1; then
    bench_available="true"
fi

printf 'hostname=%s\n' "$hostname_value"
printf 'os=%s\n' "$os_value"
printf 'architecture=%s\n' "$architecture_value"
printf 'cpu_cores=%s\n' "$cpu_cores"
printf 'memory_bytes=%s\n' "$memory_bytes"
printf 'root_available_bytes=%s\n' "$root_available_bytes"
printf 'bench_available=%s\n' "$bench_available"
