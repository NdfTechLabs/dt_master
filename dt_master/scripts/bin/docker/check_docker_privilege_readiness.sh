#!/usr/bin/env bash
# Read-only check of the Docker helper privilege boundary.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
: "${EXPECTED_SSH_USER:?EXPECTED_SSH_USER is required}"
export EXPECTED_SSH_USER
exec python3 "${script_dir}/check_docker_privilege_readiness.py"
