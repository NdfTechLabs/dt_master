#!/usr/bin/env bash
# Read-only cached APT metadata check; never refreshes package indexes.
set -uo pipefail
python3 "$(dirname "$0")/check_docker_updates.py"
