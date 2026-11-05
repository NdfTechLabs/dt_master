#!/usr/bin/env bash
# ============================================================
# lib/core.sh — single bootstrap for ALL scripts.
#
#   Usage (bin/* wrapper):
#     SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-...derived...}"
#     source "$SCRIPT_LIB_DIR/core.sh"
#
#   Resolves SCRIPT_LIB_DIR if unset (relative to this file),
#   then sources the full library stack once, in dependency order.
#   Wrappers must only call functions after sourcing core.sh.
# ============================================================

# Derive lib dir from our own location if not provided by the caller
if [ -z "${SCRIPT_LIB_DIR:-}" ]; then
    SCRIPT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
export SCRIPT_LIB_DIR

# Idempotency guard
if [ -n "${_DT_CORE_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi

_DT_CORE_LOADED=1
# This guard is shell-local: exporting it breaks bootstrap in child shells.

_LIB_DIR="$(cd "$SCRIPT_LIB_DIR" && pwd)"

source "$_LIB_DIR/log.sh"
source "$_LIB_DIR/env.sh"
source "$_LIB_DIR/state.sh"
source "$_LIB_DIR/runtime.sh"
source "$_LIB_DIR/python_env.sh"
source "$_LIB_DIR/json.sh"
source "$_LIB_DIR/system.sh"
source "$_LIB_DIR/db.sh"
source "$_LIB_DIR/bench.sh"
source "$_LIB_DIR/gitlib.sh"

# NOTE: domain modules (deps.sh, app_lifecycle.sh, site_lifecycle.sh,
# mail_stack.sh, backup.sh, bench_lifecycle.sh) are sourced explicitly
# by the glue library that needs them, NOT by core — keeps bootstrap lean.
