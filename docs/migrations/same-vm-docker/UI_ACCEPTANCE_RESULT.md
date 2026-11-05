# Dalali private browser acceptance — partial, blocked

**Business/UI acceptance has not passed. Production was not synchronized or cut
over; native Dalali remains in maintenance.** Automated evidence is not operator
business sign-off. Technical rehearsal results remain separately documented.

## Latest complete guarded run

- VM time: **2026-10-26 01:07:19–01:08:23 UTC**.
- Runner hash prefix `82757e4b8ca3`, proxy hash prefix `c1f1a7a60b38`.
- Exact current runner/proxy passed fresh no-data guard before credentials.
- Result `automated_ui_checks_passed: false`, attempt status `blocked`.
- Node syntax checks, local **161-test suite** and `git diff --check` passed.
- Two observed JavaScript exceptions: `homepage.js:loadHomepageSections`.

| Area | Verified result / limit |
|---|---|
| Isolation | Normal Chrome sandbox, clone-only proxy, synthetic external/native denials and unsafe financial insert denial passed; no unsafe Socket.IO frames observed |
| Login / Desk | Administrator browser login and explicit Desk boot succeeded |
| Lists | Item, Website Item, Sales Order, Sales Invoice, Employee, Loan rendered; each list-data HTTP response completed with 200; list array initialized |
| Existing forms | Item, Website Item, Sales Order and Sales Invoice opened; form dirty flag remained false. This is rendering evidence, not full workflow/business correctness |
| HR / lending forms | Not tested: Employee and Loan list results had no rows. Do not infer existing-record form acceptance or create business records to fill the gap |
| Safe fixture write | Marked synthetic Note changed, saved with a real CDP mouse click on Save, reloaded and content equality verified; only clone fixture data changed |
| Guest | Homepage shell rendered; guest Sales Invoice resource read was denied by the application |
| Storefront | Dynamic homepage recommendations still blocked and erroring; product/category/search acceptance incomplete |
| Roles / files / PDF | Minimally privileged synthetic-user matrix and browser attachment/PDF checks not completed; earlier technical API/file/PDF evidence is not a browser substitute |

List response instrumentation waits for the upstream response to end and the
browser AJAX callbacks to settle. No list total-count parity claim is made here.
Earlier shell-only list checks were strengthened rather than counted as full data
acceptance. Intermediate runs sometimes timed out at the Note click; the latest
complete run verified persistence, but repeatability remains part of acceptance.

## Approved CSP compatibility change

Operator separately selected **Approve clone-only CSP**. Only the private
frontend script-src gained `'unsafe-eval'` for frozen Frappe's `new Function`
template compilation. Original bytes retained root-private at
`ui-v1/nginx-before-approved-csp.conf`; exact config/mount/owned-container identity
verified, clone Nginx tested and gracefully reloaded, exact served header checked.
No container recreated/removed, host Nginx touched or production policy selected.

The first header check raced the asynchronous Nginx reload and restored the
original policy. The bounded follow-up accepts only the exact old/new header
during reload; it succeeded. No wider CSP change was made. See UI_CSP_DECISION.md.
The document's eval-block violation disappeared on Desk and render/add_page
TypeErrors no longer appear in the complete run.

## Denied calls — keep distinct from genuine UI success

Latest run: 187 allowed requests, 4 synthetic origin denials, 16 action denials,
zero denied Socket.IO frames. The 16 include the deliberate unsafe-insert probe.

| Denied method | Count | Handling |
|---|---:|---|
| `frappe.client.insert` | 1 | Expected synthetic financial-insert negative control |
| `frappe.website.doctype.web_page_view.web_page_view.make_view_log` (legacy RPC) | 2 | Tracking write remains blocked |
| `dt_recomendations.api.get_dynamic_sections` (legacy RPC) | 2 | Required storefront behavior remains blocked; reaches party/cart code, implicit writes not cleared |
| `frappe.desk.desk_page.getpage` | 1 | Dynamic Page loading can call page context/hooks; review exact required page before allowing |
| `frappe.client.validate_link_and_fetch` | 1 | Ordinary reviewed nonvirtual types allowed, custom query dispatch denied; this request's type remains outside scope |
| `frappe.model.utils.user_settings.save` | 7 | Preference writes not part of the marked-Note mutation allowance; remain blocked |
| `erpnext.controllers.taxes_and_totals.get_round_off_applicable_accounts` | 2 | Regional dispatch requires deeper review; no business posting occurred |

Route-history deferred inserts remain denied by policy (observed in earlier runs).
No fake successful responses, blanket allowlist expansion or silent waiver of
blocked actions/JavaScript exceptions was used to obtain a passing result.

Further frozen-source review **confirmed potential implicit business writes** in
the recommendation enrichment path: Webshop `get_party()` can save an existing
Customer/Supplier to append a Portal User, or insert a Customer and Contact with
ignored permissions. `_get_cart_quotation()` constructs an unsaved Quotation and
runs missing-value/cart-settings logic; no claim that it necessarily saves the
Quotation is made. `get_product_info_for_website(skip_quotation_creation=True)`
still calls `get_party()` when prices are enabled, so that flag is not a sufficient
read-only boundary. No Guest-only safety assumption is made. The recommendation
endpoint remains denied. A separately reviewed synthetic-fixture scope (no edits
to existing parties), or explicit operator-reviewed exclusion, is required before
that storefront behavior can be counted as accepted; do not authorize arbitrary
Customer/Contact/cart writes merely to remove an error.

Frozen-source reviewed additional reads are narrowly allowed: reportview get_list
for the reviewed types plus nonvirtual List Filter, ordinary validate-link lookup
without a custom query, Item dashboard/reserved-stock SELECTs, Item prices,
accounting dimensions, rounding setting, stock-UOM setting and payment-reference
counts. Posting, stock changes, unreconciliation and regional dispatch are not
authorized by those read allowances. UI code changes require fresh hash-bound
no-data proof before another credentialed run.

## Budgets, privacy and evidence

At the latest receipt: **126,218,348 download bytes**, **420,225,024 bytes (~0.39
GiB) UI-added disk**, **4,281,159,680 bytes (~3.99 GiB) rehearsal-added disk** and
**85,504,094,208 bytes (~79.63 GiB) host free**. Approved caps remain 150 MB / UI
1 GiB within rehearsal 8 GiB / host free floor 60 GiB; aggregate resources unchanged.

Browser, credentials, record names/contents and profile stayed VM-side. No PII
screenshots, DOM dumps, traces, raw console bodies, cookies/passwords, customer
record-name exports or backup transfers. No archive decrypted or cleanup done.

VM private evidence under `/var/lib/ndf-rootless-migration/rehearsal-v1/`:

- `state.json`: `ui.last_test_attempt`, result, approved CSP/config receipt.
- `ui-v1/automated-ui-blocked.json`: sanitized latest complete result.
- `commands.log`: only inspect sanitized structured UI records, not raw tails.

Local sanitized operation logs under the existing private healthchecks directory:
`ui-clone-csp-reload-wait-20261026.log`,
`ui-guard-response-complete-20261026.log`,
`ui-tests-response-complete-20261026.log`.

Post-run exact-hash no-data guard and protected native config/service/PID/resource/
maintenance checks passed (`ui-post-run-health-guard-20261026.log`). Read-only native
HTTP verification at 01:10:35 UTC remained Dalali 503, Demo 200, Dulaman 503, ERP
200 (`ui-v1/native-http-after-ui.json`). No native restart or maintenance clearing.

## Remaining gates / next work

1. Review exact outstanding read dependencies and recommendations' implicit writes;
   keep tracking/preference/route-history mutations blocked unless separately
   scoped/approved. Any additional security-policy change requires approval.
2. Complete storefront/search/products, role permissions, browser files/PDF and
   missing HR/lending form coverage where safe data exists. Review exclusions
   explicitly with the operator; do not self-issue business acceptance.
3. Resolve Dalali control-plane ownership and pilot-only external/in-flight writer
   fencing. Empty shared queues do not establish source quiescence.
4. Present a fresh exact pause/fencing/storage/schema-alignment/recovery/rollback
   final-sync plan for approval. Never promote the test-mutated rehearsal copy.
5. Obtain separate pilot traffic-switch and write-open approvals. Preserve native
   maintenance, other sites/mail, rollback artifacts and deferred exact-list cleanup.
