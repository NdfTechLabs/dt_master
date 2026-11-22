#!/usr/bin/env bash
# Read-only inspection of the installed Docker Helper Release.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "${script_dir}/inspect_docker_helper_release.py"
