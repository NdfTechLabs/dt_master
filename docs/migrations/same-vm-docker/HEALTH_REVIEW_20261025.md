# Read-only health review after reported restart

Checks observed at approximately **2026-10-25 10:54–10:56 UTC**. This review did
not authorize or execute backup locks, installation, repair or traffic changes.

## Production observations

- SSH reached the expected `mail.ndftechlabs.com` host.
- VM reports boot at `2026-10-18T14:31:09Z`, uptime approximately 6 days 20 hours.
  All seven native Bench process IDs match the earlier inspection. This does not
  support a recent reboot of the production VM; which other server restarted is
  not established by these checks.
- All seven native Supervisor Bench processes RUNNING.
- All 13 checked protected service units active: Nginx, Supervisor, MariaDB,
  Redis, Postfix instance, Dovecot, Rspamd, OpenDKIM, Cloudflared, PM2, Netdata,
  Fail2Ban and SSH.
- HTTP/maintenance baseline unchanged in the observed responses:

| Site | HTTPS ping | Maintenance |
|---|---:|---:|
| dalali.ndftechlabs.com | 503 | 1 |
| demo.ndftechlabs.com | 200 | 0 |
| dulamanenterprise.ndftechlabs.com | 503 | 1 |
| erp.ndftechlabs.com | 200 | 0 |

- Approximately 6.6–6.7 GiB RAM available and 130 GiB disk free; swap used about
  311 MiB. Load snapshots ranged from 2.63 to 3.13 (one-minute) on six vCPUs.
  This is not a continuous performance guarantee or automatic go-live approval.
- WireGuard route remains present; inspected FORWARD policy remains ACCEPT.
- Docker executable not found on the inspected root path.
- The aborted checkpoint directory contains no SQL dump, encrypted archive or
  completed receipt. Only the earlier private staging/public-keyring was created.

## Exceptions / comparison limits

1. The old complete audit under `/tmp/opencode` is missing after the reported
   restart. Full byte-for-byte baseline comparison cannot be claimed.
2. Fourteen original SHA-256 fingerprints preserved in earlier command output
   were compared successfully: nine Nginx configs, four site configs and the
   local Cloudflared config. No differences in that known set.
3. The original Supervisor config and common_site_config.json fingerprints were
   not preserved outside the lost raw audit. Their current hashes were collected
   but cannot be compared byte-for-byte to those original values.
4. All 12 pilot app revisions/cleanliness match the committed migration manifest.
   The refreshed inventory is **for review**, not silently promoted to an approved
   baseline or proof that every configuration byte is unchanged.
5. `systemd-networkd-wait-online.service` is failed. Its recorded start/exit were
   Oct 23 06:28/06:30 CEST, before this migration work. Core services/network access
   currently work; no reset/restart/repair of that unit was attempted.

Private operational evidence, outside Git:

```text
/home/franklyne/backups/ndf-migration/healthchecks/review-inventory-20261025T105537Z.json
/home/franklyne/backups/ndf-migration/healthchecks/review-comparison-20261025T105537Z.json
```

The refreshed catalog confirms **1,000 base tables and 10 sequence objects**.
No customer table contents or private recovery key material were exported by
these checks. Inventory metadata is saved in a private local directory.

## Pending decision—not approved by this review

The unanswered proposal remains: within a bounded Dalali-only preparation
checkpoint, READ-lock its 1,000 ordinary tables and exclusively lock its 10
sequence objects to fence ID allocation. Sequence reads/allocations can wait;
other databases, mail and normal table reads are outside that exclusive scope.

Acquisition deadline: 5 seconds. Hold watchdog: 55 seconds; server idle fallback:
60 seconds. Local synthetic dump/restore/failure-release rehearsals passed earlier;
they must be rechecked before a production retry. No global MariaDB lock, table
engine conversion, service stop, mode change or Docker installation is included.

A retry also needs explicit acceptance of the refreshed checkpoint reference,
given the missing original full baseline. Until the operator chooses to proceed,
schedule or decline, production mutation remains paused.
