# Migration decision and execution log

## Entry 001 — scope approved by user

- Same VM, not a separate machine; careful staged migration with documentation.
- Frappe first; keep mail/native edge infrastructure outside the first migration.
- Pilot selected by user: dalali.ndftechlabs.com.
- Host modifications and cutover have **not** been approved under a concrete
  execution window/backup plan yet. This entry authorizes planning/discovery.

## Entry 002 — read-only audit completed

Reference observation: `2026-10-24T23:21:31Z`, recorded by the server/audit.

Executed locally, with Python sent over stdin; no script installed remotely:

```sh
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=30 \
  ssh.ndftechlabs.com 'sudo -n python3 -' \
  < docs/migrations/same-vm-docker/audit.py \
  > /tmp/opencode/ndf-production-migration-audit.json

ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=30 \
  ssh.ndftechlabs.com 'sudo -n python3 -' \
  < docs/migrations/same-vm-docker/readiness.py \
  > /tmp/opencode/ndf-production-migration-readiness.json
```

Results summarized in INVENTORY.md; source pins in source-manifest.json. Earlier
supplementary checks read cgroup, AppArmor, executable and UID-range metadata.

Key findings:

- Production is Frappe/ERPNext v16, Python 3.14.3, Node 24.14.0—not the v15 sandbox.
- Four sites share native workers/scheduler; stopping those globally is unsafe.
- Dalali and Dulaman were already in maintenance/503; demo and ERP returned 200.
- Dalali has 12 installed apps; 21 app source directories exist overall.
- Non-pilot dirty worktrees must be preserved.
- WireGuard forwarding is active; rootful Docker firewall side effects need care.
- Rootless prerequisites are missing and AppArmor restricts user namespaces.
- Apt was **simulated only**: four new prerequisites, no upgrade/removal in the
  cached plan. No apt update, installation, upgrade or autoremove was executed.
- No exact Dalali FQDN master Tenant Site record was returned by the scoped lookup.

Production effects: read-only commands, SQL SELECT-only transactions and ordinary
HTTP ping requests. No Docker/user/profile installation, service restart, Nginx
reload, site mode change, source update, migration, backup/dump, traffic switch
or credential transfer was performed. Ordinary service/access logs may reflect
the inspection requests.

## Entry 003 — proposed architecture / open approvals

- Prefer dedicated rootless Docker; feasibility/resource isolation still unproven.
- Retain host Nginx/TLS/mail/native Bench, stage copied data in new private volumes.
- Build matching images off the VM; keep local application WIP out of migration.
- Approval gates, recovery and single-writer cutover/rollback are in RUNBOOK.md.
- Await backup destination/key custody, intended Dalali maintenance state,
  installation/cutover windows, and control-plane ownership confirmation.

Append future entries rather than rewriting this history to imply that planned
commands have already run. Record failures and deviations as well as successes.

## Entry 004 — read-only follow-up verification

- Compared all inventoried Nginx, Supervisor, common/site configuration and local
  Cloudflared configuration SHA-256 fingerprints to the initial audit: unchanged.
- All 13 checked protected service units were active; WireGuard remained present;
  Docker was still not found on the root execution path.
- The reusable check also confirmed all seven native Bench processes RUNNING,
  unchanged stable IPv4/IPv6 route signatures and unchanged forwarding policy.
- Added a reusable `verify_unchanged.py` check. It verifies the expected hostname,
  hashes and service/process state and fails on differences without repairing them.
- Added local-only tests of source-manifest pins, audit import behavior,
  credential/URL redaction, SELECT-only DB metadata and AST-only version reading.
- Local suite: 72 tests passed. All 12 manifest revisions matched the live
  read-only audit; syntax and whitespace checks passed.

Reproducible pre-change check (read-only):

```sh
python3 docs/migrations/same-vm-docker/verify_unchanged.py \
  /tmp/opencode/ndf-production-migration-audit.json
```

After any approved intentional configuration change, retain the old baseline and
record the approved delta/new fingerprint. Never automatically accept or revert
unexplained differences.

## Entry 005 — backup and maintenance decisions

- User approved an encrypted local-workstation recovery copy, outside Git.
- User chose to keep Dalali paused; do not reopen it automatically after cutover.
- GPG is available on the workstation. No key was selected, generated, exported
  or transferred; no production backup has been collected or downloaded yet.
- Await public encryption recipient and private recovery-key custody confirmation.
  Never request a private key or passphrase in chat.
- Installation/cutover windows and exact host-change commands remain approval gates.

## Entry 006 — secure key-generation instructions requested

- User requested instructions, not automated key generation.
- Added KEY_RECOVERY.md: isolated local keyring, interactive strong-passphrase
  generation, public recipient verification, private offline exports, fresh-keyring
  decryption test and narrowly scoped temporary-keyring cleanup.
- Only the public fingerprint/file and recovery-test confirmation should be shared.
- No keys generated/read/exported, production backups collected, or VM changes made.

## Entry 007 — operator-generated recovery key

- Operator reported public fingerprint:
  `E220A389F4E9105E6465C292F4708B832A07005F`.
- Operator reported `RECOVERY TEST PASSED` using a fresh verification keyring.
- Metadata-only local checks confirmed the private export at
  `/home/franklyne/ndf-migration-key-exports/recovery-private.asc` (6,776 bytes,
  mode 600) and public export alongside it (3,155 bytes, mode 600).
- Private-key contents were not opened or transferred by the agent. The temporary
  verification keyring is separate from the exported recovery file.
- Offline recovery-copy/passphrase custody confirmation remains pending.
- No production backup collection, VM installation or service changes performed.

## Entry 008 — recovery custody and bounded checkpoint approval

- Operator confirmed offline recovery material was copied and requested continuation.
- Independently inspected only the public export: approved primary fingerprint
  and encryption subkey present. Private-key contents/passphrase were not read.
- Production preflight found 1,007 InnoDB tables and three MyISAM tables:
  tabData Import Log, tabError Log, __global_search. No triggers/routines/events
  or remote File URL rows were returned by the scoped metadata checks.
- User explicitly approved a pilot-only bounded write pause for the DB dump:
  acquire deadline 5 seconds, hold budget below 60 seconds, release on failure;
  no global MariaDB lock, service stop, table-engine conversion or traffic change.
- Implemented checkpoint.py / collect_checkpoint.py with a 55-second watchdog,
  60-second session-idle fallback, selected-schema READ locks, ciphertext-only
  transfer, pinned recipient/source/config checks and outside-repository output.
- Local Docker rehearsal passed: MyISAM and InnoDB writers blocked, unrelated DB
  writable, normal/watchdog/failure release verified, scoped-user dump and gzip/
  table-definition validation passed. Synthetic databases/user were removed.
- Local suite: 78 tests passed. Dry run: 1,010 tables, 21 app source trees;
  encrypted output bounded to 8 GiB. No production lock had been taken at dry run.

## Entry 009 — preparation checkpoint collection initiated

Approved destination: `/home/franklyne/backups/ndf-migration` (private, outside Git).
Production staging: newly allocated root-private child of
`/var/backups/ndf-migration`; never inside the native Bench.

Collection command (the only public key provided to the VM):

```sh
python3 docs/migrations/same-vm-docker/collect_checkpoint.py \
  --audit /tmp/opencode/ndf-production-migration-audit.json \
  --public-key /home/franklyne/ndf-migration-key-exports/recipient-public.asc \
  --destination /home/franklyne/backups/ndf-migration --execute
```

This is a **preparation checkpoint**, not the final atomic DB/files cutover sync.
The database is dumped under selected-schema READ locks; those are released
before encryption/source/file capture and transfer. File/source capture uses
low-priority processes, preserves installed app dependencies/worktrees, and
excludes disposable Python bytecode caches. No host mailboxes or home SSH/DKIM
directories are selected. Source/configuration can contain private application
secrets and is enclosed in the encrypted archive.

Server-side temporary SQL/log staging is root-private; after successful archive
finalization it is removed. Ciphertext, public recipient, receipt and diagnostic
metadata remain in the new root-private checkpoint directory. An independent
SHA-256/size match is required after transfer; decryption/recovery is not implied
by transfer success. Record actual outcome in the next entry, including failures.

No Docker installation, service restart, Nginx reload, site-mode change,
production migration or traffic cutover is authorized by this checkpoint step.

## Entry 010 — initial checkpoint aborted before locking/dump

- Collection returned `Table scope changed or includes unreviewed views` at the
  object-type guard, before entering the locking/dump routine.
- A new root-private checkpoint staging/public-keyring directory was created and
  the public recipient imported. No DB lock, logical dump, completed encrypted
  archive or transfer resulted from this attempt. Do not mark gate B complete.
- Follow-up SELECT-only classification found 1,000 base tables (997 InnoDB,
  3 MyISAM) and 10 InnoDB sequence objects. Earlier engine-only totals counted
  sequences among the 1,007 InnoDB objects; they were not all ordinary tables.

## Entry 011 — sequence handling rehearsed locally; approval not received

- Local-only testing demonstrated that READ locks do not fence cached sequence
  allocation. The proposed mixed scope is READ locks on 1,000 base tables and
  exclusive locks on the 10 sequence objects, still confined to Dalali.
- A local synthetic rehearsal verified allocation fencing, unaffected unrelated
  schema writes, dump/restore of sequence definitions/cache boundaries, and normal,
  watchdog and failure release. Those local fixtures were removed.
- The expanded approval question was interrupted and **not answered**. No
  production retry/exclusive sequence lock is approved or was executed.

## Entry 012 — operator pause / status review

- Operator reported a server restart and asked to pause and review status.
  Which server restarted and the production VM's current state have not been
  independently re-established; no production restart command was issued by us.
- All further production work is paused. No assumption that previous inventory,
  process state or service health is still current after the reported restart.
- Status review used saved local documents only, with no SSH/VM commands. Updated
  stale "in progress" wording to reflect the failed pre-lock attempt and pause.
- Before resumption: obtain approval for read-only health checks, review baseline
  differences without auto-repair/rebaselining, then revisit the unanswered lock
  scope. Docker installation and traffic cutover remain separately gated.

## Entry 013 — authorized read-only post-restart review

- Operator authorized read-only health checks, then review of the unanswered
  approval. No backup retry/lock/installation/repair was authorized by that request.
- At Oct 25 approximately 10:54 UTC, the VM reported boot Oct 18 and 6 days 20
  hours uptime; native Bench PIDs matched prior records. No recent production VM
  reboot was evidenced. All seven Bench processes and 13 protected units active.
- Site HTTP/maintenance responses matched the earlier observations. WireGuard
  route remained present; FORWARD policy ACCEPT. The wait-online unit's recorded
  failure dates to Oct 23; no repair attempted.
- The complete original temporary audit is missing. Verified 14 fingerprints
  preserved in earlier output, with no changes, and the 12 pilot source pins.
  Supervisor/common-config original hashes could not be compared; no claim of
  a complete unchanged baseline is made.
- Saved a fresh **review-only** inventory/comparison under the private workstation
  backup healthchecks directory rather than ephemeral /tmp. Not auto-approved as
  a new execution baseline. See HEALTH_REVIEW_20261025.md for exact paths/results.
- Server checkpoint existence checks found no SQL dump, ciphertext archive or
  receipt. No data lock or backup collection was performed during this review.
- Expanded sequence-lock approval remains unanswered/pending. Production mutation
  remains paused; installation/cutover remain separate gates.

## Entry 014 — refreshed checkpoint reference and mixed scope approved

- After reviewing the read-only health results and incomplete original-baseline
  comparison, the operator selected **Approve backup only**.
- Explicitly accepted the refreshed private review inventory as the checkpoint
  reference; this is not a claim that missing historical hashes were recovered.
- Approved READ locks on 1,000 Dalali base tables and exclusive locks on its 10
  sequences, after rerunning the local rehearsal. Acquisition 5 seconds, hold
  watchdog 55 seconds, server idle fallback 60 seconds. No global DB lock.
- Approval covers encrypted preparation collection/transfer only, after lock
  release. Keep maintenance enabled. No Docker installation, services/configuration
  changes or traffic cutover. Local rehearsal result/retry outcome to follow.

## Entry 015 — rehearsal repeated and backup-only retry initiated

- Local sandbox database restarted for rehearsal only; no production services
  touched. An unsupported local Compose `run --no-build` flag was rejected before
  the test ran; corrected to use the existing image with `--pull never`.
- The repeated synthetic rehearsal passed MyISAM/InnoDB writer fencing, sequence
  allocation fencing and restore, unrelated-schema writes, normal/watchdog/failure
  release, and real dump validation. Disposable test schemas/account were removed.
- Local unit suite: 78 tests passed; whitespace checks passed.
- Dry run against the accepted persistent inventory confirmed 1,000 base tables,
  10 sequences, 21 app trees, 5-second acquisition/55-second hold watchdogs and
  the 8-GiB ciphertext cap. No lock is taken by the dry run.
- Production retry is limited to the approved preparation checkpoint. Outcome
  remains pending until receipt, ciphertext checksum/size and post-checks pass.

Retry command:

```sh
python3 docs/migrations/same-vm-docker/collect_checkpoint.py \
  --audit /home/franklyne/backups/ndf-migration/healthchecks/review-inventory-20261025T105537Z.json \
  --public-key /home/franklyne/ndf-migration-key-exports/recipient-public.asc \
  --destination /home/franklyne/backups/ndf-migration --execute
```

## Entry 016 — server artifact finalized; transfer paused by operator

- Operator requested an immediate pause because data transfer was becoming costly.
- Stopped only the local checkpoint collector and its SSH/Cloudflared descendants
  with SIGTERM. No production service/process was stopped. No additional SSH check
  was made after the pause; remaining evidence below is from the saved local receipt.
- Receipt: `20261025T110703Z-dalali-56_si6yk.receipt.json` in the private workstation
  backup directory. Server artifact finalized at:
  `/var/backups/ndf-migration/20261025T110703Z-dalali-56_si6yk/checkpoint.tar.gz.gpg`.
- Receipt reports: 1,000 base tables + 10 sequences dumped/validated, SQL gzip CRC
  passed, lock held 29.596 seconds and released before archive/encryption/transfer.
- Full encrypted artifact: 5,764,229,810 bytes. Local partial at stop:
  5,399,412,736 bytes; approximately 364,817,074 bytes remained.
- The partial and receipt are retained outside Git. No complete local SHA-256 match,
  full-archive decryption or application restore verification is claimed. Gate B
  remains incomplete.
- All further transfers/migration work are paused. Do not automatically recollect
  or redownload from zero. The current fetch helper rejects an existing partial;
  a future explicitly authorized resumable procedure must preserve/validate it.
- No Docker installation, maintenance change, service restart or cutover performed.

## Entry 017 — remaining ciphertext explicitly authorized and completed

- Operator requested finishing only the remaining approximately 365 MB.
- Implemented/tested suffix-only resumption. Before appending, checked the server
  artifact's full checksum and its first 5,399,412,736 bytes against the saved
  receipt/local prefix; pinned file identity/size/timestamps for the transfer.
- Downloaded **364,817,074 bytes**, not the whole backup again. Only metadata and
  hashes crossed the network before that suffix; hashing read existing files on
  their respective machines. No database dump, lock or service change occurred.
- Final local artifact:
  `/home/franklyne/backups/ndf-migration/20261025T110703Z-dalali-56_si6yk.tar.gz.gpg`.
- Size: 5,764,229,810 bytes; mode 600. Full SHA-256 matched the server receipt:
  `664d579e90bc913b62494e255517566defaf971ea9f360104755432db1b7d375`.
- The partial was finalized only after full verification, without overwriting an
  existing final file; no partial remains. Private key material was not accessed.
- Local unit suite: 80 tests passed, including remote prefix hashing and suffix
  emission with synthetic data. Resume exited successfully.
- Only encrypted-transfer integrity is verified. Full-archive decryption and
  application recovery remain pending; no automatic further migration/transfer.

## Entry 018 — operator changes backup policy; Dalali remains pilot

- Operator explicitly requested no decryption of the downloaded archive and no
  further backup downloads; requested separate approval before destructive work.
- Clarified scope: operator selected **Keep Dalali as pilot**, not a switch to
  the active sites. No site is authorized for deletion merely because it is disabled.
- Operator permitted private **server-side-only rollback copies**, acknowledging
  they do not protect against whole-VM loss. Keep mail/other sites native and
  Dalali in maintenance until separately approved otherwise.
- The downloaded ciphertext is retained unchanged. No private-key use/decryption
  occurred and no additional backup was collected/downloaded under this directive.
- Began metadata-only download-size checks for rootless Docker planning. The
  workstation HTTPS HEAD request failed with a connection reset before a binary
  download; the attempted VM metadata check did not reach the VM because Cloudflare
  Access requested reauthentication and SSH banner exchange timed out.
- No package/image downloads, Docker installation, new deployment user, service
  changes, data overwrite, site removal or traffic switch occurred in this step.
- Await normal interactive Cloudflare SSH authentication; do not record login
  links/tokens, passwords or private keys in the migration log.

## Entry 019 — access restored; rootless foundation proposal prepared

- SSH access restored as frank; hostname confirmed mail.ndftechlabs.com. Fresh
  checks found all seven native Bench processes and checked web/mail services
  running, maintenance states preserved, VPN/forwarding intact, port 18088 free,
  no Docker/rootless account installed and about 125 GiB disk / 6.6 GiB RAM free.
- No archive decryption, backup downloads, new DB pause or production data copy.
- Metadata-only checks found static Engine/rootless bundle sizes of about 99 MB,
  then a smaller authenticated Debian-package extraction route was evaluated.
- One intermediate SSH Cloudflare app-info request timed out; the next succeeded.
  No login tokens were recorded and no configuration changed.
- Verified Docker public signing fingerprint, InRelease signature and package
  index SHA-256. Selected pinned Engine/CLI/rootless 29.6.1, Compose 5.3.1 and
  compatible containerd 2.4.2 metadata: 81,728,620 package bytes. No binaries fetched.
- Queried public image metadata only: ERPNext v16.52.1 amd64 candidate is about
  981 MB compressed. Digest recorded for planning; parity unverified, pull not approved.
- Added ROOTLESS_INSTALL_PLAN.md and rootless-packages.json. Proposed stage limit
  150 MB direct-to-VM downloads, dedicated user, root-owned pinned binaries,
  targeted AppArmor allowance, 2 CPU / 3 GiB aggregate limit and synthetic tests.
- Exact host-change approval is pending. No packages installed, users/profile/unit
  changes, rootful daemon, image pull, deletion, service restart or cutover performed.

## Entry 020 — foundation-only host changes approved

- Operator selected **Approve foundation only** for the reviewed new-user rootless
  setup. Approval explicitly excludes site restores, real data loading, application
  image builds/pulls, deletion, cutover and reopening Dalali.
- Stage budget: at most 150 MB directly to the VM, including signed Ubuntu metadata/
  prerequisites, 81,728,620 bytes of pinned Docker DEBs and a tiny synthetic image.
  No backup/source download to the workstation and no archive decryption.
- Docker DEBs will be checksum-verified and extracted, not installed as rootful
  services. Four Ubuntu prerequisites only, after fresh signed metadata and a
  strict simulation showing no upgrades/removals or unexpected packages.
- New resources: dt-containers account/home/ranges; root-owned versioned binaries;
  narrow new rootlesskit AppArmor allowance; per-UID delegation/resource drop-ins;
  only the new user's rootless daemon/linger and synthetic tests.
- Rootless foundation scripts/dry-run locally checked: 85 tests passed, package
  hashes/sizes/scope pinned, existing-config overwrite and budget guard tests pass.
- Runtime journal/command logs/snapshots are root-private under
  `/var/lib/ndf-rootless-migration`. Each completed step is recorded for recovery;
  partial state is retained, not blindly deleted or repeated.
- Execution outcome pending. Native service/config/routing checks must pass before
  this foundation is marked complete; no broader migration approval is implied.

Approved execution entrypoint:

```sh
python3 docs/migrations/same-vm-docker/apply_rootless.py --execute
```

## Entry 021 — foundation completed and independently verified

- First execution completed baseline, signed Ubuntu metadata/download and verified
  Docker extraction; APT --no-download then refused private local paths before
  package installation. No account/daemon was created at that failure point.
- Changed only prerequisite installation transport: repeated the four-package
  no-upgrade/no-removal simulation, then offline dpkg-install of the same verified
  files. Cached metadata/packages/steps were reused, not downloaded again.
- Completed 2026-10-25T14:38:06Z. New account UID/GID 5001, only own group; subuid/
  subgid 362144:65536. Rootless Engine 29.6.1, Compose 5.3.1, cgroup v2/systemd,
  overlayfs; daemon process verified owned by UID 5001.
- Effective container/aggregate limits and internal-network/no-default-route
  checks passed with synthetic image only. No customer data or application image.
- Protected before/after snapshot matched. Independent checks found same seven
  native Bench PIDs RUNNING, original HTTP states, VPN route/forwarding preserved,
  and rootful Docker/socket/containerd units not found/inactive.
- Accounted VM-side data 114,297,998 bytes, including conservative tiny-image
  allowance, below 150 MB. No backup download or archive/private-key decryption.
- Exact installed versions, inspection commands and evidence paths are recorded in
  ROOTLESS_FOUNDATION_RESULT.md. Original failure retained in private journal/log.
- Added a completed-bootstrap guard for future executions: never replay bootstrap
  or stop a daemon that may later serve applications just because old native
  configuration changed after approved cutover.
- Foundation gate complete only. No app build/restore, production cutover,
  database/site removal, native service restart or maintenance change performed.

## Entry 022 — pinned base image pull/inspection approved

- Operator confirmed the approximately 981 MB download is directly on the VM,
  then requested continuation. This stage pulls/inspects that candidate only.
- Pinned amd64 reference:
  `frappe/erpnext@sha256:eaf1da1478c2b99339a0a8bc37af723a67ecf9cd4f84b313a0c3765743486fcc`.
- Planned compressed layer payload: 981,159,820 bytes. No backup download or
  dependency installation/build included; progress logs stay on the VM.
- Added prepare_base_image.py (dry-run default). It verifies the intended rootless
  daemon/free space, reuses the image if already present, pulls by digest and
  inspects a temporary read-only/network-none/cap-drop container without host data
  mounts. Runtime/source versions and available Git revisions are compared to the
  production manifest; parity is not assumed from the image tag.
- Local suite: 88 tests passed, including import/no-pull and isolation guard checks.
- No site data, archive decryption, database changes, production routes or existing
  service changes are authorized. Actual pull/parity outcome to follow.

Execution entrypoint:

```sh
python3 docs/migrations/same-vm-docker/prepare_base_image.py --execute
```

## Entry 023 — pinned image cached; runtime mismatch stops further preparation

- Approved pinned base downloaded directly by the VM; digest confirmed. Progress
  log stayed on the VM. No backup or image layers transferred to the workstation.
- Isolated read-only/network-none inspection found Frappe 16.53.0 and ERPNext
  16.52.1 matching production, but Python 3.14.9 and Node 24.22.1 differ from
  native 3.14.3/24.14.0. Image OS Debian 13 versus native Ubuntu 24.04.
- Git revisions unavailable in the vendor image, so exact source parity is not
  proven by matching version strings. No customer data loaded or app build begun.
- No silent runtime upgrade: further build/data preparation stopped for an explicit
  choice. Facts/tradeoffs recorded in BASE_IMAGE_REVIEW.md.
- Native config fingerprints unchanged. Independent checks found original seven
  Bench PIDs RUNNING, checked web/mail services active and roughly 119 GiB free.
- Original image Size receipt label was inaccurate for Docker 29; corrected the
  helper's label for future runs without changing any runtime/configuration.
- The cached image will be reused; do not re-pull it just to repeat inspection.

## Entry 024 — exact native runtime route selected; offline compatibility probes

- Operator chose **Match existing**. No adoption of the vendor image's newer
  Python/Node patches. Prepared private runtime copies only, not an image build.
- Native interpreter resolves to /usr/bin/python3.14 with base_prefix /usr;
  copied the interpreter/stdlib and two envs only, never all /usr. Also copied
  exact Node prefix, scoped native compatibility libraries and MariaDB clients.
- Runtime copies retained root-owned/group-readable under
  /opt/ndf-rootless/native-runtime-probe-v1; ephemeral containers mount only those
  copies read-only, with network none, cap-drop and bounded resources.
- First probe found MySQLdb missing libmysqlclient.so.21 and native Python using
  image OpenSSL. Second probe added four private native libraries and scoped
  LD_LIBRARY_PATH: Python 3.14.3, Node 24.14.0, OpenSSL 3.0.13 and MariaDB clients
  10.11.14 confirmed. All ten tested import targets passed. Gevent absent on native
  too; not installed. Evidence/log paths in EXACT_RUNTIME_BUILD_PLAN.md.
- No downloads, production-directory mounts, source snapshot, site-data load,
  custom build, database operation or cutover in these probes. Framework/source,
  full dependency, PDF/font and asset compatibility remain unproven.

## Entry 025 — Buildx metadata and bounded evaluation-build proposal

- Updated/executed repository_metadata.py on VM: Docker signing key, InRelease
  signature and package-index SHA-256 verified; no binary downloaded. Buildx
  0.38.1 package is 17,800,928 bytes; exact signed checksum/path in build-tool.json.
- Read-only dedicated-account check confirms buildx missing. No builder/plugin
  installation attempted. Plan uses integrated Docker builder, no BuildKit pull.
- Read-only source inventory: all 21 app trees ~8.10 GB, Git metadata ~4.41 GB,
  private runtime copies ~1.69 GB; host free ~126.51 GB. All 12 pilot revisions
  match source-manifest.json; five non-pilot dirty worktrees remain untouched.
- All 21 app .pth files reference /home/frappe/bench. Draft recipe preserves that
  image-internal path and overrides vendor startup. Wrapper scopes old OpenSSL to
  native commands rather than changing global image/host library configuration.
- Added EXACT_RUNTIME_BUILD_PLAN.md and evaluation-only recipe. Separate private
  WIP/Git snapshot versus credential-filtered context; no sites/config/customer
  files, no registry publication. Source/secret/symlink checks must pass first.
- Proposed separate approval: up to 20 MB direct-to-VM plugin download, offline
  evaluation build under existing aggregate 2 CPU / 3 GiB, up to 35 GiB incremental
  disk with 60 GiB host-free floor. Stop on extra downloads/health changes; retain
  partial work, no automatic cleanup. Root copying uses low priority separately.
- No download/build authorization inferred from earlier probe approval. Approval
  remains pending; no real-data copies/restores, deletion, traffic switch or
  clearing maintenance are included in this proposed stage.
- Final local validation: 92 tests pass; git diff --check and wrapper shell syntax
  pass. Fresh VM checks: no changed protected fingerprints, services match the
  foundation baseline, original seven Bench PIDs still RUNNING, original site
  maintenance states, FORWARD policy and WireGuard route preserved.

## Entry 026 — capped Buildx download and offline evaluation build approved

- Operator selected **Approve download and build** for EXACT_RUNTIME_BUILD_PLAN.md:
  Buildx 17.8 MB direct to VM, 20 MB additional transfer cap; private source/WIP
  staging and filtered offline evaluation image, 2 CPU/3 GiB aggregate builder
  budget, up to 35 GiB incremental disk and 60 GiB host-free floor.
- Explicit exclusions remain: real site/customer-data copies/restores, archive
  decryption, backup downloads, deletion, live overwrite, native restarts, public
  cutover or maintenance changes. No dependency/image download beyond the plugin.
- Added explicit dry-run-default build_exact_runtime.py and import-inert remote
  implementation. Private snapshot/context/secret/symlink/drift checks precede
  build; integrated builder and cached base required. No automatic cleanup/retry
  of partial snapshots/context. Build resource/health/disk monitoring cancels only
  our build client on a stop condition, not daemon/native production services.
- Plugin is installed first as a separately journaled step. Actual execution and
  acceptance results pending; approval does not imply build success.

## Entry 027 — plugin/source snapshot complete; context stopped before image build

- Downloaded exactly 17,800,928 bytes directly to VM; SHA-256 matched pinned
  Buildx package. Extracted plugin only, no Docker DEB system-install.
- Initial plugin checks exposed restrictive umask on new parent directories and
  unsupported buildx inspect --format. Corrected only our new paths/inspection;
  cached package reused without redownload. Buildx v0.38.1 and integrated Docker
  driver confirmed. No BuildKit/base/dependency downloads.
- Snapshot of all 21 app trees completed in root-private operational storage.
  Original/snapshot content-mode-symlink manifests matched; pilot revision checks
  passed. WIP and Git state preserved, no native source edits/reset/clean.
- Filtered context preparation stopped at external symlink guard before secret
  scanning, Docker build or image validation. Partial context is retained, not
  approved as build input and not automatically overwritten/deleted.
- Read-only diagnosis: eight app public/node_modules links point to original
  native app dependency directories; stdlib sitecustomize points to an external
  /etc file; libpython config link is dangling; Bench CLI env has another
  interpreter alias. Do not follow those paths into live mounts or silently omit
  them. A reviewed mapping/copy policy is needed before resuming context staging.
- Latest check: protected service states match foundation, original seven Bench
  PIDs RUNNING, Dalali maintenance=1, host free disk 112,836,902,912 bytes (~105 GiB).
  No image was built and no customer data/site config was loaded or restored.
- Local suite before execution: 96 tests pass; dry-run and whitespace checks pass.
  Root-private journal/logs and snapshots retained under
  /var/lib/ndf-rootless-migration/exact-runtime-v1; filtered partial context under
  /opt/ndf-rootless/exact-runtime-v1. No cleanup/deletion or traffic change.

## Entry 028 — operator requests reviewed symlink handling and continuation

- Operator explicitly requested handling the symlink safety stop and continuing.
  Existing 20 MB transfer / 35 GiB incremental disk / 60 GiB free / 2 CPU / 3 GiB
  build scope retained. No real-data restore or traffic approval inferred.
- Read-only inspection confirms native libpython is present: only its private
  runtime copy lacked the link target. Native sitecustomize is a zero-byte regular
  file. The other escaping links are eight absolute app node_modules links and a
  Bench CLI interpreter alias; no arbitrary live mount is required.
- Map absolute app links lexically into the verified private source snapshot and
  then into the filtered context. Reconstruct all interpreter aliases resolving
  to the exact reviewed Python. Copy only native sitecustomize and libpython into
  private support storage with before/after hashes; image links reference those
  image-internal copies. Unknown external or excluded targets still fail closed.
- Retain partial v1 context untouched. Use a new v2 context, reusing the verified
  source snapshot and installed plugin, counting retained attempts in the original
  disk guard. No deletion, backup or dependency download involved.
- Recipe now includes private sitecustomize target; libpython remains in the
  service-scoped compatibility directory, not a host/global-library replacement.
  Context scan and offline image build/validation outcomes pending.

## Entry 029 — operator requests notification before image build; durable hold

- Operator requested notification when preparation is complete **before building
  Docker**. Treat this as a new build hold and require a separate go-ahead.
- Recorded root-private hold-before-build marker and identified the running helper
  by its exact FLOCK ownership, root UID and python3-stdin command. Paused/stopped
  only that preparation helper. No daemon/native process signalled; no deletion.
- At stop: plugin/snapshot complete, v2 context not yet created, no build log or
  image build. Original partial v1 context and private snapshots retained.
- Added --prepare-only: context/link/secret checks may complete, but no build or
  image validation is scheduled. build() also refuses the durable marker even if
  invoked without that flag. Do not release the marker without operator go-ahead.
- Preparation-only continuation will notify success or a safety blocker before
  any Docker build; earlier build approval no longer permits automatic progress.

## Entry 030 — preparation transport dropped; VM journal confirms credential stop

- Preparation-only local SSH exited 255 with client_loop Broken pipe. Initial
  status reconnect timed out; retry reached expected hostname. Did not rerun the
  helper or assume that connection loss meant the VM process had stopped.
- Read-only journal/lock inspection: no active helper lock owner, v2 context and
  symlink/credential receipts exist, no build log, hold-before-build still present.
- All 783 links mapped: eight native-app-to-snapshot links, 766 private-copy links,
  two private Python-support links and seven interpreter aliases. Symlink blocker
  resolved without live mounts or new downloads.
- Credential scan stopped context acceptance on 51 indicators (46 private-key
  markers, five AWS-access-key-shaped strings). Paths include bundled dependency
  code, API documentation and crypto self-tests. No matched contents/secrets were
  displayed; these findings require classification, not blanket suppression.
- No context acceptance/image build, real data restore or traffic switch. Original
  seven Bench PIDs RUNNING, checked Nginx/MariaDB/mail/Cloudflare services active,
  Dalali maintenance=1. Free disk 107,445,944,320 bytes (~100 GiB).
- Report this as a preparation safety blocker, not evidence of a production outage
  or confirmed credential exposure. Retain partial v1/v2 contexts and private
  evidence. Operator notification/go-ahead requirement still applies before build.

## Entry 031 — image readiness rechecked; credential review blocked by SSH access

- Operator asked whether the application image passed and could be mounted.
  Read-only query of the explicit dedicated rootless daemon found no
  ndf-local/exact-runtime image, no build/validation receipts, prepared=false and
  the durable build hold still present. Dalali maintenance=1.
- Read-only finding-path review confirms hits include dependency serialization
  code, API/npm documentation, crypto/Ansible test examples, an Insights test and
  happy-dom HTTPS certificate fixtures. These categories alone are not acceptance
  or justification to disable the scanner; no matched values were displayed.
- Attempted metadata-only classification (installed RECORD checksums and marker
  structure, no credential contents). Initial connection timed out; a subsequent
  read-only connection worked, but classification reconnect was redirected to
  Cloudflare Access login and failed before SSH. Do not record login URLs/tokens
  in this migration record. Review is incomplete; normal SSH reauthentication is
  needed before continuing. Do not infer successful VM metadata-report creation.
- No image run/mount, real-data access, new download, build, hold release or site
  change performed. Keep separate preparation and explicit build/data/cutover gates.

## Entry 032 — SSH restored; credential findings reviewed, exact-hash recheck prepared

- Operator confirmed SSH works and requested finishing review, then building and
  mounting data. Continue review first; maintain pre-build notification/hold.
  No ambiguous live-data mount or production overwrite is inferred.
- Metadata/structural review completed without displaying matched values: all 51
  files classified as delimiters, documentation/examples or distributed fixtures.
  Forty-four Python files match installed RECORD hashes. AWS-shaped values occur
  in docstrings or EXAMPLES/RETURN bindings, not runtime customer configuration.
- Four npm files have placeholder key examples without base64 key material. Two
  Happy DOM 20.9.0 source/compiled modules contain the same bundled HTTPS fixture;
  comments explicitly describe that purpose. Insights test placeholder is not a
  complete key and matches committed test source. Native packages/source unchanged.
- Raw markers remain detectable. Added credential-review.json pinning the entire
  51-file structural review report SHA-256. Acceptance requires exact reviewed
  path, file hash and indicator; new/changed/missing findings stop preparation.
  No whole-directory ignore, scanner disable or secret removal from source.
- Added --prepare-only --review-existing-context: reconstruct expected filtered
  tree from recorded source manifest/private runtimes, verify every file, mode,
  symlink and recipe, then re-scan. This validates retained v2 without overwriting
  or deleting v1/v2. It cannot release build hold, build or mount data.
- Package RECORD consistency is not independent upstream-signature verification
  or a complete security audit. These exceptions concern reviewed shipped
  fixtures/constants only. Final full-context recheck/acceptance pending.

## Entry 033 — preparation-only full-context review passed; build still held

- First review launch hit an SSH connection timeout; retry completed successfully
  (exit 0). Explicit SSH keepalives enabled. No new binary/dependency downloads.
- Full retained v2 context matches expected recorded app snapshot/private runtime
  inputs, normalized modes, all reviewed symlinks, Python support and recipe.
  Re-scan findings match exact reviewed file paths/hashes/indicators; no unresolved
  credential findings. Scanner remains active for any new or changed input.
- Journal completed steps: plugin, snapshot, context. Preparation-only result:
  image=null, customer_data_loaded=false, build_on_hold=true. No Docker build or
  customer-data mount was scheduled. This preparation process has finished.
- Acceptance and final filtered manifest retained root-private on VM as
  credential-review-acceptance-v2.json and filtered-manifest.json under
  /var/lib/ndf-rootless-migration/exact-runtime-v1.
- Latest local suite: 111 tests passed; whitespace/dry-run checks passed. Operator
  asked for completion/current-running status; notify before building. Image
  runtime validation, assets/fonts, private data-copy/restore plan and cutover
  remain later gates; preparation success is not application acceptance.

## Entry 034 — explicit image-build go-ahead; process updates requested

- Operator directed **Build it and also inform me of the process**. This releases
  the pre-build notification hold for this approved evaluation image only. It
  does not authorize customer-data mounts/restores, live overwrites or cutover.
- Fresh VM preflight confirms prepared=true, 51 reviewed/zero unresolved findings,
  unchanged protected fingerprints/services, original seven Bench PIDs RUNNING,
  enforced 2 CPU/3 GiB, Dalali maintenance=1 and ~100 GiB free. Existing incremental
  use ~17.8 GiB leaves the planned 12-GiB build reserve within the 35-GiB cap.
- Added explicit --approve-build: after passed-preparation checks, archive (not
  delete) the old hold and bind approval to the filtered-context manifest hash.
  Rehash context and recheck hold/health/disk immediately before build. Any newly
  reinstated hold blocks progress. No old image tag is overwritten.
- Build uses cached pinned base, integrated builder, --pull=false, network-none
  build steps, local tag/load only and no dependency installs or registry push.
- Strengthened no-data validation: exact Python/shared-lib/OpenSSL/Node/Yarn/
  MariaDB client/Bench versions, all 12 pilot app import paths, Frappe/ERPNext
  versions and frozen application package inventory. Temporary container is
  read-only/network-none/cap-drop, no host or customer mounts. Gaps block readiness.
- Operator updates: preflight, build and validation results; execution pending.
- Local validation before launch: 114 tests passed, dry-run and whitespace checks
  passed. Launched explicit approved build helper in background with SSH keepalives
  and private output log exact-runtime-build-approved-20261025.log outside Git.
  Remote command/build outcomes still pending; no success assumed from launch.

## Entry 035 — SSH disconnected after build start; original VM build confirmed active

- Local approved-build SSH exited 255/Broken pipe after the context rehash and
  actual Buildx start. Did not rerun or assume the build stopped.
- Read-only VM journal/lock/log query found original helper PID 1381021 holding
  the build lock, no image ID yet and no build error. Integrated builder loaded
  cached base and was transferring local context (~4.37 GB latest observed).
  This is VM-local context transfer, not a workstation upload or new image pull.
- Original seven native Bench PIDs still RUNNING; host free ~95 GiB at checkpoint.
  No customer-data mount, app startup or route change.
- Started a read-only completion watcher on that exact helper via pidfd readiness;
  it neither restarts the build nor modifies the daemon/native services. Progress
  and eventual journal outcome will be reported; no parallel build launched.
- Hardened future helper progress output so SSH Broken pipe cannot abort a
  successful build/validation journal step. Added exact-recorded-image-ID recovery
  for a build that exits zero and records image ID but loses final stdout: never
  rebuild or adopt an unrelated tag, and preserve all source/image artifacts.
  These code changes do not hot-patch the currently running helper.

## Entry 036 — export retained an image, but disk guard canceled build; no validation

- Completion watcher reports original helper finished. All twelve image steps
  completed; export manifest/config/naming and unpacking were logged. During final
  export/unpack the monitored build hit its staging disk guard and canceled only
  the build client. Buildx exited canceled, not success; journal image ID remains
  null and no validation was scheduled. Do not report a successful build job.
- Read-only follow-up confirms retained tag ndf-local/exact-runtime:dalali-v1 at
  sha256:c498d968d41e675c3ab27a6aabaef679720e118e2be919923590b4b7e5b37147,
  matching exported manifest in build log, with expected native entrypoint/Bench
  working directory. Inspectability alone does not prove runnable/validated image.
- Export/unpack transient disk use triggered the original 35-GiB incremental cap
  (monitor reserves 1 GiB). After cancellation, host free 95,203,438,592 bytes
  (~88.7 GiB), net incremental use 31,305,506,816 bytes (~29.2 GiB). This was the
  agreed staging budget guard, not host disk exhaustion. No budget increase.
- Protected service states/fingerprints unchanged; original seven native Bench
  PIDs RUNNING; Dalali maintenance=1. No customer data mounted/restored, app service
  started, existing deployment deleted or traffic switched.
- Retain all artifacts; do not automatically rerun because the tag exists and the
  build never returned success. Proposed next decision: exact-export-digest
  verification and bounded no-data validation of the retained image, rather than
  a rebuild. No build-step success/adoption may be inferred from an unverified tag.

## Entry 037 — continue with retained-image validation; cleanup deferred

- Operator requested continuing now, with unused files removed after migration.
  Continue exact-retained-image no-data validation; do not rebuild or mount live
  data. Defer cleanup until migration/recovery/rollback acceptance and confirm the
  exact deletion list then. No broad prune or deletion is performed now.
- Fresh read-only check confirms the same export digest/tag, expected startup
  configuration, 231 expected packages and Bench CLI 5.29.1. Protected config
  fingerprints unchanged, ~88.7 GiB free, original 35-GiB staging budget retained.
- Added explicit --validate-retained-image mode: require passed preparation/build
  approval, exact pinned export ID, original export+unpack evidence, cached base
  layer prefix and safe startup config. Run immutable image ID read-only/network-
  none/cap-drop with no host/data mounts. No build or download is scheduled.
- Keep original build command outcome canceled even if the retained image passes;
  separate retained-image verification/validation receipts and journal steps.
  Do not silently mark the canceled build successful or overwrite its tag.
- Runtime validation acceptance pending. No customer-data restore/mount or public
  cutover authorized by this validation step; no cleanup performed.

## Entry 038 — retained image starts; all app/dependency checks pass except Yarn

- Exact retained image verified against exported manifest, original unpack log,
  cached base layer prefix and safe startup configuration. No image rebuild.
- First full launch timed out after 300 seconds while preparing the retained
  image. No validation container remained running on inspection. Docker disk use
  grew as image content was prepared: net stage use ~34.164 GiB, within 35 GiB,
  with ~83.66 GiB host free. Do not raise budget or delete artifacts automatically.
- Bounded minimal network-none/read-only/no-mount smoke then succeeded: Python
  3.14.3 in 2.94 seconds. Added per-step validation diagnostics and timeout capture;
  no daemon/native service restart or customer data involved.
- Repeated full test completed and produced validation.json. Exact Python 3.14.3,
  shared libpython 3.14.3, OpenSSL 3.0.13, Node 24.14.0, MariaDB clients 10.11.14,
  Bench 5.29.1. All 12 pilot app imports resolve under /home/frappe/bench/apps;
  all additional tested dependencies import. All 231 Python package versions match.
- Validation correctly remains failed because `yarn` is absent, not silently
  waived. Native Yarn is /usr/bin/yarn -> /usr/lib/node_modules/yarn/bin/yarn.js;
  package 1.22.22, allocated size 5,386,240 bytes. Available for offline private copy.
- Retained image has local immutable RepoDigest ndf-local/exact-runtime@sha256:
  c498d968d41e675c3ab27a6aabaef679720e118e2be919923590b4b7e5b37147.
  Proposed remedy is a small additive local image with native Yarn and wrapper
  path, preserving original image and all partial contexts. Bound extra work to
  100 MB inside original 35-GiB staging cap; no new download or large rebuild.
- Latest suite: 119 tests pass; whitespace checks pass. Original seven Bench PIDs
  still RUNNING on follow-up. No site restore, live-data mount, cleanup or cutover.

## Entry 039 — small offline Yarn corrective layer approved

- Operator selected **Apply offline fix**: native Yarn 1.22.22 private copy, small
  new local image, retain existing image/artifacts, no downloads/customer mounts,
  added disk capped at 100 MB within unchanged original 35-GiB budget. No cleanup.
- Read-only preinspection found 11 native Yarn files with pinned SHA-256, no
  symlinks or credential indicators. Added native-yarn.json and a two-COPY/no-RUN
  Dockerfile based on the immutable local retained digest. Wrapper adds only the
  native Yarn bin path; Python/Node/compatibility paths remain unchanged.
- Added add_native_yarn.py dry-run entrypoint and journaled remote helper. Preserve
  raw package/root-private evidence and a new small context, never alter approved
  earlier contexts or overwrite existing tags. New local tag dalali-v2 only.
- Build monitor checks both the 100-MB subcap and original staging/free-space
  guards; no automatic deletion. No dependency install, registry push or rootful
  service. Revalidate exact child image ID with the same strict no-data suite.
- Actual layer build/validation outcome pending; no readiness inferred from approval.

## Entry 040 — corrective image built and strict no-data validation passed

- First digest-qualified parent reference caused integrated builder to attempt
  registry metadata resolution and fail access-denied before copying layers.
  No image/dependency layers downloaded, no private source published. Retained
  failed recipe/log and original context manifest instead of replacing them.
- Added separately named local-parent recipe, after checking retained context
  hashes. Existing parent tag is verified against the pinned export ID before/
  after, child base-layer prefix verified, --pull=false and no RUN/dependency
  install. New recipe/manifest evidence added; original image/context untouched.
- New local tag ndf-local/exact-runtime:dalali-v2 successfully built at
  sha256:e8b4c638deb0c739fa7a2f932c5a79f6bd5a5be291393dd3f1d114840bf7e8eb.
- Full isolated no-data validation passed: Python/shared libpython 3.14.3, Node
  24.14.0, Yarn 1.22.22, OpenSSL 3.0.13, MariaDB clients 10.11.14, Bench 5.29.1,
  Frappe/ERPNext 16.53.0/16.52.1; all 12 pilot app paths, 231 native package
  versions and additional dependency imports. No live/data mounts or network.
- Corrective work added 29,708,288 bytes within approved 100 MB and original
  35-GiB stage cap. No new layer/dependency download or cleanup. Both old/new
  images and all contexts/snapshots retained. Original build still recorded canceled.
- Post-validation protected-state/cgroup/disk checks passed. No native service
  restart, maintenance change, data restore, deletion or public traffic switch.
- Latest local suite: 124 tests passed; whitespace/shell/dry-run checks passed.
  Sanitized outcome and remaining gates in IMAGE_VALIDATION_RESULT.md. Operator
  informed of progress; image no-data validation is not full migration acceptance.

## Entry 041 — operator requests rehearsal plan/execution; new scope quantified

- Operator requested planning and executing isolated Dalali restore rehearsal.
  Per standing constraints, first performed read-only discovery; no lock, dump,
  source data copy, image pull, data mount or rehearsal service yet.
- Native health/config/PID/HTTP baseline unchanged. Source remains 1,000 base
  tables (997 InnoDB/3 MyISAM), 10 sequences, ~102 MB estimated DB, 5,855 files
  totaling ~378 MB apparent plus ~45 MB assets. No source file symlinks/specials.
- Both running native Redis endpoints report 8.8.0. Official matching MariaDB
  10.11.14/Redis 8.8.0 registry manifests/config hashes/platform verified on VM,
  metadata only. Compressed layers total 159,639,248 bytes; no layers fetched.
- Added REHEARSAL_PLAN.md and rehearsal-images.json: proposed 180-MB direct-VM
  download cap, separate 8-GiB rehearsal disk cap with 60-GiB free floor, existing
  aggregate 2 CPU/3 GiB; new project/storage/network with no runtime egress or
  workers/scheduler and only optional 127.0.0.1:18088 frontend publication.
- Fresh pilot-only pause proposal: 5-second acquisition, max 55-second READ locks
  on base tables/WRITE locks on sequences with watchdog/60-second idle fallback.
  No global locks/shared-service stop. Original checkpoint pause approval does not
  authorize another pause; fresh scoped approval pending.
- Existing encrypted archive remains untouched. Use new private server-side
  dump/files/config copies only after synthetic network/version tests and approval;
  restore only into verified new empty clone/recovery destinations. No live mounts,
  overwrite, cleanup, public route or source maintenance change.
- PDF check found native wkhtmltopdf 0.12.6 vs cached 0.12.6.1 patched Qt. Plan
  explicitly preserves native version via bounded private-copy feasibility test
  and does not waive PDF/font acceptance. Native fonts ~12.8 MB; custom fonts empty.
- Dependency distribution build differences and rehearsal-vs-final consistency
  limits recorded. Master automation/final writer fencing/cutover still gated.

## Entry 042 — full bounded rehearsal approved; image phase prepared

- Operator selected **Approve full rehearsal** for the quantified plan: 180-MB
  direct-to-VM pinned image transfer cap, separate 8-GiB additional rehearsal disk
  cap with 60-GiB host-free floor, unchanged aggregate 2 CPU/3 GiB, and one fresh
  pilot-only 5-second-acquisition/55-second-maximum database lock/copy.
- New isolated storage only; clone-only validation/recovery writes permitted.
  Source stays in maintenance, workers/egress blocked on clone, old encrypted
  archive untouched. No production overwrite, cleanup, public route or other
  site/mail changes authorized. PDF gaps must not be waived or silently upgraded.
- Added execute_rehearsal.py/rehearsal_remote.py with dry-run default, pinned-host/
  account/daemon/validated-image guards, root-private journal/lock, separate-phase
  disk baseline and monitored image pulls. Phase outcomes must be verified before
  progressing. No lock/dump action exists in image phase.
- Images phase will pull/inspect only the two approved pinned manifests, verify
  exact binaries in no-data containers, reserve conservative transfer accounting
  and stop on unexpected payload, image/platform/version, resource or health drift.

## Entry 043 — pinned rehearsal images pulled/verified; synthetic isolation next

- Approved image phase completed successfully on VM. Pinned MariaDB 10.11.14 and
  Redis 8.8.0 manifests/platform/binaries verified in no-data network-none tests.
- Accounted 169,639,248 bytes including conservative 5-MB per-image overhead,
  within 180-MB cap. Image layers stayed on VM; no customer backup downloaded.
- Native protected-state checks passed before/after. No pilot DB lock/dump,
  customer files/config copy or real-data restore yet.
- Synthetic isolation implementation uses new labelled internal IPv4 network,
  fresh labelled volumes, independent clone credentials and readonly DB/Redis
  roles without published ports. Guard ownership/creation identity before reuse;
  refuse vanished/replaced/unrecognized resources or non-empty restore target.
- Runtime probe must connect only to new services, confirm exact DB settings/ID,
  empty Redis, absent default route, blocked external/native TCP and known-positive
  external DNS lookup. No source site data/key is loaded during this stage.

## Entry 044 — new DB ready; Redis startup restriction handled without broader privilege

- Created only new labelled internal network/DB/Redis volumes and private clone
  credentials. New MariaDB initialized empty; engine version, SQL mode/charset/
  collation/timezone/max-packet and expected hostname/server ID matched the plan.
- Synthetic isolation probe stopped before data collection: Redis aliases failed
  resolution because official 8.8 entrypoint exited (setpriv capability-bounding
  operation denied). No default-route/egress acceptance inferred from this failure.
- Read-only inspection confirmed DB running, both Redis attempts exited 127 with
  no OOM. Logs were redacted against generated clone secrets before summary.
- Retain failed containers/volumes. Corrective Redis containers use new -nonroot
  names, same verified fresh project volumes, exact cached image and direct
  redis-server entrypoint as user redis, cap-drop ALL. Do not grant SETPCAP,
  relax no-new-privileges or change daemon/native services.
- No Dalali dump, production lock, file/encryption-key copy or data restore has
  occurred. Retry only the synthetic stage with resource ownership guards intact.

## Entry 045 — synthetic isolation passed; one approved source snapshot next

- Corrective non-root Redis roles started successfully, retaining exited attempts.
  New empty MariaDB/Redis identity, settings, freshness and private DNS confirmed.
- Probe passed: no default route, external TCP/native endpoint connections blocked
  and known-positive external DNS lookup blocked. No site data/keys mounted.
  Native health/config/PID/forwarding comparison passed before/after.
- Source collection implementation now gates on that proof and rechecks current
  backend network/port/DNS/privilege/mount ownership. Copy/hashes only Dalali
  files/config and approved static assets into root-private staging.
- Import SQL driver only from root-owned private PyMySQL copy without executing
  live .pth files; disable bytecode writes. Use existing tested scoped lock/dump
  helper, never checkpoint main/encryption/archive code. Add locked source row
  counts to verify restored contents. Exactly one lock attempt is journaled before
  acquisition; retries cannot silently take another pause or overwrite staging.
- Fresh app commit/clean-worktree, DB object/engine/routine scope, source file/
  asset/config drift and budget guards must pass. Same 5-second acquisition /
  55-second hold watchdog and 60-second idle fallback; no global/native stop.
- Source dump/copy outcome pending. Source remains a rehearsal snapshot, not proof
  of final writer quiescence; no real-data restore or cutover yet.

## Entry 046 — single scoped snapshot completed; isolated restore prepared

- Approved source snapshot completed with lock held **44.699 seconds**, released
  within 55-second watchdog. Captured exactly 1,000 base tables/10 sequences and
  locked per-table row counts; gzip CRC/DDL counts verified. Dump 5,492,464 bytes,
  SHA-256 24a964664d7b837ac15643320b7c66d0e20154db267a9186fd330d8ab1ae3bec.
- All 5,855 attachment files (~378 MB apparent), source config and static assets
  stable across capture; root-private copies/manifest retained. Source maintenance
  and protected native configuration/services/PIDs unchanged. No archive decryption
  or workstation backup transfer. This is not final cutover writer quiescence.
- Restore implementation targets only owned fresh DB container and empty schema,
  records one import attempt, stops on error without --force/drop/replay. Streams
  existing gzip directly VM-local; no raw dump/customer bytes in chat/log summaries.
- Verify all 1,000 row counts and ten sequence cache boundaries against the locked
  snapshot, then populate new labelled sites/log volumes only. Raw source config
  remains root-private; distinct clone config uses new credentials/internal Redis,
  copied encryption key, paused scheduler/mail/webhook flags and initial maintenance.
- Private file/assets copy uses a verified network-none helper, no host data binds;
  tighten clone private permissions and verify every attachment hash/asset link.
  Stop only the new helper after checking it; retain container/volumes, no cleanup.
- No app server/frontend/worker started yet. Restore outcome pending; PDF parity,
  application/security/login/recovery tests and public cutover remain open.

## Entry 047 — isolated DB/files restore verified; private app/PDF stage prepared

- Restore completed into the new private DB without --force/drop or replay:
  exact 1,000 base tables and 10 sequences; every locked per-table row count and
  every captured sequence cache boundary matched. Source DB was not a restore target.
- All 5,855 staged attachment bytes/hashes and copied asset files/links verified
  inside new labelled sites volume. Clone config has independent DB/Redis
  credentials, copied source encryption key, paused scheduling/mail/webhooks and
  initial maintenance. Source raw configuration remains root-private.
- Temporary network-none copy helper stopped after verification but retained;
  no existing resource/data deleted. No live filesystem bind was used for files.
- New private application stage gates on verified restore. Exact native 0.12.6
  PDF binary/library/offscreen-plugin/font copies and no-data text-render probe
  are required before app mounts; no global library overwrite or renderer upgrade.
- Clone control step verifies DB identity before any operator-password write,
  checks all 12 installed apps and encrypted-field decryptability without exposing
  decrypted values. Only the clone may leave maintenance; source remains 1.
- Web/Socket.IO/frontend use private new volumes and fixed native Bench path;
  Socket.IO self-requests go to internal web, not source/public origins. No workers/
  scheduler. Frontend is loopback-only, private files require Frappe authorization,
  protected redirects internal; vendor Nginx bypasses native LD_LIBRARY_PATH wrapper.
- App/PDF/HTTP/security/recovery outcomes pending. Full rehearsal not yet accepted;
  no public traffic switch or cleanup.

## Entry 048 — exact PDF and clone encryption passed; frontend readiness correction

- Private native 0.12.6 PDF binary + reviewed dependency/offscreen-plugin/font
  copies rendered a one-page synthetic PDF with correct accented text, no network
  or customer mounts. Native font hashes preserved; no 0.12.6.1 adoption/global
  library override, image rebuild or extra download.
- Clone control initialization revealed Docker exec does not inherit entrypoint-
  exported LD_LIBRARY_PATH. Operator exec now explicitly uses ndf-native-exec and
  sites working directory; create only empty new clone logs/locks/backup dirs.
  These fixes do not alter native config or the validated application image.
- Initialized clone DB successfully: all 12 installed apps/order match, both
  encrypted fields decrypt, encryption roundtrip succeeds, clone-only operator
  password set. Native maintenance remains 1; distinct clone maintenance set 0.
- Web/Gunicorn and Socket.IO run privately. Initial frontend exited because Nginx
  tried default fastcgi temporary directory on readonly root. Failed role/config
  retained. New frontend generation/config sets all temp paths to /tmp tmpfs;
  no loosening readonly/capabilities or host proxy change.
- Earlier application_started marker meant role creation, not HTTP readiness;
  require a new application_ready gate that verifies running frontend and HTTP
  ping=200 before claiming application readiness. HTTP/security/recovery pending.

## Entry 049 — internal-only port publication diagnosed; loopback-only RootlessKit forwarding

- Corrected frontend is running with readonly root and tmpfs temporary paths.
  Web/Socket.IO healthy, but host connection refused. Read-only inspection shows
  Docker HostConfig requested 127.0.0.1:18088 while effective port is null: Docker
  did not publish a port for the internal-only network. RootlessKit port list empty.
- Do not add an external/default-route network to application containers or alter
  host firewall/Nginx. Implement exactly the already-approved loopback mapping via
  existing dedicated RootlessKit API, bound to current owned frontend/private IP.
- Verify API socket ownership UID 5001, internal network/IP, no existing listener
  or mapping; record returned mapping ID/spec in private journal. Refuse changed/
  unrecognized mappings without replacement/deletion. Verify actual listener is
  exclusively 127.0.0.1:18088, then require HTTP backend ping=200.
- No new binary/download, rootful daemon, source network change, data overwrite or
  source maintenance change. Runtime containers keep only their internal network
  and absent default route. Mapping lifecycle/IP reconciliation remains an explicit
  deployment concern before any production cutover; this is rehearsal only.

## Entry 050 — private frontend HTTP-ready; application/security probes prepared

- Exact approved loopback forward established through dedicated RootlessKit;
  frontend and all runtime roles remain on internal-only network, no default route.
  Real host-loopback HTTP ping passed 200; application_ready gate now complete.
- No native route/TLS/Nginx/source maintenance change. New frontend source/runtime
  roles are private; operator is not instructed to open an unrestricted browser.
- Added application-tests phase: recheck ownership/network/privilege/port/DNS and
  negative runtime egress after inbound forwarding. Create one marked nonfinancial
  Note fixture only in verified clone DB, check Frappe PDF integration, record
  compiled asset/representative-record probe plan without exposing PII.
- Private HTTP client uses clone-only operator credentials, never logs cookies or
  password, does not trust proxy environment or follow redirects. Check ping,
  protected/config denial, guest privileged API denial, login, record counts,
  private Note, compiled assets, guest private-file denial/authenticated hash and
  authenticated Socket.IO connection. No payment/email/SMS/provider tests.
- Application probes and restart/recovery tests still pending. Full rehearsal
  acceptance is separate from HTTP-ready/app-import status; no cleanup or cutover.

## Entry 051 — HTTP login exposes existing source schema gap; no migrate without approval

- Post-forward runtime checks passed: all live roles retain only owned internal
  network, readonly/cap-drop restrictions and blocked external/native TCP/DNS.
- Created one marked nonfinancial Note only in verified clone DB. Frappe's own
  PDF integration rendered expected synthetic accented text using native 0.12.6;
  asset/record-count plan prepared. Both copied encrypted fields already decrypted.
- HTTP tests confirmed ping and guest/config/internal-location denial before login
  stopped acceptance: POST login returns 500 OperationalError 1054, despite creating
  a valid clone session, because homepage user loading requests send_read_receipt.
  Do not waive status 500 just because authentication/session creation succeeded.
- Read-only information_schema comparison confirms tabUser.send_read_receipt is
  absent in **both native source and restored clone**, while frozen core User JSON
  defines it. Evidence root-private rehearsal-v1/schema-gap.json. No schema change
  or native login/reset was performed; clone is consistent with source schema.
- REHEARSAL_PLAN explicitly forbids using migrate/schema upgrade as an unreviewed
  shortcut. Stop before DDL. Proposed separate approval: checkpoint/recover the
  private clone, then run bench migrate **only inside owned isolated clone** using
  frozen app revisions, same budgets, workers/egress off, no downloads or package
  updates. Fail on patches/errors; do not --skip-failing, modify native or cut over.
- Application security/asset/Socket.IO/restart/recovery gates remain incomplete.
  Public Dalali remains native/maintenance=1; old archive and all artifacts retained.

## Entry 052 — clone-only recovery checkpoint and frozen-code migration approved

- Operator selected **Approve clone migration**: first checkpoint/recover the
  private clone, then bench migrate only the verified isolated clone with frozen
  app versions. No native schema/data changes, package updates/downloads, extra
  source pause, cleanup or cutover. Existing 8-GiB/60-GiB-floor/2-CPU/3-GiB limits.
- Added explicit --approve-clone-migration gate. Migration requires proven clone
  recovery first, owned image/controller/internal DB hostname+server ID, and one
  journaled attempt. No automatic replay on failure or --skip-failing.
- Recovery implementation pauses only new clone web/Socket.IO writers, keeps
  frontend address stable, streams VM-private clone SQL/selected sites checkpoint,
  captures current rows/sequences/files and restores into second new labelled
  DB/sites destinations. Re-check exact counts/boundaries, fixture and encrypted
  fields without printing decrypted values. Recovery config points only to recovery
  DB and has cache/worker behavior fail-closed; no source backup/archive is used.
- Preserve all partially failed/stopped containers, new volumes and checkpoints.
  Retain stopped verified recovery DB for rollback; no remove/prune/down -v.
  Resume only owned clone roles and reload only private Nginx after IP changes.
- After recovery, clone migration may temporarily give only clone control helper
  1 CPU/1 GiB inside unchanged aggregate budget, then restore its earlier limits.
  Verify missing field fixed in clone and still absent in source using SELECT-only
  metadata queries, restore clone safety flags and repeat app tests. Outcomes pending.

## Entry 053 — private clone checkpoint/recovery running; transport disconnect handled

- Recovery phase launched with one marked clone fixture and existing clone data.
  Paused only owned clone web/Socket.IO, not source; created VM-private clone SQL/
  selected-sites checkpoint without another production dump/lock or archive access.
- Local SSH exited Broken pipe after checkpoint milestone. Read-only journal/lock
  inspection found original VM helper still running: new recovery DB import and
  recovery files population completed; content/key verification still pending.
  Did not restart the phase, overwrite data or assume success from partial markers.
- Started read-only pidfd completion watch on the exact helper. All sensitive
  checkpoint contents remain VM-private; console reports only phase/count/status.
- Final validation code now requires separate recovery/schema-sync/app checks,
  owned clone-only restart tests and repeated HTTP/runtime/source-health checks.
  Explicitly distinguish pre-migration rollback recovery from current migrated
  clone/business UI acceptance. No public cutover or cleanup implied.

## Entry 054 — pre-migration clone checkpoint recovered successfully

- Completion watcher confirms original recovery phase finished successfully with
  clone_recovery_verified marker; no phase restart or duplicate source pause.
- Recovery proof: 1,000 tables/all row counts, 10 sequences/all boundaries,
  marked nonfinancial fixture, all 5,855 file hashes, both encrypted fields and
  isolated recovery host matched. This recovers the private pre-migration clone,
  including its known source schema gap, not a claim that the old schema can login.
- Recovery DB stopped but retained to free RAM; clone web/Socket.IO resumed and
  private frontend reloaded. No source process restarted or volume removed.
- Next approved clone-only migration uses a new stable idle controller (earlier
  expiring helper retained), exact frozen app image, command/help preflight,
  verified DB identity, private checkpoint and single-attempt journal. Source
  missing-column metadata remains unchanged before actual DDL. Outcome pending.

## Entry 055 — Bench help preflight caught missing layout before any schema attempt

- First approved migration phase stopped at --help, before web/Socket.IO pause or
  clone_migration_attempted flag. Bench reported not a bench directory/no --site
  because its detection requires apps/sites/config/logs/config-pids; image omitted
  native config intentionally. No DDL or migration patch executed.
- Read only the private copied Bench helper source to confirm directory checks.
  Supply a new empty private bench-config/pids bind, readonly, to a new controller
  generation. Do not copy/mount native service configs, add source Redis endpoints,
  rebuild image or relax rootfs permissions. Prior idle helpers retained.
- Re-run help preflight; only then may the separately approved single real clone
  schema migration attempt start. Original source and verified rollback remain.

## Entry 056 — corrected Bench preflight passed; real clone-only migration running

- New controller mounts only the fresh empty readonly config/pids placeholder;
  previous controllers/configs retained. Bench help preflight succeeded.
- Actual separately approved migrate command started inside isolated clone from
  frozen image. Single-attempt journal set before DDL; original source not target.
  Private log clone-migrate.log retained on VM; local console reports milestones.
- Existing aggregate CPU/RAM and rehearsal disk guards monitored; no downloads,
  package updates, new production pause, cleanup or route change. Do not infer
  successful migration from launch; app/HTTP/restart acceptance still pending.
- Latest local suite: 150 tests passed; whitespace checks passed.

## Entry 057 — clone migration succeeded; application probes pass, restart tests next

- Approved single real bench migrate command completed successfully inside the
  isolated clone from frozen image/code. User.send_read_receipt exists in clone;
  read-only check confirms still absent in native source. Source config/maintenance/
  service/PID/forwarding checks passed; no native schema change or extra source pause.
- Clone safety overlays retained, helper limits restored inside unchanged aggregate
  budget, web/Socket.IO resumed and only private frontend reloaded/verified.
- First post-migrate test SSH had a transient Cloudflare timeout; normal hostname
  retry succeeded. No auth URL/token recorded in this migration record.
- Process audit initially used ps args without PID, which Docker top rejects;
  corrected to pid,args, keep raw argv in memory only and handle exited-helper race.
  No workload privilege/security relaxation.
- Repeated application checks passed: HTTP ping, config/protected denial, guest
  privileged API/private Note denial, clone-only login, representative record counts,
  compiled assets, private-file guest denial/authenticated content hash and Socket.IO
  polling with authenticated session. No external redirects/provider requests.
- Add direct authenticated WebSocket transport to repeated checks before final
  restart gate. Final source/native HTTP/resource comparison and clone-only DB/
  Redis/web/socket restart-persistence tests still pending; no cutover/cleanup.
- Repeated checks also require actual authenticated Desk and guest website HTML,
  not just API ping. Follow only explicitly validated clone-internal redirects;
  never public/native/provider hosts. No unrestricted browser/JS execution.

## Entry 058 — isolated technical rehearsal passed; production gates still closed

- Restarted only owned rehearsal DB/Redis/web/Socket.IO and reran a fresh HTTP/
  auth/fixture/asset/private-file/realtime suite. Desk /app correctly redirects to
  /desk in this frozen version; harness permits only validated internal HTTP
  redirects, not provider/public hosts or unapproved transports/ports.
- Final passed metrics: seven representative doctype counts, 70 compiled assets,
  authenticated Desk and guest website HTML, private fixture and privileged API
  guest denial, private attachment guest denial/authenticated hash, authenticated
  Socket.IO polling **and direct WebSocket**. Committed fixture/auth persisted.
- Both encrypted fields decrypt after clone migration/restart, encryption
  roundtrip passed. Native PDF and Frappe synthetic text/font rendering passed on
  exact 0.12.6. Worker/scheduler process audit and negative runtime egress passed.
- All 1,000 tables/10 sequence boundaries/5,855 files verified initially and in
  second new pre-migration rollback recovery destination. Explicitly record this
  checkpoint's pre-schema-sync scope; no claim it is a fresh final production copy.
- Final native baseline Dalali/Dulaman 503, demo/ERP 200; original maintenance,
  protected configuration/services/PIDs/forwarding intact. Native missing-column
  metadata still absent; only clone schema synchronized to frozen code.
- Accounted transfer 169,639,248 bytes <180 MB; added rehearsal disk
  3,837,976,576 bytes (~3.57 GiB) <8 GiB; host free 85,947,277,312 bytes
  (~80.05 GiB) >60-GiB floor. Aggregate 2 CPU/3 GiB/256-MiB swap/1024 tasks verified.
- Root-private rehearsal-v1/result.json records technical_rehearsal_passed=true,
  business_ui_acceptance_pending=true, no public_cutover/source_schema_modified/
  cleanup/encrypted_archive access. REHEARSAL_RESULT.md is sanitized outcome.
- No site promoted, source reopened, real provider action tested, old deployment/
  image/volume/source snapshot/backup deleted or further backup downloaded. Next
  gates: constrained business/UI acceptance, automation/writer fencing, fresh
  final data sync/schema alignment/recovery plan, explicit route/write-open and
  later exact-list cleanup/retention approval. Do not infer them from rehearsal.
- Final local validation: **151 tests passed**, git diff --check clean, all
  migration Python/JSON parsed. No commit/reset or unrelated WIP changes discarded.

## Entry 059 — operator requests UI/business acceptance then final sync; browser budget pending

- Operator requested continuing business/UI acceptance, then final synchronization
  and separately approved cutover. Begin UI planning/discovery; do not infer a new
  production pause, live overwrite, traffic switch, write-open or cleanup approval.
- Read-only check confirms technical rehearsal passed, ~80 GiB host free, ~3.6 GiB
  rehearsal use. VM has no cached browser/automation packages; workstation Chrome
  exists but would move UI/customer responses off VM. Prefer VM-resident browser.
- Queried small official Chrome metadata/HEAD only on VM: pinned headless shell
  156.0.8078.47, 125,799,226 bytes, object generation and CRC32C recorded. No binary
  fetched. Most native browser libraries present; libasound missing.
- Reverified cached Ubuntu InRelease signatures/index checksums; reviewed audio
  library/data package metadata, 419,122 bytes with exact SHA-256. No apt refresh,
  package download/install or protected-service change.
- Added UI_ACCEPTANCE_PLAN.md/ui-browser.json. Proposal: new 150-MB direct-VM
  transfer cap, 1-GiB UI disk subcap within existing 8-GiB phase, unchanged global
  resources/free floor, rootless browser readonly/temporary profile, hard no-egress
  plus clone-only proxy/action allowlist, normal sandbox first, no silent security
  relaxation or screenshots/DOM/credential/PII exports.
- Automated UI evidence does not grant business acceptance. Final-sync proposal
  must resolve source ownership/writers, new pause/consistency/storage/schema-sync/
  rollback budget, then separate route/write-open approvals. No such action yet.

## Entry 060 — UI budget approved; normal sandbox prerequisite blocked before download

- Operator selected **Approve VM UI tests** under the 150-MB/1-GiB-subcap/original
  8-GiB/60-GiB-floor/aggregate-resource constraints, no host package/app upgrades,
  VM-only data, no PII artifacts/real provider actions and no silent security changes.
- Before downloading, ran only a no-data namespace prerequisite in fresh readonly,
  cap-drop/no-new-privileges/network-none non-root container. CLONE_NEWUSER unshare
  denied EPERM. No browser binary, source/customer page or credentials loaded.
- Read-only policy inspection: seccomp enabled, inherited targeted RootlessKit
  profile; dockerd embeds moby/profiles/seccomp v0.2.3. Reviewed matching public
  default JSON only, not applied or installed. Default blocks required namespace
  operations without outer capabilities; do not solve by --no-sandbox/SYS_ADMIN.
- UI_SANDBOX_PLAN.md proposes new browser-only default-preserving argument-limited
  inner user/PID/network namespace/chroot rules, no clone3 broad allow/default
  relax/host policy change, and staged no-data positive/negative probes. Exact
  profile/application requires separate approval. No existing container modified.
- Stop before security change, as promised. Technical rehearsal still valid;
  UI/business acceptance, final synchronization, public cutover and cleanup not run.

## Entry 061 — narrow browser-only profile approved; no-data probes prepared

- Operator selected **Approve browser-only profile** for default-preserving,
  argument-limited inner user/PID/network namespace/chroot access. No capabilities,
  SYS_ADMIN, no-sandbox, unconfined seccomp, host policy/sysctl/daemon change or
  backend-container policy change authorized. Stop on any further requirement.
- Matching v0.2.3 baseline fetched as small metadata on VM, 13,470 bytes, SHA-256
  536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74, 33 rules.
  Original preserved. No browser/support binary downloaded.
- Added pure ui_seccomp.py generator preserving all existing rules/default ERRNO,
  amd64-only additions with clone namespace masks, user/PID/net-only unshare/setns
  types, chroot and unchanged clone3 ENOSYS fallback. Outer capability-drop stays.
- Added dry-run-default ui_acceptance.py and guarded remote probe. Reuses existing
  rehearsal identity/health/budget/lock, new UI subcap/journal and new probe only.
  Require positive inner-user/PID/net and negative mount/UTS/IPC namespace tests
  before any browser download or customer-page navigation. Exact diff retained.

## Entry 062 — browser-only namespace probes passed; approved tool download next

- Positive user and user/PID/network namespace probes passed with zero outer
  capabilities. Mount/UTS/IPC namespace combinations denied EPERM as required.
  New profile applied only to new no-data probes; host/existing container policy
  unchanged. Full normal Chrome sandbox is still not assumed from this result.
- Added private UI tool preparation: bounded direct-VM downloads with pinned Google
  object generation/length/CRC32C, ZIP integrity/SHA-256 record and exact Ubuntu
  package hashes. No host execution of downloaded browser or host package install.
- Static ELF dependency inspection/copies from reviewed root-owned native files,
  private extracted audio support and known NSS provider modules, no all-/usr copy.
  Readonly tool prefix/version probe runs only in rootless no-data container with
  approved profile; no customer pages/credentials or public debug endpoint.
- Normal browser synthetic sandbox launch and clone-only request/action guard
  proof remain next gates. Stop on any further syscall/policy/download requirement.

## Entry 063 — pinned browser prepared and normal sandbox passed; final-sync audit read-only

- Tools prepared directly on VM: exactly 126,218,348 bytes, below new 150-MB cap.
  Browser generation/CRC32C/ZIP and recorded SHA-256 verified, audio DEB hashes
  verified, native dependency copies statically inspected. No host package install.
- Chrome for Testing 156.0.8078.47 binary version verified in constrained rootless
  container. Normal sandbox synthetic page rendered successfully without bypass,
  SYS_ADMIN, additional capabilities or host/existing-container policy changes.
  No customer page/credentials supplied during these probes.
- New proxy/action guard default-denies non-clone origins, unreviewed API mutations,
  unsafe Socket.IO events and arbitrary CONNECT/TLS; only marked Note Save may
  mutate application data. Frame-inspect websocket traffic, pin clone upstream,
  no raw request/cookie/response logging. Code/data guard proof before UI credentials.
- Browser/CDP harness keeps profile in tmpfs, no public debug port, dumps/screenshots/
  traces or raw console exports. Block background/network/worker/popups/downloads;
  use standard Node/CDP only. Customer UI checks not yet executed.
- Separate read-only source audit: no matching Dalali control-plane records;
  scheduler 113 enabled/last execution Oct 3, known shared queue lengths 0, one
  shared-bench cron entry. Counts/cron hashes only exported; no pickles/descriptions
  or credentials decoded. This is not proof of no in-flight/external writers and
  does not authorize fencing/final sync or another source pause.

## Entry 064 — real browser CSP blocker confirmed; clone-only adjustment awaits approval

- Current local suite: 160 tests passed; both UI JavaScript syntax checks and
  git diff --check passed. Fresh hash-bound no-data guard passed before the next
  credentialed UI attempt (`ui-guard-csp-event-20261026.log`).
- Real browser login/Desk boot succeeded, Item list still failed. Document policy
  violation listener confirmed `page_csp_eval_blocked: true`; caught compilation
  errors were missed by older CDP-only boolean diagnostics. Frozen Frappe template
  compiler uses new Function; render/make_page exceptions match that restriction.
- Added UI_CSP_DECISION.md with exact proposed clone-only script-src unsafe-eval
  addition, private original-config retention, clone nginx validation/reload and
  rollback scope. No policy change applied; stop for separate approval.
- Tracking/recommendations remain blocked; remaining UI matrix and operator
  acceptance are incomplete. No production/config/source schema/pause/sync/route/
  write-open/decryption/cleanup change made.

## Entry 065 — exact clone-only CSP change separately approved and applied

- Operator selected **Approve clone-only CSP**: add only unsafe-eval to private
  script-src for frozen Frappe templates; retain original, validate/reload only
  owned clone Nginx, no host/native policy change or deployment removal.
- Added explicit dry-run-default `csp` phase and ui_csp.py: exact single-header/token
  checks, owned frontend/image/mount verification, original bytes retained in VM
  root-private UI folder, inode-preserving private config update, nginx -t/reload
  and exact served policy verification. Reject unrelated config/policy changes.
- Initial served-header check raced the asynchronous reload and restored original.
  Bounded exact-old/new-header waiting corrected this, then approved change passed.
  Same frontend remains; host Nginx/TLS/native services and maintenance unchanged.
- Desk eval-block event disappeared and template/add_page TypeErrors ceased in
  completed runs. CSP compatibility does not prove full business acceptance.

## Entry 066 — partial UI evidence strengthened; overall acceptance still blocked

- Reviewed frozen read implementations before narrowly allowing List Filter
  listing, ordinary validate-link lookup without custom-query dispatch and six
  ERPNext settings/dashboard/prices/dimension/reference-count reads. Kept business
  mutations, tracking, preference/history writes and regional dispatch denied.
- Runner now waits for list response completion plus browser AJAX callbacks,
  opens existing forms without edits, clicks real Note Save and verifies reload
  persistence, checks guest financial-data denial. Names/data stay VM-side.
- Latest complete VM run 2026-10-26 01:07:19–01:08:23 UTC: exact-hash guard passed,
  six list-data responses 200, four existing forms rendered with dirty=false,
  Employee/Loan lists had no rows (forms not tested), Note click/save/reload and
  guest denial succeeded. Two homepage recommendation errors and 16 action denials
  (including negative control) keep automated result false/operator sign-off pending.
- Intermediate Note-click timeouts retained as failures, not silently waived.
  Added timestamp/hash/status for each test attempt so stale earlier results cannot
  be mistaken for the newest failed attempt. Latest complete status is `blocked`.
- One SSH Broken pipe was followed by VM lock/container/journal inspection before
  replay; later read-only SSH banner timeout retried once. No unexamined mutation
  replay, new source pause, sync or route change.
- UI_ACCEPTANCE_RESULT.md separates successful checks, blockers and remaining
  storefront/product/search/roles/browser-files/PDF coverage. Final-sync ownership/
  fencing/storage/schema/recovery plan and separate cutover/write-open still gated.
- Receipt: 126,218,348 UI download bytes, ~0.39 GiB UI disk, ~3.99 GiB rehearsal
  added disk and ~79.63 GiB host free; original caps/resources unchanged. No backup
  download/decryption/cleanup or native data/schema/service changes.
- Local validation: 161 tests, both Node syntax checks and git diff --check passed.
- Further frozen Webshop review confirms get_party can save Portal User links or
  insert Customer/Contact with ignored permissions. skip_quotation_creation does
  not prevent this when prices are enabled; _get_cart_quotation constructs an
  unsaved document, not necessarily a persisted Quotation. Recommendations remain
  denied pending synthetic-only scope review or explicit operator exclusion; no
  implicit business writes authorized/performed to force storefront acceptance.
- Post-run no-data guard/protected native health passed. Native HTTP read-only
  receipt at 01:10:35 UTC retained Dalali/Dulaman 503 and Demo/ERP 200.

## Entry 067 — restricted public Dalali staging requested; discovery only

- Operator selected `d-stage.ndftechlabs.com`, initially only the explicitly
  supplied reviewer email, with future reviewers individually addable. Operator
  explicitly approved clone pages/customer data in authorized reviewers' browsers.
  Do not commit the private reviewer address, infer unrestricted public access or
  confuse staging review of existing bugs with production cutover/business sign-off.
- Read-only VM audit at 01:23:22 UTC: no d-stage Nginx config or DNS resolution;
  Dalali certificate does not cover staging; existing cloudflared service/config
  active and unchanged. No Cloudflare admin/API/Access visibility established.
- Existing cloudflared 2026.3.0 supports token-file/no-autoupdate. No install,
  upgrade, secret/key/token read/export, host policy/service or DNS change made.
- STAGING_REVIEW_PLAN.md proposes a separate staging-only tunnel to a new guarded
  loopback gateway, not raw clone frontend. Requires whole-hostname Access exact
  email policy/token validation, origin/action/WS defenses and synthetic proof.
  Existing SSH connector, host Nginx/TLS, native sites/mail and maintenance retained.
- Account-side Access application/team domain/AUD and an exact service/security
  deployment approval are required before publication. Connector credentials must
  remain private on VM; no generic cloudflared service-install replacement.
- Normal reviewer-browser navigation differs from isolated VM-browser networking;
  do not silently assert equivalent external-navigation containment. Existing
  implicit-write recommendation endpoint remains blocked. No sync/cutover/cleanup.

## Entry 068 — Access identifiers supplied; public keys checked; local boundary tests only

- Operator supplied ndftech.cloudflareaccess.com and the d-stage application AUD.
  Dashboard JSON corrected to agreed hostname, HttpOnly true; session still 24h.
  Suggested 1h was advisory, not silently enforced. Email-only policy association
  still needs account-side verification; IDs/public keys do not prove it.
- Under the rehearsal lock, VM fetched only fixed public HTTPS signing metadata:
  5,116 bytes, two RSA keys, checksum/private receipt recorded at 01:54:14 UTC.
  New root-private stage-v1 request/config has origin_enabled=false. No secret/
  private key/token exported, customer data read, listener/service/route started.
- Added pure stage_access.cjs preparation component and 14 synthetic tests:
  RS256 signature/alg/key URL defenses, exact host/issuer/AUD/email, session and
  signing-snapshot age, rotation and malformed/duplicate assertions. No network
  or listener on import; this is not yet a completed public action gateway.
- STAGING_DEPLOYMENT_PROPOSAL.md gives separate gateway/connector/key-refresh
  identities, cgroup/resources, bounded metadata traffic, no package/image pulls,
  read-only public-review contract and synthetic-before-clone publication gates.
- New staging edge necessarily needs external Cloudflare connections; ordinary
  reviewer browsers do not inherit the VM browser-wide network fence. Exact
  service/security scope and this manual-review boundary require approval before
  deployment; existing app egress, shared tunnel/Nginx/TLS/native services unchanged.

## Entry 069 — guarded staging approved; private readiness services prepared

- Operator selected **Approve guarded staging**, covering the exact new rootless
  gateway, separate connector and bounded public-key refresh with normal-browser
  navigation limitations, no package pulls, original budgets/resources and no
  shared/native service/route changes. Public clone only after authentication and
  action proofs. New token must be privately entered, not pasted into chat.
- Added dry-run-default stage_review.py and guarded stage_prepare_remote.py, pure
  readiness HTTP gateway and fixed-URL bounded public-key refresher. Reused existing
  rehearsal lock, ownership/identity, native config/PID/service, disk and egress
  checks. Existing files differing from approved code/config cause refusal.
- Readiness gateway deliberately has no clone/upstream forwarding code; exact
  config cannot enable it. Real identity config only mounted after synthetic
  no-network identity/HTTP proof without real customer/credential mounts passed.
- New gateway runs rootless non-root/readonly/cap-drop/default seccomp with 96-MiB
  no-swap/0.25-CPU/64-task limits; owned loopback 18089 mapping only, existing 18088
  preserved. Anonymous loopback requests denied 403. CSP/cache/noindex headers on
  synthetic readiness content only; clone CSP untouched.
- Key one-shot/timer active, fixed team HTTPS only, no redirects, 64-KiB/15-min cap,
  one-hour accepted freshness, atomic public snapshots and ongoing disk checks.
  Non-root 32-MiB service in existing aggregate user-5001.slice; no app data/secrets.
- Separate hardened 128-MiB connector unit installed but inactive/not enabled, no
  credential. Explicit independent config/no-autoupdate/token-file/systemd credential
  scope; approval marker required before startup. Existing cloudflared untouched.
- Installed stage_store_token.py for operator-only hidden TTY entry: verifies new
  UUID/token structure, rejects protected shared UUID or any overwrite, mode-600
  root-only storage, no secret output/service startup. No real token read yet.
- Local suite **165 passed**, including synthetic Node identity/HTTP tests; VM
  no-data proof and native health/egress guards passed. At 02:11:53 UTC stage-added
  ~1.08 MiB, UI ~0.40 GiB, rehearsal ~4.00 GiB, host free ~79.62 GiB. Native HTTP
  unchanged Dalali/Dulaman 503, Demo/ERP 200. No package/image downloads, source
  pause/schema/data change, public route, production cutover, decryption or cleanup.
- STAGING_READINESS_RESULT.md records private services/evidence and next account
  action. Clone-capable reverse action gateway, reviewer login/roles, new tunnel
  credential/Access audit and authenticated readiness tests remain before exposure.

## Entry 070 — token-entry error reported; manual connector audit read-only

- Operator reports hidden paste errors and running foreground cloudflared with
  --token; location/outcome not yet known. No raw token shared. Advised stopping
  only that foreground command, never the shared cloudflared service; no process
  was killed or service changed by the agent.
- Read-only VM audit 02:31:11 UTC: only original shared connector active, managed
  stage connector inactive, no stage credential/receipt. No process argv, token
  file/secret, customer body, cookie or redirect query read/exported.
- Staging DNS now resolves; anonymous public HEAD returned 403, not expected-team
  redirect. Account route/Access protection not established from this observation;
  do not claim successful staging publication or relax policy to fix an unknown
  error. Need execution location and sanitized exact error. No validation change,
  credential overwrite, token rotation, new connector or route action performed.

## Entry 071 — token rejection narrowed to safe diagnostics; validation unchanged

- Operator confirmed manual connector ran on workstation and supplied only the
  generic helper error for requested UUID 9388ab42-7fa1-429c-a21b-bca54d301486.
  No real token supplied. Different earlier screenshot UUID noted, not assumed
  to establish the actual cause of rejection.
- Reviewed installed-version public cloudflared parser/schema. Existing helper
  overgeneralized all input failures; added safe error categories for clipboard,
  base64/JSON, UUID mismatch and account/secret format/length. Actual UUIDs may be
  printed only in canonical form; token/payload/account/secret/decoder contents
  never printed. Existing validation/protected-tunnel/no-overwrite rules retained.
- Local tests **167 passed**, including new diagnostic/privacy/unchanged-validation
  cases. Updated only exact-hash-owned VM helper, retained original root-private
  copy, verified replacement and unchanged gateway identity/HTTP sources. Protected
  native health and egress passed. Requested UUID not in shared connector config.
- No real credential or process argv read, token stored/overwritten/rotated,
  connector started, route/service changed or public/clone data connected. Need
  one operator hidden-input retry; share only safe new category/error, never token.

## Entry 072 — exact-32 helper bug corrected to documented minimum

- Operator supplied safe `[secret-length]` diagnostic only. Helper incorrectly
  required exactly 32 decoded secret bytes. Public Cloudflare API documentation
  specifies at least 32; installed-version public Go parser uses []byte without
  that exact-size assumption. Actual token/decoded length never read by the agent.
- Corrected exact-32 to minimum-32 within unchanged 16-KiB token cap; account,
  encoding, exact UUID, shared-tunnel, root-only/no-overwrite/storage and no-start
  safeguards retained. No Cloudflare Access or service/security policy changed.
- Expanded synthetic regression/boundary cases: longer secrets accepted, shorter/
  over-cap secrets and wrong/shared UUIDs denied. Local suite **168 passed** and
  whitespace checks clean. VM update exact-hash-owned, prior helper retained,
  unchanged gateway sources and protected native health/egress verified.
- No credential read/stored, connector started, service/route changed, clone/public
  data connected or cleanup. Operator must retry hidden entry; no token in chat.

## Entry 073 — private staging credential stored; safe preflight verified

- Operator reported successful hidden credential entry for new UUID
  9388ab42-7fa1-429c-a21b-bca54d301486, services_started=false. VM verified root-only
  regular-file metadata and safe exact-UUID receipt, no token content or argv read.
- Source/unit hashes and protected native health/egress passed; loopback anonymous
  denial remains 403, signing keys fresh. New connector inactive, approval marker
  absent, metrics port unused, aggregate headroom adequate; clone upstream disabled.
- Remote-managed tunnel routing must be confirmed before startup: only d-stage,
  all paths, guarded 127.0.0.1:18089, Protect with Access team/AUD enabled. A token
  alone cannot prove safe remote routes. No service/route/Access/DNS changes made;
  do not delete/overwrite a conflicting route or point directly at frontend 18088.

## Entry 074 — operator saved protected route; separate readiness connector healthy

- Operator configured the new hostname to 127.0.0.1:18089, enabled Protect with
  Access and committed AUD tag; screenshot confirmed route saved successfully.
  Existing exact guarded-staging approval covers starting only the new connector.
- Added dry-run-default stage_connect.py requiring --route-confirmed, guarded
  remote activation/rollback and pure received-config validator. Token file only
  stat'ed; systemd reads it privately. No token/argv/customer response exported.
- Preflight exact source/unit/credential/gateway/key/resource/native checks passed.
  New approval marker created and only ndf-dalali-stage-tunnel.service started and
  enabled; existing cloudflared process identity/config/service unchanged.
- Running metrics /config version 2 audited on VM: exactly one all-path d-stage
  route to guarded 18089, required Access/ndftech/single expected AUD, fallback404,
  no private routing or proxy/TLS bypass. /ready 200. Actual UID/caps/cgroup/memory/
  swap/CPU limits and loopback-only metrics verified; native health/egress retained.
- Public anonymous HEAD 403; no team redirect, so do not claim exact edge policy
  reason or permitted login. Operator must test normal-browser login to static
  readiness next. Clone forwarding remains hard-disabled; no app credentials or
  customer data connected. No source sync, native changes, production switch,
  write-open, archive decryption, package download or cleanup.
- Local suite **171 passed**, route negative/mutation and activation gates included;
  git diff --check clean. STAGING_CONNECTOR_RESULT.md records result and limits.

## Entry 075 — unexpected native-looking 404; read-only routing diagnosis

- Operator's public-browser screenshot showed d-stage missing-site HTML, not
  readiness. Reverified connector config v2/ready200, exact guarded route and
  local marked static gateway denial. Native Bench unknown-host 8081 request
  reproduced the exact missing-site message, without existing-site/customer data.
- Proxied public DNS hides CNAME target; public anonymous HEAD/GET metadata 403
  without gateway marker/team redirect does not establish the edge cause. Possible
  DNS/route override or stale browser response needs account/browser checks.
- Request exact d-stage DNS type/target/proxy state and fresh private-window test;
  expected new tunnel CNAME is known. No DNS overwrite, new native site, host
  Nginx/Access relaxation, service stop/restart or clone connection performed.
  Existing protected health/egress checks passed; no raw bodies/cookies/tokens
  exported. Readiness permitted login still pending.

## Entry 076 — Nginx site-name question answered without routing to production

- Operator proposed forwarding d-stage via global Nginx to Dalali. Read-only
  active Nginx audit validates host config, native Dalali vhost present and d-stage
  absent. Intended separate tunnel→guarded18089 path bypasses global Nginx.
- Private clone frontend running with expected readonly config; already sets
  X-Frappe-Site-Name to dalali-rehearsal.invalid. Native Dalali remains production
  source in maintenance. Public readiness gateway still has no Frappe forwarding.
- Do not map staging to native production, add an unguarded host vhost or relax
  Access to hide the unintended route. Need exact account DNS target/overrides;
  no config/route/service/maintenance changes made. Protected health/egress passed.

## Entry 077 — named staging copy requested in isolated Docker Bench

- Operator explicitly chose isolated Docker Bench and a copy of Dalali data for
  new d-stage.ndftechlabs.com, not native Bench. No existing production rename or
  live restore inferred. Public missing-site/DNS investigation remains unresolved.
- Read-only feasibility: new site/DB/user absent, old clone retained, captured files
  ~360 MiB and current migrated clone DB allocation ~95 MiB; ~4 GiB remains inside
  original 8-GiB rehearsal cap and ~79.62 GiB host free. No snapshot/pause/import yet.
- NAMED_STAGE_SITE_PLAN.md proposes new DB/user/site from already migrated clone,
  table/sequence/file/crypto parity, distinct private credentials/safety overlays,
  no migration/upgrade or native source operation, original clone retained.
- Proposed bounded clone-only web/Socket.IO snapshot pause (120s deadline) and
  new application-data phase max1 GiB within unchanged total caps. Separate actual
  dataset accounting needed so stage64MiB/UI1GiB tool guards remain meaningful;
  no blanket limit increase or cleanup. Exact proposal requires approval first.

## Entry 078 — named-copy proposal paused; exact staging DNS cause identified

- Operator answered **No, wait** to the separate named-site-copy proposal. No new
  site/database/user, checkpoint, clone pause or copy created. Subsequent public
  access discussion returns to using existing dalali-rehearsal.invalid behind the
  approved guarded d-stage hostname; readiness/public clone gate remains in place.
- Operator DNS screenshot confirms exact d-stage record is proxied **A →
  161.97.69.61**, native origin, not tunnel CNAME. Explains observed native missing
  site HTML; proxied A status did not route through the correctly configured tunnel.
- Scoped operator correction: edit only d-stage to proxied CNAME for new UUID
  9388ab42-7fa1-429c-a21b-bca54d301486.cfargotunnel.com, TTL Auto. No deletions,
  production/mail/root records, host Nginx/native site mapping or raw18088 bypass.
  Agent has not applied DNS changes; readiness login still to be verified and
  clone forwarding remains disabled. No data/credential exports or cleanup.

## Entry 079 — signed-in readiness confirmed; Guest preview reverse guard prepared

- Operator corrected staging DNS, supplied signed-in static readiness screenshot,
  then requested connecting existing clone. No new site/data copy (paused proposal).
- Prepared strict Guest-only reverse policy/server: exact auth boundary retained,
  no cookies/SID/password/Authorization/Access assertions to upstream, no upstream
  session cookies back. Only root/compiled assets/raster images/website_script and
  three reviewed no-argument reads; canonical GET, no broad fallback website/API.
  Login/Desk/cart/private files/unreviewed methods and business mutations denied.
- Existing clone site header preserved via private frontend; public hostname/HTTPS
  mapping retained. Clone-only built-in maintenance read-only config proposed as
  independent SQL fence; original config/code retained. No schema/app upgrade.
- 32 synthetic Node checks passed locally and VM network-none before clone data.
  Initial reporter-format mismatch stopped before overlay; strict all32/pass32/
  fail0/cancel0/skip0 parser fixed/tested, no waived or skipped checks.

## Entry 080 — stale staging mapping surfaced; scoped failure recovery verified

- Private real-Guest homepage/banner,8-assets,ping/denial proof and SQL read-only
  zero-row-update denial passed. Gateway restart changed private IP and stale
  recorded loopback mapping triggered fail-closed activation/rollback handling.
- Original clone site config and readiness entry/runtime restored exactly. Map
  verification could not complete, so only new staging connector stopped; native
  services/data unchanged. Inspected only safe hashes/metadata, no real bearer or
  customer page/record exports.
- Added explicit stage_mapping.py reconciliation, bound to the exact retained ID/
  spec, same gateway/container/image/private network and parent127.0.0.1:18089 /
  child18089. Retargeted only that owned object, retained mapping history; frontend
  18088 and all other mappings untouched. No file/deployment/volume cleanup.
- Verified original configs/readiness403 first, reconnected only stage service,
  then retried with explicit --retry-rolled-back --repair-owned-mapping. Unknown
  mappings/broad binds/different containers/network/ports remain hard failures.

## Entry 081 — existing clone Guest homepage connected, broader review still gated

- Activated 2026-10-26 04:19:29 UTC; exact guard bundle SHA-256
  fdc1406ea7ed635977f5d4a538b89e2601e7385faebb7913a0399cb07e782f0a.
  Repeated network-none32-check proof, actual Guest SQL read-only denial and private
  origin8-assets/ping/action-denial proof. No real credentials/cookies or public
  customer-page diagnostics used/exported. Public anonymous still403.
- Clone site now maintenance1/allow_reads_during_maintenance1/read_from_replica0,
  HTTPS staging host_name and existing scheduler/mail/webhook safety retained.
  Original private config retained. Only isolated clone web/gateway restarted;
  host Nginx/shared connector/native maintenance/PIDs/services untouched.
- Guest website now reaches existing dalali-rehearsal.invalid through Access and
  guarded18089. Visible staging banner/no-store/noindex, unsafe/external redirects
  and raw5xx diagnostics blocked. No Frappe login/Desk/private/cart/unreviewed routes
  or business writes. Homepage recommendations remain denied for implicit writes;
  full UI/business acceptance is not claimed. Operator browser confirmation pending.
- Local suite **177 passed**; Node syntax/git diff --check clean. 04:21 receipt:
  stage~0.89MiB, UI~0.40GiB, total rehearsal~4.00GiB, free~79.62GiB; budgets unchanged.
  Native HTTP still Dalali503/Dulaman503/Demo200/ERP200. No new DB/site/data copy,
  source pause/sync/schema change, package download, archive decryption, production
  cutover/write-open, provider side effects or cleanup. Result scope documented in
  STAGING_GUEST_REVIEW_RESULT.md.
