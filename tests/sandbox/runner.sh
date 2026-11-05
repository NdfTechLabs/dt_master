#!/usr/bin/env bash
set -euo pipefail
[ "${DT_DOCKER_SANDBOX:-}" = 1 ] || { echo 'Sandbox flag missing' >&2; exit 1; }
chmod go-w /home/frappe
mkdir -p /run/sshd /home/frappe/.ssh
chmod 700 /home/frappe/.ssh
chown frappe:frappe /home/frappe/.ssh
if [ ! -f /home/frappe/.ssh/id_ed25519 ]; then
    sudo -u frappe ssh-keygen -q -t ed25519 -N '' -f /home/frappe/.ssh/id_ed25519
fi
cp /home/frappe/.ssh/id_ed25519.pub /home/frappe/.ssh/authorized_keys
chown frappe:frappe /home/frappe/.ssh/authorized_keys
chmod 600 /home/frappe/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e -o PasswordAuthentication=no -o PermitRootLogin=no -o AllowUsers=frappe
