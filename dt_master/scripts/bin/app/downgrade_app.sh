#!/usr/bin/env bash
# -------------------------------------------------
# downgrade_app.sh — thin wrapper.
# All logic lives in lib/app_lifecycle.sh (app::downgrade).
# Env contract & output keys unchanged (see lib module).
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/app_lifecycle.sh"

app::downgrade
