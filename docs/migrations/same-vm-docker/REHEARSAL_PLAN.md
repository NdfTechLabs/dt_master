# Isolated Dalali restore rehearsal — plan and execution gate

**Executed: technical checks passed.** See REHEARSAL_RESULT.md and CHANGELOG for
actual outcomes, separately approved clone-only migration and pre-migration
recovery scope. The plan below preserves approved constraints; it is not a replay
instruction or production cutover/cleanup authorization.

## Status

Operator requested **plan the isolated Dalali restore rehearsal and execute it**.
At planning time, read-only discovery quantified the new download/disk/pause scope;
no mutating phase began before the separate approval below. The earlier checkpoint
lock approval was not reused for this fresh rehearsal pause.

**Subsequent approval:** operator selected **Approve full rehearsal**, including
the exact 180-MB download/8-GiB rehearsal/55-second pilot lock scope below. This
authorizes the staged actions, not a claim that they are complete. Record phase
outcomes in CHANGELOG.md; synthetic isolation must still precede any source copy.

**Current outcome:** technical restore, crypto/PDF, private HTTP/auth/assets/
WebSocket, pre-migration rollback recovery and restart tests passed. The initially
discovered User.send_read_receipt gap was fixed by explicitly approved frozen-code
migration only in clone; native schema/traffic remain unchanged. Business/UI,
final data sync and production cutover/write-open remain separate gates.

Operator subsequently selected **Approve clone migration** (entry 052): first
prove a VM-private clone checkpoint can recover into a second new destination,
then migrate only the original isolated clone with frozen code, same budgets and
external effects blocked. Native schema stays unchanged; one migration attempt,
stop on error, no skip-failing/package upgrade/new production pause. Completion
was not assumed until the recovery and clone migration receipts passed; both now
exist with final technical results in rehearsal-v1/result.json on VM.

The validated application image is local dalali-v2, ID
`sha256:e8b4c638deb0c739fa7a2f932c5a79f6bd5a5be291393dd3f1d114840bf7e8eb`.
No rebuild, registry publication or cleanup is proposed for the rehearsal.

## Fresh preflight (2026-10-25)

- Protected configuration hashes and services match baseline; same seven native
  Bench PIDs running. HTTP baseline Dalali/Dulaman 503, demo/ERP 200 unchanged.
- Native Dalali maintenance=1, encryption key present (value never displayed).
- Database: **997 InnoDB + 3 MyISAM base tables**, **10 sequences**, about
  **102,136,016 bytes** estimated data/index size. No triggers/routines/events.
- Files: **5,843 public + 12 private**, **377,619,987 apparent bytes**;
  no symlinks/special files. Assets: ~45 MB, 239 regular files and 24 links,
  all within approved native assets/app roots. Copy only the selected site's data.
- Source scheduler rows remain 113 enabled with last execution Oct 3; zero enabled
  outgoing accounts/webhooks/pending email rows. These are not proof that all
  custom writers or side effects are inactive. Do not stop shared native workers.
- Native database 10.11.14; **both running native Redis instances 8.8.0**, confirmed
  via read-only INFO server (not inferred from an installed binary).
- Source SQL mode/collation/charset/max-packet unchanged; server SYSTEM timezone,
  host Europe/Berlin, observed offset UTC+1. Clone must match these settings.
- Port 18088 free; no current rootless application containers. About **83.6 GiB
  host free** after image preparation. Existing images/workspaces retained.

## Explicit new budget requiring approval

### Downloads directly to VM only

| Required image | Native-matched engine version | Compressed layer bytes |
|---|---|---:|
| Official MariaDB | 10.11.14 | 105,369,173 |
| Official Redis | 8.8.0 | 54,270,075 |
| **Total** | | **159,639,248** |

Pin exact amd64 manifests in rehearsal-images.json. Proposed new download cap:
**180,000,000 bytes**, including registry/config overhead. No application image
re-pull, pip/npm/apt downloads, backup transfer to workstation or archive decryption.
If another download is needed, estimate and ask first. Transfer logs stay private
on VM. Only small sanitized status reports traverse SSH.

Registry manifests/config digests and version ENV are verified, not a claim of
independently signed upstream provenance. MariaDB engine patch matches, but the
official distribution build is Ubuntu 22.04-based versus native Ubuntu 24.04.
Inspect/test binary version, SQL modes, table/sequence behavior and settings before
loading data; do not claim the database OS package is byte-identical to native.

### Separate rehearsal disk budget

Proposed **up to 8 GiB additional disk** measured from a new rehearsal baseline,
including images/unpack, source copies/dump, DB/files/assets volumes, recovery
copy, private probes and logs. Maintain **at least 60 GiB host free** throughout.
This is a new phase approval, not silently raising the earlier image-build 35-GiB
budget. Retain earlier artifacts and count all partial rehearsal attempts.

Keep existing aggregate **2 CPU / 3 GiB RAM / 256 MiB swap / TasksMax 1024** for
the dedicated account. Use a single web worker and bounded DB/Redis processes;
no unbounded stress test. Root-side collection is sequential nice 15 / idle I/O,
separately monitored. No hard filesystem quota is claimed.

### Fresh bounded pilot-only database pause

- Prestage public/private files into new root-private storage, verify hashes/modes.
- Recheck exact 1,000-base-table/10-sequence scope; refuse unexpected engines/views
  or source config/revision drift. Preserve original source files/configuration.
- Lock only Dalali base tables READ and sequences WRITE. **5-second acquisition
  deadline; 55-second maximum hold/watchdog; 60-second idle fallback**. No global
  lock, FLUSH TABLES WITH READ LOCK, native service restart or shared-worker stop.
- Use the already-tested sequence-aware dump helper, not checkpoint.main (which
  would create another encrypted archive). Capture sequence definitions/cache
  boundaries in the lock-owning session, exclude sequence tables from normal dump.
- Ordinary pilot reads remain available; pilot writes can wait briefly. Other
  sites' tables are not locked. Previous checkpoint held 29.596 seconds; this is
  an estimate, not a guarantee. Abort on deadline, release locks and retain evidence.
- Compare staged/source file/config manifests around capture; changes invalidate
  the rehearsal input. Do not silently keep a mismatched DB/files copy.

This is a **preparation/rehearsal copy**, not a final application-level quiescent
cutover snapshot. Locks and stable file observations do not prove there were no
in-flight application/provider writes. Final writer fencing remains a separate gate.

## New private ownership/layout

```text
rootless account/socket: existing dt-containers / run/user/5001/docker.sock
project: ndf-dalali-rehearsal-v1 (new, never production project)
site inside clone: dalali-rehearsal.invalid
root-private collection/journal: /var/lib/ndf-rootless-migration/rehearsal-v1/
private deployment config/secrets: /home/dt-containers/stacks/dalali-rehearsal-v1/
network/volumes: new project-labelled names, exact inventory recorded before writes
frontend publication: 127.0.0.1:18088 only (recheck unused before creation)
```

Refuse pre-existing unrecognized paths, network, containers, databases or volumes.
Do not reuse native /home/frappe/bench, host DB, native Redis, old Docker volumes,
Docker sockets, host networking or privileged mounts. All bind sources must be
reviewed private copies/config, never live production directories.

Every data operation must verify dedicated daemon rootless identity/data root,
resource labels, target server identity, network and expected empty destination.
An interrupted restore is **not** permission to drop/reimport into that database.
Stop for review; retain partial artifacts. No automatic down -v/prune/remove.

## Execution sequence

### 1. Dependency/isolation tests with synthetic data

Pull approved pinned images to intended rootless daemon, inspect exact versions.
Create only new labelled resources. Confirm aggregate limits and internal-only
network with no runtime default route; configure DNS to resolve project services
but not forward arbitrary external names. Test that native host services and
external destinations are unreachable before any production keys/data are loaded.

Database/Redis have **no published ports**. Start fresh MariaDB storage with
clone-only root/app passwords, native SQL/timezone settings and event scheduler
off. Bound Redis memory; no imported native queues/sessions/jobs. Verify clones
cannot join other Docker networks or reach production host endpoints.

### 2. Server-side collection under approved pause

Only after synthetic isolation passes, collect the new bounded dump, site files,
site encryption key/config and static asset metadata into private storage. Do not
decrypt or read the previously downloaded/VM encrypted archive. Do not download
new backups to the workstation or copy unrelated site/mail/TLS/SSH credentials.

Keep raw source configuration root-private. Generate a separate clone config with
new DB credentials, project-private Redis endpoints, clone host URL and logging.
Never emit source passwords/encryption key or decrypted values into chat/logs.

### 3. Restore into new empty isolated storage

Database name may remain the native logical name **inside the isolated new server**
so qualified sequence defaults remain correct. Server/user/network identity—not
name alone—must prove this is not native MariaDB. Confirm zero application objects
before import; use no --force and stop at the first SQL error.

Populate only new clone sites/assets volumes, with image Frappe ownership and
private config/file permissions. Use copy streams within VM; never mount live
site paths or expose root-private raw source staging to frontend.

Preserve source encryption key only in the private clone site configuration.
Compare base-table and sequence counts/definitions, file counts/checksums and
app installation order. No bench update/install/migrate/schema upgrade against
native or clone as a shortcut; report version/schema issues first.

### 4. Start limited private application roles

Start DB, independent Redis cache/queue, one web worker, Socket.io and internal
frontend only. **No scheduler, background worker, email sender or management
automation processes**. Disable clone scheduler/mail/webhook behavior explicitly;
egress blocking is the hard side-effect boundary, not disabled flags alone.

Use app image for frontend with explicit Nginx entrypoint and LD_LIBRARY_PATH
unset; do not run Debian Nginx through the native compatibility wrapper. New
frontend config serves copied assets/files, with private-file locations internal
and Frappe authorization enforced. No host Nginx/TLS/Cloudflare configuration change.

Native Dalali maintenance remains **1**. The distinct private clone may leave
maintenance solely for tests after isolation passes; that does not reopen Dalali.
Use clone-only test login credentials, reset only inside verified clone storage.
Access through localhost/SSH tunnel; no public DNS/ingress or real provider tests.

### 5. Automated acceptance without external side effects

- Recheck engine/runtime/app versions, exact target ownership and source health.
- Required apps, login/roles and representative existing record **counts**, without
  printing PII. Test disposable writes only in clone, never source/shared Redis.
- Compare all copied public/private attachments by manifest; test denied anonymous
  private-file access and authenticated access without exporting file contents.
- Verify encrypted-field decryptability using copied key, logging only pass/fail
  counts—not decrypted strings. Do not test a real payment/email/SMS/webhook.
- Check assets and WebSocket authentication/connection with isolated tooling.
- No unrestricted workstation browser session yet: JavaScript can contact real
  providers independently of server egress. Use network-intercepted/isolated
  browser tooling for later UI tests, or limit this stage to HTTP checks that
  never follow external redirects. CSP alone is not complete navigation isolation.
- Stop/restart only rehearsal roles and verify persistence. Rehearse a clone-only
  backup into a second **new empty recovery destination**, bounded by the same
  phase budget; never overwrite/reuse native or an existing restored database.
- Finish by comparing native service/PID/config/route and four-site baselines.

### 6. PDF/font parity gate (identified, not waived)

Native wkhtmltopdf is **0.12.6** (Ubuntu Qt5-linked); cached image has **0.12.6.1
with patched Qt**. Do not silently upgrade by using the latter. Native font tree
is about 12.8 MB; custom font tree is empty at preflight.

Within the rehearsal disk budget, prepare only reviewed private copies of native
PDF binary/needed library/plugin/font closure and run a synthetic no-data offline
probe with per-process compatibility paths/offscreen Qt if needed. No all-/usr
copy or global library replacement, live mounts, dependency downloads or image
rebuild. Preserve hash/source provenance and compare behavior to native baseline.
If exact native PDF parity cannot be achieved safely, report this blocker; DB/files
restore tests may be reported separately, **full rehearsal acceptance stays open**.
Do not call the pilot production-ready without assets/PDF/font acceptance.

## Stop/rollback/cleanup policy

Unexpected source drift, non-pilot health/config/PID changes, lock deadline, missing
image version, failed isolation, restore conflict, OOM, disk limit, or extra
download => stop and inspect. Stop only verified new rehearsal processes if
required; never native shared Supervisor/DB/Redis or host networking.

Keep project volumes, copies, old images, archives and logs. Do not clear source
maintenance, delete an old deployment or switch routes as rollback. After successful
migration and recovery/rollback retention, present an exact unused-file cleanup
list for approval. Active images/volumes and required backups are not disposable.

## Still separately gated after rehearsal

Application/business acceptance, exact Dalali master automation ownership/fencing,
final source writer pause/final synchronization, public route switch, write-open,
mail/other-site migrations and destructive cleanup. Approval of this rehearsal
does not approve any of them.
