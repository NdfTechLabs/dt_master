#!/usr/bin/env bash
# ============================================================
# lib/gitlib.sh — git state inspection for app directories
#
# All functions run git AS the frappe user (bench-owned folders)
# and take an absolute APP_DIR as their first argument.
#
#   git_short_head APP_DIR     — 7-char commit hash ("" on failure)
#   git_current_branch APP_DIR — branch name ("" if detached)
#   git_is_dirty APP_DIR       — true/false
#   git_tag_exact APP_DIR      — exact tag on HEAD ("" on failure)
#   git_remote_name APP_DIR    — single remote's name
#   git_remote_url APP_DIR R   — URL for remote name R
# ============================================================

git_short_head() {
    local app_dir="$1"
    run_as_frappe git -C "$app_dir" rev-parse --short HEAD 2>/dev/null || true
}

git_current_branch() {
    local app_dir="$1"
    run_as_frappe git -C "$app_dir" branch --show-current 2>/dev/null || true
}

git_is_dirty() {
    local app_dir="$1"
    if run_as_frappe git -C "$app_dir" status --porcelain 2>/dev/null | grep -q .; then
        echo "true"
    else
        echo "false"
    fi
}

git_tag_exact() {
    local app_dir="$1"
    run_as_frappe git -C "$app_dir" describe --tags --exact-match 2>/dev/null || true
}

# Print "branch" or "detached" (empty if not a git work tree)
git_head_state() {
    local app_dir="$1"

    if ! run_as_frappe git -C "$app_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "no-git"
        return
    fi

    if run_as_frappe git -C "$app_dir" symbolic-ref -q HEAD >/dev/null 2>&1; then
        echo "branch"
    else
        echo "detached"
    fi
}

git_remote_name() {
    local app_dir="$1"
    local remotes
    remotes="$(run_as_frappe git -C "$app_dir" remote 2>/dev/null || true)"

    if [ "$(echo "$remotes" | grep -c .)" -eq 1 ]; then
        echo "$remotes"
    fi
}

git_remote_url() {
    local app_dir="$1"
    local remote="$2"
    run_as_frappe git -C "$app_dir" remote get-url "$remote" 2>/dev/null || true
}
