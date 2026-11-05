# Same-VM Frappe → Docker migration

## Status and agreed scope

**Isolated technical restore rehearsal passed; production not cut over.**
See REHEARSAL_RESULT.md for verified restore, crypto/PDF, HTTP/permissions/assets,
polling/WebSocket, pre-migration clone recovery and restart results. Native Dalali
remains maintenance=1; other sites/mail and protected native services unchanged.
Native and clone initially lacked User.send_read_receipt expected by frozen code.
Separately approved clone-only bench migrate fixed the clone; native schema remains
unchanged. Business/UI acceptance, automation ownership/fencing, fresh final sync
and public cutover/write-open remain separately gated. No cleanup performed.
No native/live overwrite, service reconfiguration, public route switch or deletion
is authorized by completion of the private rehearsal.

Operator requested business/UI acceptance, then final synchronization and
separately approved cutover. Approved VM-only browser tools, narrow browser-only
sandbox policy and clone-only CSP compatibility change are in place. Real login,
six list-data responses, four existing-form renders, marked Note save/reload and
guest denial succeeded. **UI acceptance remains blocked**, including storefront
recommendations and outstanding guarded calls/coverage. See UI_ACCEPTANCE_RESULT.md
for precise limits and UI_ACCEPTANCE_PLAN.md for final-sync gates. No source writer
change, final-sync pause, public switch or cleanup has occurred.

Operator subsequently requested `d-stage.ndftechlabs.com` for restricted Dalali
review, initially one explicitly named reviewer, and approved clone pages/data in
authorized reviewers' browsers. STAGING_REVIEW_PLAN.md records the read-only audit,
Cloudflare Access setup needed and proposed separate guarded staging tunnel.
Exact guarded staging scope was subsequently approved. Operator corrected the
staging DNS, confirmed signed-in readiness and requested clone connection.
**Guest homepage preview is now connected** through the guarded Access/tunnel
route; existing clone has read-only HTTP maintenance enabled. Login/Desk/cart,
private files, unreviewed routes and business writes remain blocked. No new site
or database copy; native services/production unchanged. See
STAGING_GUEST_REVIEW_RESULT.md; reviewer browser homepage confirmation is pending.
The server finalized an encrypted preparation checkpoint; its receipt reports a
29.596-second pilot lock, released before encryption/transfer. The operator paused
the download for cost, then explicitly approved finishing only the remaining
364,817,074 bytes. Server/full-file and local-prefix hashes were checked before
resuming. The final local file is 5,764,229,810 bytes, mode 600, with full SHA-256
matching the receipt. That ciphertext was not decrypted or application-restored;
the later rehearsal used a separately approved fresh VM-side copy. The operator directed **do not
decrypt this archive** and **no more backup downloads**. Keep the downloaded
ciphertext untouched. No new dump/lock was taken for resumption.

The operator confirmed keeping Dalali as pilot and allowed private server-side
rollback copies. These copies do not protect against loss of the whole VM; no
claim of a verified off-server application recovery is made. No deletion is
approved: ask first before dropping sites/databases, overwriting live data or
removing old deployments/volumes. Live cutover remains a separate approval gate.

Rootless foundation is installed/verified with aggregate limits and synthetic
isolation tests (ROOTLESS_FOUNDATION_RESULT.md). Vendor base app versions matched
but Python/Node patches differed; operator selected exact native runtimes. Private
21-app/WIP snapshot and filtered sources were used, with staged symlink mappings
and exact-file review of 51 shipped delimiter/example/fixture scanner findings.
The original large export hit its disk guard; a retained image was verified and
a separately approved small native-Yarn layer produced validated dalali-v2
(IMAGE_VALIDATION_RESULT.md). No source/WIP reset or private registry publication.

Full rehearsal was then separately approved: up to 180 MB direct-VM downloads,
8 GiB additional disk/60 GiB free floor, unchanged aggregate 2 CPU/3 GiB and one
fresh pilot-only 55-second-max pause (actual 44.699 seconds). Technical tests passed
using new labelled private storage; original encrypted archive stayed untouched.
No site was promoted or reopened publicly, no old image/volume/file was deleted.
See REHEARSAL_PLAN.md, REHEARSAL_RESULT.md and CHANGELOG entries 041–058.

Read-only health review is in HEALTH_REVIEW_20261025.md, with missing-baseline
limits. The refreshed reference/mixed lock scope was approved for this checkpoint
only. Foundation installation was separately approved/completed; later production
service/configuration changes and cutover remain gated.

- Server access: existing `ssh.ndftechlabs.com` SSH alias, user `frank`, through
  Cloudflare Access. Observed server hostname: `mail.ndftechlabs.com`.
- User-selected scope: **Frappe first**, not mail.
- User-selected pilot: **dalali.ndftechlabs.com**.
- Native source Bench: `/home/frappe/bench`.
- Plan: stage a version-matched, isolated container replacement on this VM;
  cut over only Dalali after verified recovery and explicit approval.
- Keep the other three sites and their native services running throughout.

This folder is the migration record, not a one-command production installer.
The Docker v15 integration sandbox elsewhere in this repository is **not** the
production image and must not be deployed as Dalali's replacement.

## Documents

| File | Purpose |
|---|---|
| [INVENTORY.md](INVENTORY.md) | Sanitized facts and migration blockers |
| [RUNBOOK.md](RUNBOOK.md) | Staged execution, approval gates, acceptance, rollback |
| [CHANGELOG.md](CHANGELOG.md) | Decisions and actual actions, including what was not changed |
| [HEALTH_REVIEW_20261025.md](HEALTH_REVIEW_20261025.md) | Authorized health review, comparison limits and pending decision |
| [KEY_RECOVERY.md](KEY_RECOVERY.md) | Operator-controlled key generation, offline custody and exported-key recovery test |
| [CHECKPOINT.md](CHECKPOINT.md) | Approved bounded database pause, encrypted artifact, transfer and verification procedure |
| [ROOTLESS_INSTALL_PLAN.md](ROOTLESS_INSTALL_PLAN.md) | Exact foundation-only host changes, isolation checks and VM-side download budget |
| [ROOTLESS_FOUNDATION_RESULT.md](ROOTLESS_FOUNDATION_RESULT.md) | Verified installation, resource/isolation tests, operator commands and next gate |
| [BASE_IMAGE_REVIEW.md](BASE_IMAGE_REVIEW.md) | Downloaded candidate findings: matching app versions, newer Python/Node patches, no data loaded |
| [EXACT_RUNTIME_BUILD_PLAN.md](EXACT_RUNTIME_BUILD_PLAN.md) | Native-runtime probe, filtered-copy/draft recipe, Buildx download and offline build approval scope |
| [IMAGE_VALIDATION_RESULT.md](IMAGE_VALIDATION_RESULT.md) | Validated corrected image ID, exact-runtime/package/import results and remaining rehearsal gates |
| [REHEARSAL_PLAN.md](REHEARSAL_PLAN.md) | Private restore scope, exact new budgets/pilot pause, isolation, acceptance and no-cleanup policy |
| [REHEARSAL_RESULT.md](REHEARSAL_RESULT.md) | Technical rehearsal passed, precise recovery/schema scope, budgets, evidence and remaining production gates |
| [UI_ACCEPTANCE_PLAN.md](UI_ACCEPTANCE_PLAN.md) | Browser containment, UI/business matrix and separately gated final synchronization/cutover |
| [UI_ACCEPTANCE_RESULT.md](UI_ACCEPTANCE_RESULT.md) | Partial real-browser evidence, outstanding blockers/coverage; business acceptance not passed |
| [UI_CSP_DECISION.md](UI_CSP_DECISION.md) | Separately approved/applied private clone template-CSP compatibility change |
| [STAGING_REVIEW_PLAN.md](STAGING_REVIEW_PLAN.md) | Requested restricted d-stage URL, account-side Access setup and guarded publication gates |
| [STAGING_DEPLOYMENT_PROPOSAL.md](STAGING_DEPLOYMENT_PROPOSAL.md) | Approved separate connector/gateway scope; readiness portion deployed, public/clone gates remain |
| [STAGING_READINESS_RESULT.md](STAGING_READINESS_RESULT.md) | Approved private readiness services, no-data proof, hidden credential entry and remaining public/clone gates |
| [STAGING_CONNECTOR_RESULT.md](STAGING_CONNECTOR_RESULT.md) | New connector healthy, actual protected route verified; readiness-only browser login pending |
| [STAGING_GUEST_REVIEW_RESULT.md](STAGING_GUEST_REVIEW_RESULT.md) | Existing clone guest homepage connected, write fences/proofs, guarded restart recovery and remaining scope |
| [NAMED_STAGE_SITE_PLAN.md](NAMED_STAGE_SITE_PLAN.md) | Requested isolated d-stage site/data copy; new-target preservation, disk accounting and clone-only snapshot proposal |
| [FINAL_SYNC_PROPOSAL.md](FINAL_SYNC_PROPOSAL.md) | Exact final-sync writer fencing, snapshot, schema sync, rollback, cutover/write-open proposal — not executed |
| [ui-browser.json](ui-browser.json) | Pinned approved/downloaded browser/support artifacts and unchanged budgets |
| [rehearsal-images.json](rehearsal-images.json) | Approved pinned MariaDB/Redis images and 180-MB transfer cap |
| [build-tool.json](build-tool.json) | Signed Buildx metadata; approved plugin installed |
| [rootless-packages.json](rootless-packages.json) | Pinned package versions/checksums verified from signed Docker metadata |
| [source-manifest.json](source-manifest.json) | Exact pilot source revisions and runtime baseline |
| `audit.py` | Reproducible read-only system/source/config inventory |
| `readiness.py` | Read-only workload, control-plane, HTTP and prerequisite checks |
| `verify_unchanged.py` | Pre-change configuration/service comparison; never repairs differences |
| `checkpoint.py` / `collect_checkpoint.py` | Approved bounded pilot DB dump, encrypted checkpoint and verified ciphertext transfer |
| `resume_checkpoint.py` | Verified local-prefix/server identity checks and suffix-only ciphertext resumption; no database operations |
| `repository_metadata.py` | Public signed repository/index validation and package sizes; no binary downloads |
| `apply_rootless.py` / `rootless_foundation.py` | Explicit, journaled foundation-only setup; dry-run by default |
| `prepare_base_image.py` | Approved VM-only pinned image pull and isolated runtime/source inspection; no customer data |
| `probe_native_runtime.py` | Offline compatibility test using private runtime copies, never live production mounts |
| `rehearsal_preflight.py` / `rehearsal_image_metadata.py` | Read-only source sizing, native readiness and small public registry metadata; no dump/lock/pull |

Raw audit output is kept outside the repository in approved local temporary
storage. Only sanitized summaries and non-secret source revision data belong in
these documents. Never commit production dumps, configuration contents,
passwords, encryption/DKIM/SSH keys, tokens, `.env` files or customer records.

## Protected production components

Do not replace/reconfigure/restart these during Docker staging:

- Native Bench, MariaDB, native Redis and Supervisor groups.
- Nginx public ports 80/443, existing certificates and renewal configuration.
- Postfix, Dovecot, Rspamd, OpenDKIM and mail databases/mailboxes.
- Cloudflare Access/SSH, WireGuard, Fail2Ban and monitoring.
- PM2 applications and unrelated Nginx sites on ports 3000/4000.

The eventual approved cutover may gracefully reload host Nginx after changing
**only the pilot's route**. Host Nginx stays the TLS edge; it must proxy the
pilot's assets, files and websocket traffic as well as web/API requests to the
new container frontend.

Never run the generic `install_deps`, `prepare_production`, `stop_project`,
`bench update`, `remove_bench` or site-destruction scripts against this native
production Bench as part of the migration.

## Current isolation layout and remaining proposal

A **dedicated rootless Docker user/daemon** is installed and verified. Synthetic
network/cgroup checks passed without rootful firewall changes. This does not
authorize real site loading or traffic cutover.

Proposed ownership/layout:

```text
dedicated user: dt-containers       (created, UID/GID 5001)
/home/dt-containers/stacks/dalali/   (does not exist yet)
project: ndf-dalali-prod            (not created)
frontend: 127.0.0.1:18088            (candidate only; recheck before use)
database/Redis: private Docker network, no published ports
```

Do not reuse `/home/frappe/bench`, its Redis, its database instance or its files
as live container mounts. Restore **copies** into new, private volumes. Docker
socket mounts, privileged containers and host networking are prohibited.

Enforced staging budget: at most 2 CPU equivalents and 3 GiB aggregate memory,
covering daemon and containers under the dedicated user slice. The earlier off-VM
build proposal is superseded for review by an offline VM-side evaluation build
in EXACT_RUNTIME_BUILD_PLAN.md; that build is not approved yet. Disk monitoring
is required; no hard disk quota is currently proven.
Containers share the VM kernel and physical resources, so this is risk reduction,
not VM-grade isolation or a guarantee of no production impact.

## Decisions and remaining gates before host changes

1. User approved the existing **encrypted backup on this workstation, outside Git**.
   Private destination: `/home/franklyne/backups/ndf-migration`. The public
   recipient was verified and the operator confirmed recovery testing/offline
   custody. A bounded pilot-only database pause was explicitly approved; see the
   change log and CHECKPOINT.md. The initial attempt aborted on unclassified
   sequence objects. The first expanded dialogue was interrupted, not approved;
   the post-health-review dialogue was explicitly approved for backup only.
   Transfer integrity is verified. Archive decryption is now explicitly prohibited
   by the operator; use separately approved server-side copies for rehearsal and
   do not download more backups.
2. User chose to **keep Dalali paused** through staging/cutover. Reopening requires
   separate approval; do not clear the existing maintenance flag automatically.
3. Approved window for installing rootless prerequisites/creating its user and
   a separate window/acceptance gate for final sync and traffic cutover.
4. Pilot control-plane ownership: the master did not return a Tenant Site with
   an exact matching Dalali FQDN. Confirm aliases/external automation before any
   migration-related changes to master records.

Backups, recovery rehearsal, image parity, resource limits, writer isolation and
rollback must be verified first. No rootful fallback or traffic switch is implied
by approval to investigate rootless Docker.
