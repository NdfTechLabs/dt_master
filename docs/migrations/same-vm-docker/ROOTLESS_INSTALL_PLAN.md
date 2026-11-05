# Rootless Docker foundation — approved and applied

Completed results and deviations are recorded in ROOTLESS_FOUNDATION_RESULT.md.
This file preserves the reviewed plan; it is not authorization to replay setup.

This stage creates an isolated Docker foundation with **synthetic data only**.
It does not migrate/restore/delete a site, change routing, clear maintenance mode,
decrypt the archive or download another backup.

## Fresh preflight

- SSH to expected host works; one intervening Cloudflare app-info timeout was
  transient. Subsequent metadata verification succeeded without another login.
- All seven Bench processes and checked native web/mail/database services active.
- Dalali and Dulaman remain in maintenance; demo and ERP are not in maintenance.
- About 6.6 GiB RAM available, 125 GiB disk free; port 18088 unused.
- WireGuard route present, host FORWARD policy ACCEPT.
- Docker/rootless tools and deployment user are still absent.
- cgroup v2 cpu/memory/pids controllers present; AppArmor userns restriction on.
- Cached Ubuntu prerequisite simulation: four new packages, no upgrades/removals.

## Proposed changes

### 1. Record baseline, refresh/simulate narrowly

Save current protected service/configuration/routing/resource evidence in private
operational storage. Do not automatically accept unexplained baseline differences.
Obtain current signed Ubuntu prerequisite metadata and re-simulate before install.
Abort if additional package removals/upgrades or unexpected service changes would
be required. No distribution upgrade, autoremove or global Docker apt repository.

### 2. Install only rootless prerequisites

Expected Ubuntu packages: `uidmap`, `slirp4netns`, `libsubid4`, `libslirp0`.
`dbus-user-session` is already installed. Use the reviewed/pinned resolution and
prevent unrelated automatic needrestart actions for this transaction, not by
changing global restart policy. Do not install Docker's rootful service packages.

### 3. Dedicated account, isolation and resources

- Create `dt-containers` with no password login, no sudo privileges and no
  production groups. Do not alter the existing frappe account or its groups.
- Allocate non-overlapping subordinate UID/GID ranges; record actual UID/ranges.
- Keep Docker data/configuration under its private home; do not mount production
  Bench/database/Redis directories or Docker sockets into test containers.
- Aggregate dedicated user-slice budget: **2 CPU equivalents, 3 GiB memory**,
  bounded swap/processes and rotated logs. Demonstrate effective cgroup limits.
  There is no proven hard disk quota; monitor disk and stop staging on low space.
- Enable linger/user service only for this new account. Existing system service
  restarts or changing other users' linger/services are not part of this stage.

### 4. Verified, versioned binaries without rootful installation

The public Docker Release signature and package-index SHA-256 were verified using
the pinned Docker signing key. Exact package paths/sizes/hashes are in
`rootless-packages.json`.

- Docker/CLI/rootless tools: **29.6.1**.
- Containerd: **2.4.2**, satisfying Engine's declared dependency >=2.1.5.
- Compose: **5.3.1**.
- Download these packages to the VM, check each digest, and extract into private
  staging. Do **not** dpkg-install Docker/containerd packages or execute their
  maintainer scripts, which could create/start rootful services.
- Copy only reviewed required binaries/scripts into a new root-owned, versioned
  `/opt/ndf-rootless/...` tree restricted to the deployment group. No existing
  `/usr/bin` replacement, system Docker socket or rootful Docker unit.
- Keep binaries on root-owned non-writable parent paths so a path-specific
  AppArmor exception cannot be replaced by a deployment-user-controlled executable.

### 5. Narrow AppArmor allowance and rootless user daemon

If required, add/load **one new rootlesskit-specific profile** for that pinned
binary path. Do not disable AppArmor/global userns restrictions or reload unrelated
profiles. Stop if a safe targeted profile isn't supported.

Configure only the new user's Docker service/socket and cgroup delegation. Review
packaged rootless setup scripts before invoking them. Confirm daemon UID, rootless
security options, data root, socket/context and namespace ownership. Rootless mode
reduces daemon privilege; it does not make shared-kernel isolation risk-free or
guarantee that every rootful security feature is available.

### 6. Tiny synthetic checks, no customer data

- Pull a tiny approved public test image (a few MB), not Frappe/ERPNext yet.
- Verify CPU/memory/process limits and restart behavior without an unbounded load
  test. If limits don't apply, stop rather than run unconstrained containers.
- Verify internal network isolation and, if needed, temporary HTTP on
  `127.0.0.1:18088` only. No public proxy or database/Redis publishing.
- Recheck native processes, site baselines, mail listeners, SSH/VPN, forwarding
  policy and protected config hashes. Do not modify native Nginx/Supervisor/MariaDB.
- Stop only our synthetic test containers after checking them. Retain deployment
  artifacts/logs; do not delete old sites, databases, deployments or volumes.

## Bandwidth

- Verified Docker/containerd/Compose package payload total: **81,728,620 bytes**.
- Allow additional Ubuntu package indexes/prerequisites and a tiny test image;
  proposed cap for this foundation stage: **150 MB downloaded directly by the VM**.
  If the projected total exceeds that, stop and ask again.
- Only commands/status metadata travel to the workstation; no backup or app-source
  download. Do not decrypt the already-downloaded archive.
- Later Frappe base candidate alone is **981,159,820 compressed bytes**. Custom
  dependencies/images add more. That later stage needs its own bandwidth/resource
  approval and version-parity checks; no image pull is included here.

## Failure / rollback policy

If a step fails, stop and inspect the recorded state before retrying. Stop only
the new rootless service/test containers if necessary. Preserve logs/new storage;
do not remove the account, its volumes, binaries, backups or packages automatically.
Any removal, live-data overwrite or traffic switch requires explicit approval.
Never replace the plan with rootful Docker, a global firewall restore, a global
AppArmor relaxation or a restart of native production services.

## Still separately gated

- Real data copies, their exact scope/writer-fencing window, and restore into
  **new** private storage. Permission for server-side copies is not permission to
  overwrite production data or silently take an unreviewed pause.
- Build location/resources: the original off-VM build proposal must be reviewed
  against the new no-backup-download/data-cost policy before implementation.
- Native master automation mapping, application validation, public cutover,
  write-open, cleanup/drop and any move of mail or the other production sites.
