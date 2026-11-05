#!/usr/bin/env bash
# ============================================================
# lib/system.sh — OS-level primitives
#
#   apt_install PKG...       — non-interactive apt install
#   ensure_service SVC       — enable + start (idempotent)
#   ensure_dpkg PKG          — true if installed
#   port_listening PORT      — true if listening
#   ensure_swap REQUIRED_MB  — idempotent swap provisioning (from init_project)
# ============================================================

apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

ensure_service() {
    local svc="$1"

    systemctl enable "$svc"
    systemctl start "$svc"
}

ensure_dpkg() {
    dpkg -s "$1" >/dev/null 2>&1
}

port_listening() {
    local port="$1"
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$port$"
}

# ------------------------------------------------------------
# Idempotent swap provisioning.
#   - Sufficient swap (±16MB)  → no-op
#   - Inactive correct-size swapfile → activate
#   - Missing/wrong-size swapfile → recreate + persist fstab
# ------------------------------------------------------------
ensure_swap() {
    local REQUIRED_SWAP_MB="${1:-4096}"
    local TOLERANCE_MB=16
    local SWAPFILE="/swapfile"

    log ">> Checking swap configuration"

    local active_swap swapfile_active=false swapfile_size_mb=0 current_swap_mb

    active_swap=$(swapon --show 2>/dev/null | awk 'NR>1 {print $1}')

    if echo "$active_swap" | grep -Fxq "$SWAPFILE"; then
        swapfile_active=true
    fi

    if [ -f "$SWAPFILE" ]; then
        local size_bytes
        size_bytes=$(stat -c%s "$SWAPFILE")
        swapfile_size_mb=$((size_bytes / 1024 / 1024))
    fi

    current_swap_mb=$(swapon --show 2>/dev/null | awk 'NR>1 {sum+=$3} END {print int(sum)}')

    if [ -z "$current_swap_mb" ] || [ "$current_swap_mb" -eq 0 ]; then
        current_swap_mb=$(free -m | awk '/Swap:/ {print $2}')
    fi

    log ">> Current total swap: ${current_swap_mb}MB"
    log ">> Swapfile size: ${swapfile_size_mb}MB (active: $swapfile_active)"

    # -------------------------------------------------
    # Case 1: swap already sufficient (with tolerance)
    # -------------------------------------------------
    if [ "$current_swap_mb" -ge $((REQUIRED_SWAP_MB - TOLERANCE_MB)) ]; then
        log ">> Swap is sufficient — no changes needed"
        return 0
    fi

    log ">> Swap below required (${REQUIRED_SWAP_MB}MB), evaluating..."

    # -------------------------------------------------
    # Case 2: swapfile exists with correct size → activate
    # -------------------------------------------------
    if [ "$swapfile_size_mb" -ge $((REQUIRED_SWAP_MB - TOLERANCE_MB)) ] && \
       [ "$swapfile_size_mb" -le $((REQUIRED_SWAP_MB + TOLERANCE_MB)) ]; then

        if [ "$swapfile_active" = false ]; then
            log ">> Activating existing swapfile"
            swapon "$SWAPFILE"
        else
            log ">> Existing swapfile already active and within acceptable range"
        fi

    # -------------------------------------------------
    # Case 3: recreate swapfile
    # -------------------------------------------------
    else
        log ">> Swapfile size incorrect or missing — recreating"

        if [ "$swapfile_active" = true ]; then
            log ">> Disabling active swapfile"
            if ! swapoff "$SWAPFILE"; then
                log ">> swapoff failed, forcing swapoff -a"
                swapoff -a || {
                    log "ERROR: Could not disable swap"
                    exit 1
                }
            fi
        fi

        if [ -f "$SWAPFILE" ]; then
            log ">> Removing old swapfile"
            chattr -i "$SWAPFILE" 2>/dev/null || true
            rm -f "$SWAPFILE"
        fi

        log ">> Creating swapfile (${REQUIRED_SWAP_MB}MB)"

        fallocate -l "${REQUIRED_SWAP_MB}M" "$SWAPFILE" \
            || dd if=/dev/zero of="$SWAPFILE" bs=1M count="$REQUIRED_SWAP_MB"

        chmod 600 "$SWAPFILE"
        mkswap "$SWAPFILE"
        swapon "$SWAPFILE"
    fi

    # -------------------------------------------------
    # Persist in fstab
    # -------------------------------------------------
    if ! grep -q "^$SWAPFILE " /etc/fstab; then
        echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log ">> Swap ready"
}
