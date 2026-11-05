# Dalali business/UI acceptance and final-sync gate

**Approval/update:** operator approved capped VM-side tools/tests and then the
narrow browser-only policy. Positive/negative namespace probes passed. Pinned
tools downloaded directly on VM (126.2 MB); normal Chrome sandbox synthetic render
passed. Exact-hash proxy/action guards and real clone UI checks have run. The
operator also separately approved the clone-only CSP unsafe-eval addition and
private frontend reload; no host policy/packages or native services changed.
**Business/UI acceptance remains blocked**: UI_ACCEPTANCE_RESULT.md records partial
results, denied actions, remaining coverage and unchanged production gates.

## Status and proposed tool budget

Operator requested UI/business acceptance, then final synchronization and
**separately approved cutover**. Technical rehearsal passed; guarded browser work
has progressed as recorded above. The following budget/matrix was proposed and
approved before tool download; it is not a claim that all matrix checks passed.

Initially the VM had no browser/automation package cached. Workstation Chrome exists, but that
would transfer cloned UI/assets/customer data to the workstation. Preferred route:
keep browser, credentials, DOM/customer data and temporary profile entirely on VM.

Known pinned artifacts (ui-browser.json):

| Artifact | Bytes |
|---|---:|
| Chrome headless shell 156.0.8078.47 | 125,799,226 |
| libasound2t64 + data 1.2.11-1ubuntu0.3 | 419,122 |
| **Total** | **126,218,348 (~126.2 MB)** |

Approved budget: **150 MB additional direct-VM downloads**, a separate cap from
the earlier 180-MB image stage. **Up to 1 GiB additional UI disk**, still inside
existing **8-GiB rehearsal cap**; keep **60 GiB host free**. Existing aggregate
**2 CPU / 3 GiB RAM / 256 MiB swap / 1024 tasks** unchanged; one bounded browser/
context/page at a time. Latest receipt: ~79.63 GiB free, ~3.99 GiB rehearsal use,
including ~0.39 GiB UI-added disk and exactly 126,218,348 browser/support bytes.

No app/runtime upgrade, app image pull, backup transfer or host package install.
Verify Google's fixed object generation/length/CRC32C, ZIP integrity and record
SHA-256; this is HTTPS/object integrity, not an independent release signature.
Verify support DEB SHA-256 against signed cached Ubuntu indexes and extract only
into private tool storage. Copy reviewed root-owned native libraries as needed;
stop/estimate/ask for any additional download, missing artifact or policy change.

## Containment before customer-data navigation

1. Recheck clone target/DB server 65001, technical journal, native maintenance/
   config/PID/HTTP baselines, budgets and absence of workers/scheduler.
2. New labelled rootless UI container only. No host networking, privileged mode,
   Docker/RootlessKit sockets, public debug port, source credentials or live Bench/
   DB mounts. Readonly root; bounded tmpfs profile/scratch/RAM/CPU/PIDs. Mount only
   vetted tools/minimal clone-only test-access inputs, not site keys/checkpoints.
3. Keep internal-only network, no default route and external DNS blocked. Add a
   fail-closed clone-origin proxy/request allowlist, including WebSockets and
   navigation; deny native/public/provider addresses and unsafe redirects/posts.
   Block service workers, downloads, background sync/networking, unsafe popups,
   WebRTC/non-proxy UDP/QUIC where supported. CSP alone is not the safety boundary.
4. Try normal browser sandbox. No silent --no-sandbox, SYS_ADMIN, privileged mode,
   seccomp-unconfined or global AppArmor/userns changes. If a changed security
   policy is necessary, stop and obtain approval for a narrowly scoped alternative.
5. Prove guard behavior with synthetic external/native requests/unsafe actions
   before loading clone-only credentials. Never reuse a normal operator profile.
6. Prefer existing Node/standard CDP, no npm/pip installs. Keep CDP private. Report
   only booleans/counts/error categories/timings: no DOM dumps, PII screenshots/
   traces/raw console bodies, cookie/password/record-name exports or saved customer
   files. No unrestricted workstation browser session.

## Proposed acceptance matrix

Automated results are evidence, **not self-issued business sign-off**. Permit
required reads and identified nonfinancial clone-fixture writes only. No checkout,
payments, stock/invoice posting/cancellation, payroll/loan disbursement, real
email/SMS/webhook/provider tests. Server egress and workers remain blocked.

| Area | Checks |
|---|---|
| Login/Desk | Real browser login, routing/boot/render/navigation, essential assets/no blocking JS errors |
| Storefront | Homepage, local product/category/search and representative product rendering; no checkout/provider navigation |
| Existing data | Item/Website Item, sales read-only views, HR Employee and lending Loan lists/forms; align counts with baseline |
| Permissions | Guest and minimally privileged synthetic clone user where needed; welcome mail off, no source account/password change |
| Safe write | Open/edit/save only marked nonfinancial Note fixture; confirm persistence, no existing business record edit |
| Attachments | Public images and authenticated/guest private-file behavior without content exports |
| PDF/fonts | Browser-facing synthetic/nonfinancial PDF; native 0.12.6, complex print layout reviewed by operator |
| Realtime | Browser Socket.IO/reconnect/auth behavior; no queue worker execution |
| Containment | External/native/provider requests and unsafe mutations denied; never widen allowlist simply to pass a test |

Synthetic accounts/passwords/fixture IDs stay private in clone scope. No source
users or real integrations are changed. Test mutations never enter final data.
Retain artifacts until exact-list cleanup approval; no blanket prune/removal.

## Final synchronization: separate exact plan before source changes

No final synchronization has executed. Existing source lock approval was one
rehearsal attempt; **not authorization for another production pause**.

1. Operator accepts the UI/business matrix or explicitly reviews blockers.
2. Resolve exact Dalali master/control-plane ownership/aliases/external deployment
   paths. Earlier lookup returned no exact Tenant Site record. Audit read-only;
   do not invent ownership or disable the shared node.
3. Fence only pilot scheduler, queued/in-flight jobs, APIs/provider retries and
   automation. Maintenance alone is insufficient. Never stop shared native worker/
   Redis/Supervisor groups or deserialize untrusted Redis pickles as root. Unknown
   writers block final sync.
4. Fresh DB/files/config snapshot into new private final-production storage with
   reviewed bounded pause/watchdog/consistency and disk/resource budget. No live
   mounts, existing DB/volume overwrite, old archive decryption or promotion of
   the synthetic-test-mutated rehearsal dataset.
5. Frozen-code schema sync in final destination is required (native User field
   remains absent). Preserve rollback; verify versions/schema/data/files/encryption
   and final backup/recovery. Do not migrate native implicitly.
6. Review production frontend/RootlessKit mapping lifecycle, restart/address
   reconciliation, native PDF/font artifact lifetime and one authoritative writer.
7. Obtain exact final-sync scope/window approval. Then **separate pilot route
   switch and write-open approvals**. Native Dalali stays paused until explicitly
   approved; mail/other sites unchanged.

Cleanup only after successful migration/recovery and retention with exact unused
file approval. Active images/volumes/PDF tools, source WIP and required rollback/
backups are not disposable just because UI checks finished.
