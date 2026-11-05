#!/usr/bin/env bash
# ============================================================
# lib/json.sh — jq wrappers with consistent defaults
# ============================================================

json_valid() {
    [ -n "$1" ] && echo "$1" | jq empty >/dev/null 2>&1
}

# json_get '<json>' '.a.b' — value or empty string
json_get() {
    local json="$1"
    local path="$2"
    echo "$json" | jq -r "${path} // empty"
}

# json_require '<json>' '.key' 'description' — abort if missing/empty
json_require() {
    local json="$1"
    local path="$2"
    local description="${3:-$2}"
    local value

    value=$(json_get "$json" "$path")
    if [ -z "$value" ]; then
        die "Missing/descriptor required but empty: ${description} (path=$path)"
    fi
    echo "$value"
}
