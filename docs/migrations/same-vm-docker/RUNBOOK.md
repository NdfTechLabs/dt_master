# Gated same-VM migration runbook

## Execution rules

This is an execution plan. **Operator retains Dalali pilot and permits server-side preparation.**
Do not decrypt the downloaded archive or download further backups. Private
server-side rollback copies are permitted, with no guarantee against whole-VM
loss. Rehearsal should use separately approved server-side copies, not the
downloaded archive. Historical off-server recovery gates below were the original
plan; the changed operator policy is recorded here and in CHANGELOG entry 018.
Never represent the untested archive as an application-verified recovery set.
Destruction, live-data overwrite, old-deployment removal and cutover require
explicit approval. Host installation/security-change steps still need a reviewed
plan and bandwidth estimate. SSH access currently needs Cloudflare reauthentication.
Phase 0 completed; the first checkpoint stopped before database locks or dump.
The first expanded dialogue was unanswered; the post-health-review dialogue
explicitly approved the refreshed reference and mixed scope. All other production
changes remain gated. A gate is closed until its
evidence and operator approval are recorded in CHANGELOG.md. Planning approval
does not imply permission to install packages, stop jobs or switch production.

Every executed step must record: time, operator, exact command/script revision,
target host/site/project, expected result, actual result, non-secret artifact
location/checksum, health checks, and any deviation/rollback. Never record secrets.
Recheck inventory immediately before a change; these documents are not a live lock.

### Hard prohibitions

- No major-version change, `bench update`, unreviewed `bench migrate`, Git reset,
  Git clean, app upgrade or unrelated local WIP deployment on the native Bench.
- No shared Supervisor stop/restart to migrate only Dalali.
- No production site/database/Redis/configuration bind mount into Docker.
- No privileged containers, host networking, host Docker socket mounts or public
  database/Redis ports. No direct Internet access for the rehearsal application.
- No rootful Docker daemon fallback without a new, explicit firewall/VPN plan.
- No global disabling of AppArmor user-namespace restrictions.
- No unattended `curl | sh` installers or unverified binaries.
- No `apt upgrade`, `apt autoremove`, global Docker prune or broad data cleanup.
- No removal of the original Bench or any native database during the pilot.

## Phase 0 — discovery and decisions [completed / decisions pending]

Evidence: INVENTORY.md, source-manifest.json, sanitized local audit outputs.
Tools: `audit.py` and `readiness.py`, run over existing SSH using sudo Python on
stdin. They read configuration locally but export only allowlisted non-secret
values, hashes and aggregates. Git optional locks/fsmonitor are disabled; SQL
reads use read-only transactions. Apt was simulated, not executed.

Record user decisions:

- Frappe first; mail remains native.
- Pilot: dalali.ndftechlabs.com.
- Backup destination class: encrypted copy on this workstation, outside Git,
  approved by user. Recipient, offline custody and the bounded pilot DB pause are
  now approved and locally rehearsed. Installation/cutover windows and maximum
  acceptable public outage/RPO remain **not yet agreed**.
- Dalali already has maintenance_mode=1 and returns 503. Preserve that baseline
  as explicitly requested by the user; reopening requires separate approval.

**Gate A:** agree backup/data handling, windows and scope before host changes.

## Phase 1 — recovery checkpoints [ciphertext transfer verified; do not decrypt; server-side copies permitted]

1. Confirm provider snapshot/other sites' and mail's existing recovery coverage.
   A VM snapshot is useful but is not a substitute for a tested logical backup.
2. After approval, create a root-private, dated migration directory **outside**
   the native Bench. No production data/configuration in this Git repository.
3. Preserve existing backup artifacts before invoking anything that might prune
   them. Prefer reviewed logical dump/archive commands; do not assume a Bench
   backup command leaves older backups untouched.
4. Capture pilot DB, public/private files, exact site configuration/encryption
   keys, relevant common configuration, assets/source provenance, file ownership
   and permissions. Check remote storage/symlinks and non-transactional tables;
   do not silently omit them. Initial live backups are preparation checkpoints,
   not proof of a final cross-DB/files consistent cutover snapshot.
5. Preserve native app sources privately, including Git state, tracked changes
   and untracked files. All 21 Bench apps/dependencies need review; the 12 pilot
   apps are the minimum active set, not proof of no undeclared imports. Preserve
   non-pilot dirty worktrees. Backing up source is not permission to publish it.
6. Privately snapshot critical host routing/service/firewall configuration and
   its original hashes. Protect TLS/DKIM/Cloudflare credentials if included.
   Mail is not migrated; its existing recovery coverage must be confirmed.
7. Encrypt before transfer, with the recipient's public key. Private decryption
   keys stay off the VM. Record fingerprints/checksums, not passwords/key values.
8. Transfer to the approved off-server destination, verify checksums, and prove
   decryption/recovery. Same-VM copies alone do not protect against VM/disk loss.

**Gate B:** valid, complete off-server recovery set + source preservation + an
accepted restore-rehearsal plan. Do not install Docker before this gate is met.

## Phase 2 — rootless prerequisites and synthetic isolation [completed/verified]

See ROOTLESS_FOUNDATION_RESULT.md. This does not open later data/image/cutover gates.

Prepare an exact command plan for approval, including rollback, before executing:

1. Recheck SSH, site baseline HTTP codes, native process PIDs, mail listeners,
   VPN routes, firewall policies, free memory/disk and package-manager activity.
2. Refresh package metadata only if approved, then simulate the exact pinned
   prerequisite transaction again. Initial simulation: uidmap, slirp4netns and
   two libraries, no upgrades/removals. Stop if that plan changes. Suppress
   unrelated automatic needrestart actions for this transaction, not globally.
3. Create a dedicated deployment user, home and non-overlapping subordinate
   UID/GID ranges. Do not repurpose the native frappe account or add it to a
   root-equivalent Docker group. Verify that this account has no production
   directory/credential access beyond intentionally transferred copies.
4. Install a reviewed, version-pinned rootless Docker/CLI/Compose distribution
   with verified provenance/checksums under that account. Do not install/start a
   system-wide rootful daemon as an accidental package side effect.
5. Resolve Ubuntu 24.04 AppArmor restrictions with a reviewed, narrowly targeted
   rootlesskit policy if necessary. Do not globally change userns protections.
6. Configure user systemd service/linger and cgroup delegation. Prove daemon UID,
   rootless security options, data root, socket/context and actual cgroup limits.
7. Enforce an approved aggregate CPU/memory/process budget on the dedicated user
   slice, plus service limits and log rotation. Verify effective limits with tiny
   synthetic containers, not an unbounded stress test. Define disk thresholds,
   monitoring/retention; do not pretend an unconfigured hard disk quota exists.
8. Test private container DNS, internal-only backend networking and a temporary
   loopback-only frontend port. Check that routes, VPN, SSH, native sites, mail,
   host forwarding policy and protected config hashes remain as before.

**Gate C:** rootless/cgroups/isolation proven with synthetic data, resource
headroom acceptable, protected services unchanged. If unsupported, stop and
review alternatives—do not quietly switch to rootful Docker.

## Phase 3 — immutable production-matched image [no-data image validated]

See IMAGE_VALIDATION_RESULT.md. Native PDF/font copies are additional readonly
rehearsal runtime artifacts; include their provenance/lifecycle in production plan.

- Original proposal was an off-VM build. The operator separately approved a
  bounded **offline VM-side evaluation build** using the already-cached base,
  private source snapshot and filtered context. See EXACT_RUNTIME_BUILD_PLAN.md
  and CHANGELOG entries 026–028 for the resource/download scope and reviewed
  symlink policy. This is not permission for additional network dependencies,
  customer-data loading or automatic production adoption of an evaluation image.
- Pin the actual source commits/worktree content, Python 3.14.3, Node 24.14.0,
  Frappe 16.53.0, ERPNext 16.52.1 and required app dependency closure. Match the
  database baseline and review pip/npm/OS library and wkhtmltopdf/font differences.
- Do not select a moving branch, run upgrades, reuse the v15 test image, discard
  dirty changes, or substitute this workstation's dt_master WIP.
- Backups must preserve all original files, but image contents must be reviewed:
  no `.git` credentials, `.env`, site data or secrets baked into layers. Supply
  required private runtime configuration via protected configuration/volumes.
- Store the image privately, record its digest/SBOM/dependency manifest, scan it
  and prove app imports/assets/PDF capability before transfer. No unapproved
  publication of private app source to an external registry.

**Gate D:** exact image/source parity documented, digest pinned, secrets excluded,
functional tests successful, VM disk budget rechecked before loading the image.

## Phase 4 — private Dalali restore rehearsal [technical checks passed; business/UI acceptance pending]

Detailed approved scope is in REHEARSAL_PLAN.md; executed results and limitations
in REHEARSAL_RESULT.md. Private data restore, source-schema-gap diagnosis, explicitly
approved clone-only migration, pre-migration rollback recovery and HTTP/restart
checks passed. Native source is unchanged and paused. Do not promote the test-mutated
clone or infer approval for final source sync, public cutover/write-open or cleanup.

1. Use a distinctly named rehearsal Compose project and new private volumes.
   Keep databases and Redis private. Use explicit rootless Docker context/socket
   and verify target labels/data root before every data operation.
2. Restore into a **new container database**, not host MariaDB. Confirm the target
   server identity and network first. Use copied files/configuration only.
3. Replace copied DB/Redis endpoints with this stack's internal services. Keep
   sessions/queues/cache isolated; do not copy or connect to the native Redis.
4. Keep clone scheduler/workers, outgoing email, webhooks, payments, SMS and
   master/tenant automation disabled. Block backend runtime egress. Production
   keys are not permission to perform live external operations from a clone.
5. Preserve the original encryption key to verify encrypted fields; never log
   decrypted values. Use clone-only operator credentials and documented staging
   URL/config overlays; do not modify the source site or production accounts.
6. Access the clone through an SSH tunnel/private staging hostname. Constrain
   browser requests too: server egress restrictions do not prevent JavaScript
   in an operator's browser from contacting real payment/provider endpoints.
   Never test live charges/messages. Use isolated/allowlisted browser tooling or
   approved provider sandbox credentials for any integration test.
7. Validate required apps, login/permissions, representative existing records,
   public/private attachments, encryption, ecommerce/search/recommendations,
   HR/lending views, PDF/font output, assets, websocket auth and negative access.
   Run disposable test writes only in the rehearsal copy. Do not publish PII in
   screenshots/test reports or mix test data back into the final production copy.
8. Rehearse backup/restore, restart recovery, resource-pressure behavior and
   rollback. Record actual timings to define a realistic maintenance window.

**Gate E:** operator/business acceptance + recovery rehearsal + measured limits
and timings + final quiescence/rollback procedure approved. No public cutover yet.

## Phase 5 — pilot-only writer fencing and final sync [not started]

- Confirm control-plane ownership first. The master lookup did not return an
  exact Dalali Tenant Site record. Review aliases/external automation rather than
  inventing a mapping. Other sites share the existing native Infrastructure Node.
- Fence only Dalali's native scheduler, background/long-running jobs, APIs,
  external writers and deployment automation. Shared worker processes stay up.
- Per-site maintenance alone is not proof that workers are stopped. Prove no
  in-flight/queued pilot jobs or provider retries can write after the snapshot.
  Do not blindly deserialize Redis pickles in a privileged audit process.
- Any native master execution still targeting `/home/frappe/bench` for the pilot
  must be blocked/rebound before cutover. A container transport adapter needs
  tests; otherwise fence that site's management actions explicitly. Do not
  disable access on the shared node and inadvertently block other sites.
- Take a fresh, consistent final logical DB/files/config backup after confirmed
  quiescence. Encrypt/copy/check it. Restore final data into the new production
  stack; discard rehearsal writes and clone-only configuration changes that must
  not enter production. Reapply only the documented necessary container overlays.
- Keep new writers disabled and both source/final copies fenced. Check version,
  data/file parity, grants and encryption before progressing.

**Gate F:** explicit approval for the measured cutover window, verified final
snapshot, no old writer path, new stack validated in read-only/maintenance mode.

## Phase 6 — one-site route switch and write-open decision [not started]

1. Preserve the original Dalali Nginx file privately and record its checksum.
2. Modify **only** its HTTP/HTTPS routing. Send web/API, `/assets`, public/private
   files and Socket.io to the new loopback container frontend. Do not leave native
   aliases serving old files/assets or change the other virtual hosts/certificates.
3. Preserve Host, original HTTPS scheme/port, origin, real-client/proxy handling,
   cookies, upload/timeouts and websocket upgrade semantics. Do not trust arbitrary
   client forwarded headers. Do not expose a backend bypass around private files.
4. Validate the whole host Nginx configuration, then gracefully reload only after
   successful validation. Recheck the three non-pilot site baselines, unrelated
   PM2 sites, mail, WireGuard and SSH immediately.
5. Test the pilot through its real TLS endpoint, including assets/uploads/private
   file authorization and realtime events. Route switching is not permission to
   clear its pre-existing maintenance flag or enable financial side effects.
6. Only with explicit write-open/business approval, enable exactly one pilot
   scheduler/worker deployment, the approved integrations and normal traffic.
   Keep all old pilot writer/automation paths fenced.

**Gate G:** successful route checks, business acceptance and explicit declaration
of the single authoritative data copy. Record the write-open timestamp.

## Rollback

### Before write-open

Keep the original native data untouched/fenced. Disable/stop new pilot writers,
restore the saved Dalali Nginx route, validate/reload, restore the original
site-specific scheduler/automation configuration, and verify the baseline state.
Do not enable maintenance-disabled sites without separate approval. Confirm that
other sites/mail/VPN stayed intact. Retain failed container artifacts for diagnosis.

### After writes have been accepted in containers

**Do not simply route traffic back.** That would lose new records, uploads,
payments or job effects and risk double execution.

Freeze new writes/jobs/integrations first; capture and verify the authoritative
container DB/files/encryption configuration and reconcile irreversible external
effects. Approve either a compatible reverse restore into the fenced native pilot
or a forward repair. Validate data consistency and then switch the single writer.
No destructive reverse restore, loss of acknowledged writes or replay of payments
is implied by permission to migrate.

### Rootless staging/prerequisite rollback

Stop only the dedicated user service/project, preserve required data/reports,
disable only newly created linger/unit/profile changes after verifying nothing
uses them, and compare protected routes/config/services with the baseline. Never
restore the host's entire firewall wholesale over legitimate intervening changes.
No blanket package autoremove, user-home deletion or global volume prune.

## Phase 7 — observation and expansion [not started]

Agree a soak period and thresholds for latency, errors, queue age, job execution,
memory/swap, CPU, IO/disk, mail delivery and VPN/SSH reachability. Verify a fresh
off-server container backup and recovery again. Keep old pilot recovery data.

Migrate remaining sites individually using their own app/data/dependency plans.
Review dirty non-pilot sources, cross-site integrations and master automation
before each move. Do not retire the shared native Bench until its last site and
all jobs/dependencies are migrated and a separate decommission approval is given.
Mail migration, if later requested, is a separate project/window.

## References

- https://docs.docker.com/engine/security/rootless/
- https://docs.docker.com/engine/security/rootless/troubleshoot/
- https://docs.docker.com/engine/network/packet-filtering-firewalls/

These references inform the design; examples from them are not approval to apply
system changes verbatim to this production VM.
