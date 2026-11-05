# Final Synchronization Proposal — Dalali Production

**Status: Proposal only. Not executed. No source pause, copy, schema change, traffic
switch, or write-open has occurred.** Every phase below requires separate explicit
approval before execution. Native Dalali remains `maintenance_mode=1`. Other native
sites, mail, host Nginx/TLS, Cloudflare, WireGuard, and monitoring are unchanged.

## 1. Objective

Migrate the isolated Dalali rehearsal clone into the production rootless Docker
destination on the same VM, preserving application/runtime versions and protecting
all four native sites. The test-mutated rehearsal dataset must **never** be promoted
as authoritative production data.

## 2. Preconditions That Must Be Resolved First

These are not assumptions — they must be confirmed before any final-sync step:

### 2.1 Dalali control-plane ownership and writer fencing

Read-only audits found **no matching Dalali record** in `tabInfrastructure Node` or
`tabTenant Site`. Control-plane ownership, aliases, external deployment scripts, and
site-specific automation must be identified and documented.

- Audit read-only first; do not invent ownership or disable the shared native node.
- Never stop shared native Supervisor/Redis/web/worker groups.
- Never deserialize untrusted Redis job pickles in privileged audit.
- Unknown writers block final sync.

Empty shared queue lengths (short/default/long = 0) do **not** prove absence of
pilot in-flight jobs or external automation. Queue lengths are not proof of source
quiescence.

### 2.2 Source quiescence

Before any snapshot, the following must be verified read-only:
- Pilot scheduler: 113 enabled entries, last execution `2026-10-03 22:36:11.707772`
- Shared queue lengths: short/default/long = 0
- Shared-bench frappe cron: 1 entry
- No native rootful Docker/containerd services
- Native Bench process identity/PIDs unchanged
- Host forwarding (iptables FORWARD) unchanged

## 3. Phase 1 — Fresh Source Snapshot (Bounded Pause)

### 3.1 Scope

Only the Dalali pilot site database and files are snapshotted. The snapshot goes into
**new private final-production storage**, not over an existing DB/volume.

### 3.2 Bounded Pause/Watchdog

- Fresh pilot-only source pause (separate from the one-time rehearsal lock).
- Bounded pause with watchdog and file-consistency check.
- Exact duration and watchdog limits must be specified in the execution approval.
- Source maintenance alone is insufficient; scheduler/queue/API/provider/automation
  fencing must accompany the pause.

### 3.3 What Is Not Done

- No live mount of production DB/volumes.
- No existing DB/volume overwrite.
- No downloaded archive decryption (encrypted artifact remains at
  `/home/franklyne/backups/ndf-migration/20261025T110703Z-dalali-56_si6yk.tar.gz.gpg`,
  SHA-256 `664d579e90bc913b62494e255517566defaf971ea9f360104755432db1b7d375`).
- No test-mutated rehearsal dataset promotion.
- No source schema migration implicit in the snapshot.

## 4. Phase 2 — Frozen-Code Schema Synchronization

### 4.1 Requirement

Final destination requires frozen-code schema synchronization because source
`User.send_read_receipt` remains absent while frozen Frappe code expects it.

### 4.2 Allowed

- `bench migrate` **only** in the new final destination, using frozen code
  (Frappe 16.53.0, ERPNext 16.52.1, Python 3.14.3, Node 24.14.0, Yarn 1.22.22,
  MariaDB 10.11.14, native wkhtmltopdf 0.12.6).
- Preserve rollback; compare schema/app/version/data/file/encryption parity.

### 4.3 Not Allowed

- No native schema migration (source `User.send_read_receipt` remains absent).
- No package/runtime upgrade.
- No implicit native migration.

## 5. Phase 3 — Parity Verification

Before cutover, verify in the new final destination:

| Item | Source Baseline | Destination Must Match |
|---|---|---|
| Base tables | 997 InnoDB + 3 MyISAM | Same count |
| Sequences | 10 | Same boundaries |
| Files | 5,855 attachments | Same attachment hashes |
| Encrypted fields | Both verified | Both verified |
| Python packages | 231 versions | Match |
| App versions | Frappe 16.53.0 / ERPNext 16.52.1 | Match |
| Native maintenance | Dalali = 1 | Preserved until cutover approval |

## 6. Phase 4 — Rollback Procedure

- Restore-test the final checkpoint before any traffic change.
- Original native configuration/PIDs/services retained as rollback reference.
- No destructive cleanup until after successful migration/recovery/retention.
- Exact unused-file list must be approved before any deletion.

## 7. Resource and Disk Budget

| Resource | Limit |
|---|---|
| Aggregate CPU | 2 CPU (unchanged) |
| Aggregate RAM | 3 GiB (unchanged) |
| Aggregate swap | 256 MiB (unchanged) |
| Aggregate tasks | 1024 (unchanged) |
| Rehearsal disk cap | 8 GiB (unchanged) |
| Host free floor | 60 GiB (unchanged) |

Final snapshot/restore must fit within existing budgets; no disk-cap increase
without separate approval.

## 8. Phase 5 — Cutover (Separate Approval)

### 8.1 What Requires Separate Approval

- Pilot route switch (native → container replacement).
- Public DNS/Cloudflare/Nginx/TLS change for `dalali.ndftechlabs.com`.
- Native maintenance clearing (`maintenance_mode=0`).

### 8.2 What Is Not Authorized by This Proposal

- Any source schema change.
- Any native service restart.
- Any host Nginx/TLS change.
- Any Cloudflare/WireGuard change.
- Any cleanup or deletion.

## 9. Phase 6 — Write-Open (Separate Approval)

Write-open is a **separate decision** from cutover. Even after route switch, the
site may remain read-only/maintenance until explicit write-open approval.

## 10. Cleanup (Deferred)

Cleanup occurs only after:
1. Successful migration/recovery,
2. Retention period satisfied,
3. Exact unused-file list approved by the operator.

Active images/volumes/PDF tools, source WIP, and required rollback/backups are
**not** disposable merely because sync/cutover completed.

## 11. Approval Checklist (Operator Must Confirm Separately)

- [ ] Dalali control-plane ownership/writer fencing resolved read-only
- [ ] Fresh source snapshot destination and bounded pause/watchdog approved
- [ ] Frozen-code schema sync in new final destination approved
- [ ] Parity verification plan approved
- [ ] Rollback procedure approved
- [ ] Resource/disk budget confirmed within existing limits
- [ ] Cutover (route switch) — **separate approval**
- [ ] Write-open — **separate approval**
- [ ] Cleanup list — **separate approval after retention**

## 12. What Has NOT Been Done

- No source snapshot taken.
- No source pause executed.
- No schema migration performed.
- No traffic switched.
- No write opened.
- No cleanup performed.
- No archive decrypted.

---

**This document is a proposal. Execution requires separate operator approval for
each phase above.**
