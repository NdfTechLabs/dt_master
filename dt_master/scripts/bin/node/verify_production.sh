#!/usr/bin/env bash
# -------------------------------------------------
# verify_production.sh — thin wrapper.
# All logic lives in lib/node_lifecycle.sh (node::verify).
# Env contract & output keys unchanged (see lib module).
# -------------------------------------------------
set -uo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/node_lifecycle.sh"

node::verify
