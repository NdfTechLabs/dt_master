# Dalali guest website connected behind Access — read-only preview

**https://d-stage.ndftechlabs.com/** now connects to the existing isolated
**dalali-rehearsal.invalid** website through the authenticated action gateway.
No new site/database/data copy was created. Native production remains untouched
and in maintenance. This is a guest homepage preview, **not full Desk/business
acceptance, production cutover or write-open**.

## Prerequisite and exact scope

Operator corrected the staging DNS record to the new tunnel and supplied a browser
screenshot of the signed-in static readiness page, then explicitly requested
connecting the clone. Exact same email/issuer/AUD/signature/expiry/key-freshness
checks remain required for every request, including assets. App bugs were accepted
as review findings, not permission to run unsafe actions.

Phase one forwards **only as Guest**. No Frappe passwords or reviewer-account
creation are needed. The proxy never forwards client cookies/SID, Authorization,
Access bearer/email headers, caller site/forwarding headers or arbitrary bodies.
Upstream Set-Cookie is not forwarded; Cloudflare manages its own unchanged cookie.

| Allowed | Denied |
|---|---|
| Root homepage, staging robots.txt | Frappe login/logout/Desk and all business saves |
| Reviewed compiled assets and raster public images | Private files, REST document/resource APIs, financial operations |
| Frozen website_script.js (settings reads) | Dynamic/unreviewed pages, product/category/search/cart/checkout routes |
| No-argument ping, logged-user and cached cart-enabled reads | Recommendations (implicit Customer/Contact/party writes), tracking/history/preferences writes |
| Canonical upstream GET for those reviewed no-argument RPC reads | Custom query/command dispatch, duplicate/ambiguous parameters, method overrides, arbitrary CONNECT/WebSocket traffic |

Guest logged-user reads can legitimately return application permission denial;
no synthetic successful business response is substituted. Recommendations and
other withheld features may leave homepage sections incomplete. Login, private
data, product navigation/search, realtime and full role/Desk review require a later
reviewed scope, not a blanket allowlist expansion.

## Layered read-only mode and host mapping

- Outer public hostname stays **d-stage.ndftechlabs.com**. Fixed internal upstream
  is **frontend:8080**; private Nginx already selects **dalali-rehearsal.invalid**.
  No global/native Nginx vhost, raw18088 public route, source-site rename or new DB.
- Clone-only config now has **maintenance_mode=1** and
  **allow_reads_during_maintenance=1**, using frozen Frappe's built-in read-only
  transactions, not a new app/code version. `read_from_replica=0` prevents external
  replica selection. Public host_name is HTTPS staging; cookie_domain remains None.
- Scheduler/email/webhook/developer safety overlays remain in place, no worker or
  scheduler started, app network/default-route/egress restrictions unchanged.
- Direct clone request initialization verified **Guest**, private DB server65001
  and read-only flag. A **zero-row** synthetic UPDATE was rejected by SQL read-only
  mode; no business rows affected. Thus HTTP writes are fenced independently of
  the reverse-proxy allowlist. Earlier private Note-save acceptance evidence is
  historical; writes are intentionally disabled during this preview.
- Original clone config is retained mode600 inside the existing clone's
  `private/backups/ndf-site-config-before-guest-review-v1.json`. Readiness gateway
  code/runtime originals retained root-private under `stage-v1/`. Config rollback
  checks exact before/after hashes and refuses unrelated drift; no retained files
  are deleted. No source database/schema or app/runtime upgrade occurred.

Responses carry a visible **DALALI STAGING — read-only website preview** banner,
no-store/private cache headers, noindex/noarchive/nofollow and no-referrer/nosniff.
Existing private clone CSP is preserved. External/provider redirects are denied;
approved internal redirects map to HTTPS staging. Backend 5xx bodies are replaced
with a static error at the same failing status, not a success or private trace.
Normal reviewers still must avoid external/provider navigation, as previously
agreed; no browser-wide network sandbox is claimed for their ordinary browser.

## Proof and execution

- New stage_policy.cjs/stage_reverse.cjs implement a reverse gateway, not the old
  automated browser proxy's broad fallback-GET behavior. Explicit guest review
  mode/site is required; origin_enabled alone cannot select arbitrary upstreams.
- Local **177-test Python suite** passed, including **32 synthetic Node identity,
  readiness and reverse HTTP checks**. Node syntax and git diff --check passed.
- Exact-source 32-check proof repeated VM-side in a network-none constrained
  container, no real Access config/credentials or clone data mounted.
- Then a separate private-network VM-only Guest origin proof used synthetic keys
  on a container-local test listener, no public port/real bearer/cookie/password.
  It loaded real clone homepage with banner, **8 assets**, and ping; all selected
  unsafe routes and synthetic financial insert were denied before forwarding.
  Raw HTML, asset URLs, records, credentials and console bodies stayed on VM.
- Successful activation: **2026-10-26 04:19:29 UTC**, bundle SHA-256
  `fdc1406ea7ed635977f5d4a538b89e2601e7385faebb7913a0399cb07e782f0a`.
  Public anonymous HEAD remains **403**; actual authorized browser homepage
  confirmation is pending, not inferred from private HTTP proof.

### Guarded failures and recovery were not waived

An initial launch stopped before config changes because the Node reporter used a
different summary format. Parser now accepts TAP/spec/ANSI formatting **only** with
exactly 32 tests passed, zero failures/cancellations/skips; negative parser tests
added. This was a harness issue, not customer data or app failure.

Next attempt passed private read-only and origin proofs, but gateway restart
changed its private IP while the existing RootlessKit mapping still targeted the
previous IP. Activation failed closed. Original clone config/readiness entrypoint
were restored exactly; stale-map validation prevented claiming complete rollback,
so only the new staging connector was stopped. Native services/data were unchanged.

After private inspection, repaired **only the recorded 127.0.0.1:18089 mapping**
to the **same owned gateway ID/image** on its owned internal network. Complete old
mapping ID/spec and fixed parent/child ports verified before removal/re-add; other
mapping18088 retained. History/receipts retained. No unknown mapping, deployment,
volume or file deleted. Readiness config restoration/403 were verified before
reconnecting only the new staging service and retrying publication explicitly.

stage_mapping.py supports this narrowly bounded reconciliation. Publish requires
explicit `--repair-owned-mapping` before any retarget; broad binds, unknown IDs,
different containers/networks/ports, extra mappings or unexpected drift fail closed.
Future restarts still require owned mapping/lifecycle checks; no unverified
production reboot/lifecycle guarantee is made.

## Final receipt, 04:21 UTC

- Guest preview enabled; separate connector active, exact received protected route
  still valid. Native HTTP baseline: **Dalali503 / Demo200 / Dulaman503 / ERP200**.
- Stage-added disk ~**0.89 MiB**, UI-added ~**0.40 GiB**, rehearsal-added ~**4.00
  GiB**, host free ~**79.62 GiB**. Original 64-MiB/1-GiB/8-GiB/60-GiB-floor and
  aggregate CPU/RAM/swap/tasks constraints unchanged. No package/image downloads.
- No new native pause/snapshot, archive download/decryption, site/DB creation,
  financial/customer record modification, provider message/webhook, production
  cutover/write-open or cleanup. Named-site duplication proposal stays paused.

VM evidence under `rehearsal-v1/stage-v1/`: `guest-website-result.json`,
`guest-final-receipt.json`, guarded failure/mapping recovery receipts and private
`state.json.staging`. Local sanitized receipts include
`stage-publish-guest-owned-mapping-20261026.log` and
`stage-guest-final-receipt-20261026.json` in the private healthchecks directory.

## Next

Operator refreshes the signed-in staging URL and confirms the homepage/banner,
without sharing customer screenshots, cookies or tokens. Record app/guard findings
separately. Full UI/business acceptance remains blocked as previously documented;
resolve reviewer roles/read endpoints and required application behavior before
expanding review scope. Final synchronization/writer fencing, production cutover,
write-open and exact-list cleanup remain separately gated.
