#!/usr/bin/env bash
# -------------------------------------------------
# install_bench.sh — thin wrapper.
# All logic lives in lib/node_lifecycle.sh (node::install_bench).
# Env contract & output keys unchanged (see lib module).
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/node_lifecycle.sh"

node::install_bench
