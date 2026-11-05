# Named Docker Bench staging copy — approval pending

**Paused by operator:** answered **No, wait**. No named-site/database/user copy
or clone snapshot/pause was performed. Later explicitly chose to connect the
existing clone instead; see STAGING_GUEST_REVIEW_RESULT.md. This proposed copy
is not authorized by that later connection request.

Operator explicitly requested a site named **d-stage.ndftechlabs.com** in the
**isolated Docker Bench**, containing a copy of Dalali data. Not the native Bench,
not an empty app installation and not a production cutover.

## Read-only feasibility (2026-10-26 03:25 UTC)

- Existing isolated migrated Dalali database: 1,030 objects, allocated data/index
  estimate **99,744,712 bytes (~95 MiB)**; encryption/schema/runtime already tested.
- Captured attachments: **5,855 files**, **377,619,987 bytes (~360 MiB)**.
- Requested new site path, `ndf_d_stage_v1` DB and user all absent.
- Old `dalali-rehearsal.invalid` site remains present; source/native untouched.
- Host free ~79.62 GiB; roughly 4 GiB remains within the original 8-GiB rehearsal
  disk cap. No package/image/browser download is required.

## Proposed exact creation scope

1. Copy the **existing migrated isolated clone**, not current production and not
   the downloaded encrypted archive. This is the earlier rehearsal dataset plus
   marked synthetic fixtures/password changes, not a fresh/final source sync.
2. Create only a **new site directory**, **new database** and **new private DB user**
   (`ndf_d_stage_v1`) in the owned MariaDB server **65001**. Refuse any pre-existing
   target/path/user, partial attempted restore or identity drift; no overwrite,
   rename, deletion or automatic retry of partial restores.
3. Briefly stop only the original clone's labelled web/Socket.IO processes while
   capturing a new VM-private SQL/count/sequence checkpoint. No clone workers are
   running. Use a bounded clone-only snapshot window, aim <60 seconds, deadline
   **120 seconds**, and always resume the original clone roles on failure. Native
   shared services/processes/site maintenance never change. No new native lock,
   backup or pause is authorized/needed.
4. Restore into the new empty DB only; compare all copied table row counts,
   sequence boundaries, important schema and app versions before staging-only
   safety/password overlays. Keep exact existing runtime/app image. **No bench
   migrate/package upgrade is planned**, since this source is already migrated.
5. Copy attachments from the private clone, preserving all hashes/permissions;
   stream/copy without making another full temporary file tree. Retain old clone
   and existing source/checkpoint artifacts. Keep the encryption key private and
   verify encrypted fields in the new DB without exporting plaintext or keys.
6. New site config uses only private DB/Redis endpoints and distinct DB credentials;
   `maintenance_mode=1`, scheduler paused, email queues/webhooks muted, developer
   mode off, external network/default route absent. Rotate only the new site's
   clone Administrator password privately; do not publish/reuse native/test-admin
   passwords. Do not change the original clone's configuration or database data.
7. Verify Bench can discover exactly the new site by name and import the existing
   apps; no host Nginx changes, new public port, privilege/security-profile expansion,
   app worker/scheduler or public application connection as part of site creation.

## Disk accounting (requires approval with creation)

- Expected new application data/checkpoint cost: roughly **0.5–0.7 GiB**; hard new
  named-site phase cap **1 GiB**, still within the original **8-GiB** rehearsal cap
  and **60-GiB host-free floor**. Existing aggregate CPU/RAM/swap/tasks unchanged.
- Keep stage-code/key/receipt **64-MiB** and UI-tool **1-GiB** subcaps unchanged.
  Their current conservative free-space-delta guards count all host allocations;
  the named site's separately measured/accounted application data must therefore
  be excluded from those *tool/code* deltas, without excluding it from the total
  8-GiB guard. Record actual DB/site/checkpoint allocation and bounded explicit
  offset, never a blanket free-space reset or speculative unlimited allowance.
- Account key-refresh budget for this reviewed new dataset so a legitimate file
  copy does not accidentally stop public-key refresh. Bound all new data by the
  new phase and aggregate guards; do not silently raise any overall limit or prune
  retained data to make the copy fit. Any additional download requires approval.

## Public routing remains a separate unresolved gate

Creation of a correctly named Docker site does **not** prove the public request is
using its tunnel. Existing public 404 matches native Bench; proxied DNS hides the
record target. Need the exact d-stage Cloudflare DNS type/target/proxy status and
any overrides, plus successful permitted readiness login. Do not add the hostname
to native Bench/global Nginx or bypass guarded 18089 to mask that routing issue.

The public gateway remains readiness-only and has no clone forwarding. After
creation, a reviewed clone-capable reverse action gateway and clone-only reviewer
account/roles still need exact-code no-data proof before customer pages are
connected. Existing app bugs can be reviewed then; no checkout/financial posting,
payments/messages/webhooks/provider actions, final sync, cutover or write-open.

**Status: read-only feasibility passed. No named site/database/user or clone
checkpoint created yet. Creation/accounting/pause proposal awaiting approval.**
