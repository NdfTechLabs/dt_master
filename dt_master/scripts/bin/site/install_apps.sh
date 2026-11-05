#!/usr/bin/env bash
# -------------------------------------------------
# install_apps.sh — thin wrapper.
# All logic lives in lib/site_lifecycle.sh (site::install_apps).
# Env contract & output keys unchanged (see lib module).
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/site_lifecycle.sh"

site::install_apps
