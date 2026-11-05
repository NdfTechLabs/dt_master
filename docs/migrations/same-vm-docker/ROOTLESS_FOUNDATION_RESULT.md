# Rootless foundation verified

Completed: **2026-10-25T14:38:06Z** (VM journal).
Scope: approved foundation only; **no real site data loaded or migrated**.

## Installed / created

| Component | Verified |
|---|---|
| Dedicated account | dt-containers, UID/GID 5001; supplementary groups only 5001 |
| Subordinate UID/GID | Both 362144:65536, checked non-overlapping |
| Engine | Docker 29.6.1, daemon host UID 5001 |
| Compose | v5.3.1 |
| Containerd binary | Pinned 2.4.2 package, extracted rather than system-installed |
| Cgroups | v2, systemd driver |
| Docker data | /home/dt-containers/.local/share/docker |
| Security options | seccomp builtin, rootless, cgroupns |
| Storage driver | overlayfs |
| Rootful system units | docker.service/docker.socket/containerd.service not found/inactive |

Verified aggregate limits on `user-5001.slice`: 2 CPU equivalents, 3 GiB memory;
configured MemoryHigh 2560M, swap max 256M, TasksMax 1024. Tested a container's
effective memory.max=67108864, cpu.max=`25000 100000`, pids.max=32.

Targeted AppArmor rule permits user namespaces for the root-owned pinned
rootlesskit binary. Global restriction remains unchanged. Docker's reported
container security options do not include AppArmor; do not confuse this targeted
host binary rule with a claim that rootless containers have every rootful feature.

Ubuntu prerequisites installed, with no unrelated upgrade/removal:

```text
libslirp0     4.7.0-1ubuntu3.1
libsubid4     1:4.13+dfsg1-4ubuntu3.2
uidmap       1:4.13+dfsg1-4ubuntu3.2
slirp4netns  1.2.1-1build2
```

Docker DEBs were hash-verified/extracted, not installed with dpkg. Only those four
reviewed Ubuntu prerequisite packages were installed with offline dpkg after APT
simulation proved the closure. An initial APT --no-download attempt refused the
local file paths before installing; cached files were reused, not redownloaded.

## Synthetic checks

- Tiny image pinned after pull to
  `busybox@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e`.
- No customer data or host directories mounted into test containers.
- Container and aggregate resource limits effective.
- Internal Docker network has the foundation label and `Internal=true`.
- A container on that network has no default egress route.
- Temporary test containers removed by their approved `--rm`; no production
  sites/databases/volumes/deployments were removed. Test image/network retained.
- No host-facing application port or real Frappe image started in this stage.

## Production checks

Installer's before/after comparison passed: protected configuration hashes,
active services, native Bench process state, stable routes and FORWARD rules
unchanged. Independent follow-up confirmed:

- Same seven native Bench process IDs, all RUNNING.
- Dalali 503, demo 200, Dulaman 503, ERP 200—maintenance baseline preserved.
- WireGuard route present; FORWARD policy ACCEPT.
- Only the new user's daemon active; no system-wide Docker/containerd units.

Download accounting: **114,297,998 bytes**, including a conservative 10 MB
reservation for the tiny image/registry metadata. Within approved 150 MB budget.
Downloads were direct to the VM, not backup transfers to the workstation.

## Operator inspection

Run through the normal SSH alias; use the explicit user/socket, not rootful Docker:

```sh
sudo -n -H -u dt-containers env \
  XDG_RUNTIME_DIR=/run/user/5001 \
  DOCKER_HOST=unix:///run/user/5001/docker.sock \
  PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  /opt/ndf-rootless/29.6.1/bin/docker info

sudo -n -H -u dt-containers env \
  XDG_RUNTIME_DIR=/run/user/5001 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/5001/bus \
  systemctl --user status docker.service
```

Root-private evidence:

```text
/var/lib/ndf-rootless-migration/state.json
/var/lib/ndf-rootless-migration/commands.log
/var/lib/ndf-rootless-migration/host-before/
```

Do not export the private host-before account files to chat or Git. The runtime
journal contains exact steps, package metadata, execution-code hashes and errors.
Completed bootstrap is not replayed by the helper; use inspection for later checks.

## Not done / next gate

No archive decryption, new backup download, real site restore, application image
build, native automation change, cutover or clearing maintenance mode. Dalali
still uses its original native deployment.

Next stage: review the production-matched image/source/build plan and separate
bandwidth budget (candidate Frappe base alone ~981 MB compressed on the VM). Do
not pull/build/load real site data or change routing merely because the foundation
checks passed. Destructive work still requires explicit approval.
