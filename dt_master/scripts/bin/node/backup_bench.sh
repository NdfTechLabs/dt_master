#!/usr/bin/env bash
set -euo pipefail
SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"
source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/backup.sh"
backup::run
