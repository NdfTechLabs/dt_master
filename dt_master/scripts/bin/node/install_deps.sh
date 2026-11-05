#!/usr/bin/env bash
# -------------------------------------------------
# install_deps.sh — thin wrapper.
# All logic lives in lib/deps.sh (deps::resolve_all), driven by
# lib/node_lifecycle.sh (node::install_deps: dirs, logging, root).
# Env contract & output keys unchanged (see lib modules).
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/deps.sh"
source "$SCRIPT_LIB_DIR/node_lifecycle.sh"

node::install_deps
