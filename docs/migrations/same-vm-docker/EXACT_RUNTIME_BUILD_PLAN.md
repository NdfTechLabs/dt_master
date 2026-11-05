# Exact-runtime evaluation image — preparation passed, explicit build go-ahead

**Subsequent results:** corrected image passed strict no-data checks and the
isolated technical rehearsal. See IMAGE_VALIDATION_RESULT.md/REHEARSAL_RESULT.md
for actual image/schema/runtime-copy outcomes. Pending/future language below
preserves the reviewed build plan, not current status or permission to replay it.

**Latest steering: build explicitly approved.** After notification of successful
preparation, the operator directed **Build it and also inform me of the process**.
The launched build job uses --approve-build to archive the prior hold, bind
approval to the context manifest and recheck it before building. No customer-data
mount/restore or cutover is implied. Image build and no-data validation results
must still pass; see CHANGELOG entry 034.

**Preparation result: passed.** The full v2 context matches its
recorded filtered inputs, modes, links and recipe; all 51 scanner findings are
covered by exact reviewed file hashes, with no unresolved findings. No image has
been accepted as a production image, and no customer data mounted. See CHANGELOG
entry 033 for preparation and entry 034 for the subsequent build authorization.

**Historical preparation-only hold.** The operator requested notification before
the image build. The running preparation uses --prepare-only, with a durable VM
hold-before-build marker. Finish checks and report; require separate go-ahead to
build. That hold was honored until successful preparation and the explicit
subsequent build go-ahead recorded above; it is not silently discarded.

The operator selected **Approve download and build** for the scope below. Actual
outcomes and any stop conditions are recorded separately in CHANGELOG.md; this is
not permission to replay a completed/partial build or broaden the scope.

Execution status: Buildx installed; private 21-app source snapshot verified.
Context v1 stopped on links to original app dependency paths and Python support
files. The operator requested handling those links and continuation. Context v1
is retained untouched; v2 uses the reviewed mapping below. No image acceptance is
claimed until credential scan, build and validation receipts pass.

### Reviewed v2 link policy

- Absolute `/home/frappe/bench/apps/...` dependency links map lexically into the
  already verified snapshot before resolution, then become relative links within
  the filtered context. Do not read live dependency targets through these links.
- Native application/CLI interpreter aliases resolving to `/usr/bin/python3.14`
  reconstruct that exact image-internal interpreter path, including the CLI's
  Unicode alias. Other external command targets are not allowed.
- Copy only root-owned regular `/etc/python3.14/sitecustomize.py` (observed empty)
  and `/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0` into private support storage,
  bracketing the copy with hashes. Sitecustomize's image link retains its /etc
  target, supplied by the recipe. The config libpython link targets the private
  service-scoped compatibility library in the image. Its target was absent from
  the probe copies, **not absent from native production**.
- No generic external-symlink exception, recursive host-library copy or live mount.
  Excluded/missing/unknown targets still stop the build. Write private link mapping
  and support-copy hash receipts. Retained v1 and new v2 count against the same
  original disk budget; neither is automatically deleted or overwritten.

## Decision and evidence

The operator chose **Match existing**, not newer Python/Node patches. The cached
vendor image is a filesystem foundation only, not source/runtime parity proof.
There is still no custom image build, customer-data restore or traffic change.

The private-copy runtime probe succeeded without downloads:

| Component | Probe v2 |
|---|---|
| Python | 3.14.3 |
| Node | 24.14.0 |
| OpenSSL used by native Python | 3.0.13 |
| mariadb / mariadb-dump | 10.11.14 |
| Imports | PyMySQL, MySQLdb, Pillow, cryptography, Redis, RQ, Gunicorn, NumPy, pandas, pyarrow passed |

Probe v1 correctly exposed missing libmysqlclient.so.21 and use of the image's
newer OpenSSL. Probe v2 added private copies of native libmysqlclient.so.21,
libssl.so.3, libcrypto.so.3 and libmariadb.so.3, with LD_LIBRARY_PATH scoped to the
test process. Gevent was absent on native too; it was not installed for testing.
These are smoke checks, not full dependency, PDF or Frappe acceptance.

VM evidence (private, not image inputs):

```text
/var/lib/ndf-rootless-migration/native-runtime-probe.json
/var/lib/ndf-rootless-migration/native-runtime-probe-v2.json
/var/lib/ndf-rootless-migration/native-runtime-probe-v2.log
/opt/ndf-rootless/native-runtime-probe-v1/
```

## Additional download, not yet performed

Read-only inspection confirms `docker buildx` is unavailable. Signed Docker
repository metadata identifies **Buildx 0.38.1**, package **17,800,928 bytes**.
Exact authenticated path/hash are recorded in `build-tool.json`. Recheck the
package size/hash on download; extract, do not system-install the Docker DEB.
Install the plugin in a new root-owned versioned directory, linked only into the
dedicated account's CLI plugin directory; never overwrite an unknown plugin.

Proposed additional transfer cap: **20,000,000 bytes directly to the VM**.
No second base-image pull, package indexes, app clones, pip/npm/yarn installs,
BuildKit image, moving Dockerfile frontend or workstation source/backup transfers.
If another artifact is required, stop and estimate/ask rather than download it.

Use Docker's integrated builder driver. Do not create a docker-container builder
that silently pulls BuildKit. A missing integrated builder is a stop condition,
not authorization for legacy fallback or another download.

## Copy scope and source provenance

1. Recheck protected services/configurations/routes, maintenance states, free disk,
   source revisions and dedicated-user limits. Never reset or clean a worktree.
2. Preserve **all 21 app trees** in a new root-private VM-side source snapshot,
   including Git state and tracked/untracked WIP. Snapshot manifests must include
   file hashes/modes and symlink targets; bracket copying with source comparisons
   and stop on detected changes. This is not an atomic snapshot of active writers.
   Native source trees are read-only inputs; no production mounts in containers.
3. Non-pilot dirty trees observed: appe, crm, csf_ke, helpdesk,
   kenya_compliance_via_slade. Preserve their changes, do not silently substitute
   upstream sources. The 12 pilot revisions still match source-manifest.json.
4. Produce a **separate filtered build context**, never put the private snapshot
   itself in the builder context. Include dependency trees/native modules needed
   for offline operation, not just Git-tracked files. Record excluded names/counts
   privately and stop on unexplained exclusions that affect runtime parity.
5. Exclude `.git` (directories and files), credential/config directories such as
   `.ssh`, `.gnupg`, `.aws`, `.azure`, `.config`, plus `.env`/`.env.*`, `.npmrc`,
   `npmrc`, `pip.conf`, `.pypirc`, `.netrc`, `direct_url.json`, private-key files,
   site/common config, backup/dump/archive files and caches/logs. Do not display
   their contents. Scan remaining files for credential/private-key indicators;
   suspected embedded secrets block the build for private review. Filename
   exclusions alone do not prove that source code is free of credentials.
6. Reject devices/FIFOs/sockets and symlinks escaping the approved staged roots.
   Explicitly reconstruct reviewed runtime interpreter symlinks when needed;
   never follow an arbitrary source symlink into production data or credentials.
7. Copy only interpreter `/usr/bin/python3.14` and stdlib `/usr/lib/python3.14`,
   application env, Bench CLI env, Node prefix and four compatibility libraries/
   two client executables. **Never copy all /usr.** Filter runtime copies too.
8. Preserve `/home/frappe/bench` inside the image: all 21 native editable-install
   .pth files reference that path. Preserve `/home/frappe/.venv` and the exact
   Node prefix as well. Do not overlay native sources onto the vendor's different
   Bench path and assume imports resolve correctly.
9. Do not copy any native `sites/` tree, site configuration, logs, DB, public or
   private customer files. Static asset integration (observed assets ~46 MB) and
   PDF/font behavior remain later acceptance checks; no customer content belongs
   in this evaluation image. Record sanitized provenance beside the image.

The source snapshot may contain sensitive files and Git credentials; keep it
root-private outside the Docker account/context. Filtered source/images are still
private application intellectual property: no registry push or export.

## Draft recipe and isolated build

`exact-runtime/Dockerfile` is an **evaluation-only draft**, not a deployment image
or executable build helper. Its named COPY inputs must be generated by the
validated filter above. Do not run it against a live Bench or the raw snapshot.

- FROM is the already-cached immutable digest; `--pull=false`.
- No external `# syntax` frontend, ADD URLs, package manager or RUN instruction.
- Build with `--network=none`, Docker integrated builder, local tag only, no push.
- Use an explicitly selected dedicated-account socket/context, inherited aggregate
  user-slice limits, conservative build parallelism and monitored free disk.
- Override vendor working directory, entrypoint and default startup. Default
  command only prints Python's version: no server, worker, scheduler or site init.
- Compatibility-library environment is applied by `ndf-native-exec`, not a global
  Dockerfile LD_LIBRARY_PATH. Vendor Nginx/other Debian processes must not be run
  through this wrapper. Separate future frontend/runtime roles require review.
- Base still contains newer runtime files/vendor sources and inherited volumes
  for the vendor Bench's sites/logs paths. The evaluation image is not a claim of
  an entirely Ubuntu-matched OS or of removing those files. Validation must assert
  which executable/source paths are actually used. No vendor startup is allowed.

## Resources and stop conditions

Read-only inventory: app trees ~8.10 GB, of which ~4.41 GB is Git metadata;
private runtime probe copies ~1.69 GB; host free disk ~126.51 GB (decimal).
Filtered source footprint is an estimate, not a measured final context yet.

Proposed stage cap: **35 GiB additional disk** for the private source snapshot,
filtered context, plugin, image layers and build cache combined; **60 GiB minimum
host free disk** throughout. Count retained/partial attempts too. Monitor both
allocated and apparent sizes; abort before either disk guard is reached. No hard
filesystem quota is claimed. No automatic prune/removal on failure.

All builder/validation processes stay under the existing **2-CPU / 3-GiB aggregate
limit**, 256-MiB swap and TasksMax 1024. Root-side copying is sequential, nice 15 /
idle I/O priority; not inside that slice, so monitor its resource use separately.
No host service restart, global library change, firewall change or maintenance
change. Unexpected source drift, secrets, OOM, isolation failure, native health
change or extra downloads => stop, retain logs and request a decision.

## No-data acceptance for this stage

Run temporary containers `--pull=never --network=none --read-only --cap-drop=ALL`
with no-new-privileges and bounded resources; only isolated tmpfs scratch if
required. No live host mounts, published ports, DB/Redis connections or site data.

Verify exact Python/Node/Yarn and MariaDB client versions, expected OpenSSL,
package inventory against native, imports including Frappe/ERPNext and pilot
modules with `__file__` under `/home/frappe/bench/apps`, Bench CLI availability,
and shared-library resolution. Native package inventory previously had 231 names
versus base 164, 10 differing versions and 67 native-only; do not resolve this by
installing newer packages. Record native-matching dependency gaps separately.

PDF/fonts, asset serving/build parity, real site bootstrap, application behavior,
data restore and automation ownership are **not proven** by an import-only image.
If an import needs services/site config, do not connect to native production to
make it pass; report the remaining gate. Finish with protected-state comparison.

## Explicitly excluded

No archive decryption, backup download, DB dump/lock, customer-data copy/restore,
old deployment/volume deletion, live overwrite, registry publication, public
route switch or reopening Dalali. Other production services stay unchanged.
Approval for this build is not approval for any of those operations.
