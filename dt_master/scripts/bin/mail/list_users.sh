#!/usr/bin/env bash
# -------------------------------------------------
# list_users.sh — thin wrapper.
# All logic lives in lib/mail_stack.sh (mail::user_list).
# Env contract & output keys unchanged (see lib module).
# -------------------------------------------------
set -euo pipefail

SCRIPT_LIB_DIR="${SCRIPT_LIB_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)/lib}"

source "$SCRIPT_LIB_DIR/core.sh"
source "$SCRIPT_LIB_DIR/mail_stack.sh"

mail::user_list
