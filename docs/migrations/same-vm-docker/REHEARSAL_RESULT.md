# Isolated Dalali restore rehearsal — technical checks passed

**Production was not cut over. Native Dalali remains in maintenance.**
Project: `ndf-dalali-rehearsal-v1`, dedicated rootless account UID/GID 5001.
Application image:
`sha256:e8b4c638deb0c739fa7a2f932c5a79f6bd5a5be291393dd3f1d114840bf7e8eb`.

Private clone endpoint is VM loopback `127.0.0.1:18088` only. This is **not** an
invitation to open an unrestricted browser: browser-side integration/navigation
isolation and business acceptance are still required before interactive testing.
No public DNS/Cloudflare/Nginx/TLS change, new native service restart or cleanup.

## Executed and passed

| Check | Result |
|---|---|
| Pinned service images | MariaDB 10.11.14; Redis 8.8.0, matching native engine patches |
| Source collection | One pilot-only lock, **44.699 seconds**, within 55-second watchdog |
| Tables/sequences restored | **1,000 base tables / 10 sequences** |
| Database parity before clone test writes | All locked table row counts and sequence boundaries matched |
| Copied attachments | **5,855 files**, every byte/hash matched; assets/links verified |
| Encryption | Both encrypted fields decrypt; roundtrip passed before and after migration/restart |
| Exact native PDF | **wkhtmltopdf 0.12.6**, private binary/library/plugin/font copies, no renderer upgrade |
| PDF integration | Native synthetic and Frappe PDF rendering/font-text probes passed |
| Core HTTP | Ping, authenticated Desk HTML and guest website HTML passed |
| Login/permissions | Clone-only Administrator login; guest privileged API and private Note access denied |
| Representative records | **7** doctype count checks matched expected data |
| Assets | **70** compiled assets served correctly |
| Private attachments | Guest denied; authenticated response matched stored file hash |
| Realtime | Authenticated Socket.IO **polling and direct WebSocket** connections passed |
| Restart persistence | Restarted only rehearsal DB/Redis/web/Socket.IO; fresh auth/fixture/HTTP tests passed again |
| Egress/privilege | Internal network only, no default route, external/native TCP and external DNS blocked; no worker/scheduler processes |
| Source health | Protected fingerprints/services/PIDs/forwarding unchanged; four-site HTTP baseline preserved |

HTTP `/app` redirects to `/desk` in this frozen framework version. The probe
follows only validated private-clone HTTP paths, never public/provider redirects.
No real payment, email, SMS or webhook execution was tested or enabled.

## Existing source schema gap and explicitly approved clone migration

Login initially returned 500 because `tabUser.send_read_receipt` was missing.
Read-only metadata proved it absent in **native source and restored clone**, while
the frozen Frappe User definition expects it. This was not restore data loss.

The operator separately approved recovery checkpoint verification followed by
`bench migrate` **only inside the original isolated clone**, using the frozen app
code. No app/package/runtime upgrade or source schema migration occurred.
Clone migration completed; the field exists in clone and is **still absent in
native source**, verified read-only. Original native maintenance remains 1.

The clone controller uses a fresh empty readonly `config/pids` placeholder for
Bench layout recognition—not native Redis/Supervisor configuration. Operator
Frappe exec requires the native runtime wrapper and sites working directory.

## Recovery proof and its precise scope

Before clone schema changes, created a **VM-private pre-migration clone checkpoint**
and restored it into a second new DB/sites destination. Verified all 1,000 table
row counts, 10 sequence boundaries, 5,855 attachment hashes, marked test fixture
and both encrypted fields. Recovery hostname/server ID and config isolation passed.
Recovery DB was stopped but retained to conserve RAM; all recovery volumes remain.

This proves rollback to that checkpoint, **including its known old schema gap**.
It is not a claim that the pre-migration checkpoint can serve the newer frozen
code without schema synchronization. Current migrated clone was subsequently
validated through HTTP/PDF/realtime/restart checks. A new final production restore
must repeat the approved schema-sync procedure and its own backup/recovery checks.

Source capture is a rehearsal preparation snapshot, not proof of final source
application/provider writer quiescence. The synthetic Note and clone-only password
changes must never be promoted as authoritative production data.

## Isolation / operational details

- New labelled resources, no live Bench/host DB/native Redis mounts or connections.
- MariaDB/Redis have no published ports; new credentials/config remain private.
- No scheduler/background worker/email sender/management automation is running.
- App roles use readonly roots and cap-drop/no-new-privileges. Redis starts directly
  as its non-root image user rather than granting entrypoint capability-bounding
  privileges. Failed startup attempts/configs retained, not deleted.
- Vendor Nginx bypasses native LD_LIBRARY_PATH; only private tmpfs temp directories
  writable. Private files require Frappe authorization and internal redirects.
- Docker did not activate port publishing on the internal network. The dedicated
  RootlessKit mapping publishes exactly VM `127.0.0.1:18088` to the owned frontend
  address; containers retain no default route. Mapping ID/spec recorded. Frontend
  lifecycle/address reconciliation must be explicitly handled before production.
- Only private Nginx reloads were used after backend IP changes; host Nginx untouched.

## Budget at final receipt

| Resource | Recorded |
|---|---:|
| Download accounting | **169,639,248 bytes** including conservative metadata allowance; cap 180 MB |
| Additional rehearsal disk | **3,837,976,576 bytes (~3.57 GiB)**; cap 8 GiB |
| Host available disk | **85,947,277,312 bytes (~80.05 GiB)**; floor 60 GiB |
| Aggregate dedicated user | **2 CPU equivalents / 3 GiB RAM / 256 MiB swap / 1,024 tasks** |

No extra app image pull, dependency install or backup transfer to workstation.
Previously downloaded/server encrypted archive is untouched; no decryption/private
recovery key was used. All old images, contexts, failed containers and backup
copies remain retained. No cleanup/prune/drop is authorized by this result.

## Native HTTP baseline after all checks

| Site | HTTP |
|---|---:|
| Dalali | 503, original maintenance retained |
| Demo | 200 |
| Dulaman | 503, original maintenance retained |
| ERP | 200 |

## Private evidence / inspection

VM root-private journal and sanitized receipts:

```text
/var/lib/ndf-rootless-migration/rehearsal-v1/state.json
/var/lib/ndf-rootless-migration/rehearsal-v1/result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/source/snapshot-result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/restore-database-result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/restore-files-result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/pdf-probe.json
/var/lib/ndf-rootless-migration/rehearsal-v1/recovery-result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/clone-migration-result.json
/var/lib/ndf-rootless-migration/rehearsal-v1/http-checks.json
```

Private deployment/config copies:
`/home/dt-containers/stacks/dalali-rehearsal-v1/`.
Do not export raw source/checkpoint/config/credentials or logs containing PII.
Use explicit dedicated-account/socket Docker commands from the foundation result;
never rootful Docker or generic native bench lifecycle scripts.

## Not done / remaining production gates

Browser follow-up: see UI_ACCEPTANCE_RESULT.md. Clone-only CSP compatibility was
separately approved/applied; real login, six list responses, four form renders,
Note save/reload and guest denial succeeded. Full business/UI acceptance remains
blocked, with role/storefront/browser-file/PDF and other coverage incomplete.

1. Business/user-specific permissions and UI/workflow acceptance in constrained
   browser tooling; real provider side effects stay disabled. HTML/API/record-count
   checks do not prove every ecommerce/HR/lending workflow or complex PDF layout.
2. Resolve Dalali control-plane ownership/aliases and site-specific automation/
   queue/provider writer fencing. Do not disable the shared native node/workers.
3. Review final production stack/resources and RootlessKit/frontend lifecycle.
4. Fresh approved final source DB/files sync, new production destination, required
   frozen-code schema synchronization, parity and final recovery/rollback procedure.
   Do not reuse the test-mutated rehearsal copy or decrypt the old archive.
5. Explicit pilot route-switch and separate write-open approvals. Other sites/mail
   remain on their original services.
6. After successful migration/retention, review exact unused-file cleanup list;
   preserve active images/volumes, source WIP and required rollback/backups.
