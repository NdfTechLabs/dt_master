#!/usr/bin/env bash
set -euo pipefail

# Read-only Host discovery. The Python probe uses only the standard library
# and read-only OS commands; it never invokes sudo or changes Host state.
if ! command -v python3 >/dev/null 2>&1; then
    printf 'HOST_DISCOVERY_PYTHON3_MISSING\n' >&2
    exit 127
fi

exec python3 "${BASE_DIR}/bin/node/inspect_host_discovery.py"
