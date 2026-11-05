# Read-only production inventory

Observed audit timestamp: `2026-10-24T23:21:31Z` (server-reported UTC).
These are point-in-time observations, not continuous capacity guarantees.

## Host

| Item | Observed |
|---|---|
| OS / virtualization | Ubuntu 24.04.4 LTS / KVM |
| Kernel | 6.8.0-147-generic |
| CPU | 6 AMD EPYC vCPUs |
| RAM | Approximately 11.7 GiB total; 7.0 GiB available |
| Swap | 4 GiB total; approximately 497 MiB used |
| Root filesystem | Approximately 193 GiB total; 130 GiB free |
| Load averages | Approximately 0.52 / 0.55 / 0.62 |
| Reboot marker | Not present |
| Docker / Podman | Not found |
| Privileges | `frank` has passwordless sudo |

## Runtime parity is mandatory

- Frappe **16.53.0**, ERPNext **16.52.1**.
- Bench application Python **3.14.3**.
- Socket.io Node executable **24.14.0**; Yarn **1.22.22**.
- MariaDB **10.11.14**, Ubuntu build.
- wkhtmltopdf **0.12.6**; PDF/font compatibility remains an acceptance test.
- The previous local Docker sandbox is v15/Python 3.11 and is not a valid
  production restore target. No major-version downgrade or incidental upgrade.

MariaDB baseline observed through a SELECT-only connection:

```text
sql_mode: STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,
          NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION
character_set_server: utf8mb4
collation_server: utf8mb4_general_ci
time_zone: SYSTEM
lower_case_table_names: 0
max_allowed_packet: 16777216
```

Use these as compatibility inputs, not instructions to alter host settings.

## Shared native Bench

All four sites use `/home/frappe/bench` and the same native web/workers/scheduler.

| Site | HTTP ping through local host TLS edge | Maintenance flag at audit | Installed apps | Estimated DB bytes |
|---|---:|---:|---:|---:|
| dalali.ndftechlabs.com | 503 | 1 | 12 | 102,136,016 |
| demo.ndftechlabs.com | 200 | Not set | 8 | 383,692,700 |
| dulamanenterprise.ndftechlabs.com | 503 | 1 | 3 | 53,957,488 |
| erp.ndftechlabs.com | 200 | Not set | 17 | 98,249,332 |

The 503s and maintenance flags **predate this migration**. They were not enabled
by the audit. Do not silently clear either site's maintenance flag.

MariaDB size figures are information_schema estimates, not exact dump sizes.

Supervisor observed all seven native processes RUNNING:

- Redis cache and queue in group `bench-redis`.
- Gunicorn and Socket.io in group `bench-web`.
- Scheduler plus short/long workers in group `bench-workers`.
- Gunicorn bind: `127.0.0.1:8081`; configured workers: 13.
- Socket.io: port 9000.
- Short worker listens on `short,default`; long on `long,default,short`.
- Cache Redis: 127.0.0.1:13000; queue Redis: 127.0.0.1:11000.
- Existing `redis_socketio` points to the cache endpoint (13000).

**Do not stop a shared Supervisor group to migrate a single site.** A validated
site-specific scheduler/job/automation fencing procedure is required.

## Dalali pilot

Installed app order reported by the database:

```text
frappe, erpnext, payments, flutterwave_gateway, webshop, dt_ecommerce,
dt_tenant, dt_recomendations, dt_dalali_ecommerce, hrms, lending,
frappe_mpsa_payments
```

- All 12 pilot app worktrees were clean at audit time; exact commits are recorded
  in [source-manifest.json](source-manifest.json).
- Database: 1,010 tables, approximately 97.4 MiB estimated data/index size.
- Public files: approximately 370.7 MiB allocated.
- Private files: approximately 900 KiB allocated.
- Existing private backup directory: approximately 16.9 MiB allocated. This is
  **not evidence** that backups are current, complete, off-server or restorable.
- Encryption key and DB password are present; values were not exported.
- Site timezone: Africa/Nairobi.
- Scheduled Job Type: 113 rows, all 113 not stopped; greatest `last_execution`
  value observed: `2026-10-03 22:36:11.707772` (database/site time).
- Outgoing Email Account rows enabled: 0; enabled Webhook rows: 0; pending
  Email Queue statuses Not Sent/Sending: no rows returned.

These counts do not prove that queues are drained or that custom integrations
cannot send email, payments, SMS or callbacks. Dalali has payment and tenant apps;
clone server egress and browser validation paths must be constrained.

## Sources outside the pilot

Twenty-one app directories exist on the native Bench. Non-pilot dirty worktrees:

| App | Tracked changes | Untracked entries |
|---|---:|---:|
| appe | 6 | 6 |
| crm | 2 | 0 |
| csf_ke | 0 | 1 |
| helpdesk | 3 | 0 |
| kenya_compliance_via_slade | 2 | 2 |

Do not reset, clean, discard or replace these. Later migrations must preserve
actual deployed files, not just branch names or remote repositories. This local
working copy of dt_master also has WIP; do not combine its feature changes with
the production infrastructure migration.

## Routing and other services

- Host Nginx serves public 80/443 and each tenant has a separate configuration.
- Dalali route: `/etc/nginx/conf.d/tenant_dalali.ndftechlabs.com.conf`.
- Its HTTP/HTTPS blocks send web/API to 127.0.0.1:8081 and websocket to port 9000.
- Its assets/public file locations currently alias native Bench directories.
  These locations must move with the pilot route, not remain on old assets/data.
- `ndftechlabs.com` and `smartlab.ndftechlabs.com` have unrelated host Nginx
  configurations/applications on ports 3000/4000; preserve both.
- Mail, MariaDB, Redis, PM2, Netdata, Fail2Ban and Cloudflared are active.
- Local Cloudflared configuration lists SSH to localhost:22. Provider-side DNS
  and remote routing still require confirmation; no routing was changed.

## Firewall / rootless readiness

- WireGuard route: `10.0.0.0/24` on `wg0`; IPv4 forwarding is already enabled.
- UFW is inactive. iptables uses the nft backend; inspected INPUT/FORWARD/OUTPUT
  policies are ACCEPT with no rules in those chains. Other/provider firewall
  controls were not exhaustively audited.
- Rootful Docker can change forwarding policy. A loss of VPN forwarding is an
  unacceptable unreviewed side effect, even if SSH remains available.
- cgroup v2 is active; cpu, memory, pids and io controllers are listed.
- User namespaces are enabled, but Ubuntu AppArmor restricts unprivileged user
  namespaces. Do not disable that restriction globally to make Docker work.
- `uidmap`, `slirp4netns`, rootlesskit and fuse-overlayfs tools were not found.
- Proposed user `dt-containers` does not exist. Existing subordinate UID/GID
  mappings are allocated; new non-overlapping ranges must be checked.
- An apt **simulation only** proposed four new packages: libslirp0, libsubid4,
  slirp4netns, uidmap; zero upgrades/removals using the current apt cache.
  No package index refresh or installation occurred. Recheck before installation.

Rootless feasibility is **not yet proven**: a narrowly scoped AppArmor solution,
verified downloads, user service/linger, actual cgroup delegation and resource
enforcement all need approved implementation and a synthetic test first.

## Master/orchestration dependency

The scoped master DB lookup returned exact Tenant Site records for demo, Dulaman
and ERP, all pointing to `NDF Tech Labs Contabo Node` with native project path
`/home/frappe/` and scripts `/home/frank/scripts`.

It did **not** return an exact Dalali FQDN record. Do not infer that Dalali has no
other automation or aliases. Confirm control-plane ownership and external jobs.
Any migrated site's automation must not keep targeting the old native Bench.
Container-aware execution must be tested or that site's automation explicitly
fenced; disabling access on the shared native node would affect other sites.
