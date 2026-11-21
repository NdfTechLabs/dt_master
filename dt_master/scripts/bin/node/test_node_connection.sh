#!/usr/bin/env bash
set -euo pipefail

# Harmless SSH round-trip marker. No privilege escalation or host mutation.
printf '%s\n' 'DT_MASTER_SSH_OK'
