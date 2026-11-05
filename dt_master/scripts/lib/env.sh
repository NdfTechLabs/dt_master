#!/usr/bin/env bash
# ============================================================
# lib/env.sh — environment contract helpers
#
#   require_env VAR...        — abort if any variable is unset/empty
#   require_var VAR MESSAGE   — abort with custom message
#   decode_framework_context  — base64 JSON context → $FRAMEWORK_CONTEXT
#   ctx_get KEY               — read key from $FRAMEWORK_CONTEXT (empty default)
#   json_valid STRING         — boolean (true/false)
#   json_get STRING PATH      — jq -r access (empty default)
# ============================================================

require_env() {
    local var
    for var in "$@"; do
        if [ -z "${!var:-}" ]; then
            log "ERROR: Missing required env var: $var"
            exit 1
        fi
    done
}

require_var() {
    local var="$1"
    local message="${2:-$var is required}"

    if [ -z "${!var:-}" ]; then
        log "ERROR: $message"
        exit 1
    fi
}

# ------------------------------------------------------------
# Framework context: base64-encoded JSON exported by orchestrator.
# Decodes into the global $FRAMEWORK_CONTEXT (defaults to "{}").
# ------------------------------------------------------------
FRAMEWORK_CONTEXT="${FRAMEWORK_CONTEXT:-"{}"}"

decode_framework_context() {
    FRAMEWORK_CONTEXT="${FRAMEWORK_CONTEXT:-"{}"}"

    if [ -n "${FRAMEWORK_CONTEXT_B64:-}" ]; then
        FRAMEWORK_CONTEXT=$(printf '%s' "$FRAMEWORK_CONTEXT_B64" | base64 -d) || die "Invalid FRAMEWORK_CONTEXT_B64"
    fi
}

# ------------------------------------------------------------
# Read a key out of $FRAMEWORK_CONTEXT (jq -r, empty default)
# ------------------------------------------------------------
ctx_get() {
    local key="$1"
    echo "$FRAMEWORK_CONTEXT" | jq -r ".${key} // empty"
}
