# M4 — Consolidated DT Master File Disposition Matrix

**Repository:** NdfTechLabs/dt_master  
**Branch:** develop  
**Related audits:**
- docs/migrations/M4_DT_MASTER_FILE_LEVEL_AUDIT.md
- docs/migrations/M4_API_CALLER_AND_SECURITY_AUDIT_ADDENDUM.md
- docs/migrations/M4_UI_WORKSPACE_ROLE_AUDIT.md

**Status:** Proposed disposition baseline from read-only audit. This is not implementation authorization.  
**Audit baseline:** DT Master develop snapshot reviewed during M4.1 discovery; DT Tenant version-1 caller/UI snapshot reviewed for cross-repository dependencies.

## 1. Disposition vocabulary

| Disposition | Meaning |
|---|---|
| RETAIN | Keep current responsibility and implementation, subject to normal maintenance |
| EXTEND | Keep the component but add fields, behavior or contract support |
| REFACTOR | Preserve purpose while changing internal structure, ownership or execution model |
| MOVE | Transfer responsibility to a different component/DocType/layer |
| REPLACE | Replace the existing execution/authority mechanism with the target contract |
| REMOVE | Retire from active repository/runtime after dependency and migration verification |

A component may have a combined disposition where part of its responsibility remains and another part moves. The target milestone is where the change is implemented, not a claim that the milestone has started.

## 2. Core DocTypes and ownership

| File/component | Current responsibility | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| dt_master/dt_master/doctype/infrastructure_node/infrastructure_node.json | Node identity plus Bench paths, version and lifecycle buttons | **REFACTOR + MOVE** | M4.3 | Keep infrastructure fields; move Bench-owned runtime/path/state fields and lifecycle actions to Bench Environment |
| dt_master/dt_master/doctype/infrastructure_node/infrastructure_node.py | Node controller and Bench lifecycle state | **REFACTOR** | M4.3 | Restrict controller to Node lifecycle/readiness and infrastructure operations |
| dt_master/dt_master/doctype/infrastructure_node/infrastructure_node.js | Node actions and Bench backup/update/migrate/remove/restore controls | **REFACTOR + MOVE** | M4.3 | Node-only actions remain; Bench controls move to Bench Environment |
| dt_master/dt_master/doctype/tenant_site/tenant_site.json | Site identity, direct Node link, app rows, credentials and site actions | **EXTEND + REPLACE + MOVE** | M4.6 | Add mandatory Bench Environment; migrate/retire direct Node source; move credentials to Runtime Secret refs |
| dt_master/dt_master/doctype/tenant_site/tenant_site.py | Site controller and lifecycle behavior | **REFACTOR** | M4.6 | Resolve Node through Bench Environment; separate Site and Bench lifecycle |
| dt_master/dt_master/doctype/tenant_site/tenant_site.js | Site lifecycle and child app action controls | **REFACTOR** | M4.5/M4.6 | Route all mutations through authorized operation/request service; display parent Bench and derived Node |
| dt_master/dt_master/doctype/tenant_installed_app/tenant_installed_app.json | Site-level app state and direct action buttons | **EXTEND** | M4.5 | Keep site installation state; enforce availability in parent Bench Environment; correlate request/job IDs |
| dt_master/dt_master/doctype/tenant_installed_app/tenant_installed_app.py | Child row controller | **RETAIN/EXTEND** | M4.5 | Preserve site-specific app state; no independent Bench code mutation |
| New Bench Environment DocType and child tables | Not present in audited current tree | **ADD** | M4.1 design; M4.3 implementation | Implement agreed Node → Bench Environment → Tenant Site hierarchy and Bench Environment Application inventory |
| New Bench Deployment / Deployment Stage / Deployment Candidate records | Not present in audited current tree | **ADD** | M4.1 design; M4.3/M4.4 implementation | Track immutable desired composition, deployment descriptor, candidate and stage history |
| New App Installation Request | Not present in audited current tree | **ADD** | M4.1 design; M4.5 implementation | Durable tenant app request, authorization, idempotency and queue lifecycle |
| New Runtime Secret model/provider abstraction | Not present as agreed target model | **ADD** | M4.1 design; M4.3/M4.6 implementation | Store references/provider metadata; do not store plaintext in ordinary fields |
| New durable operation/request coordinator | Not present in audited current tree | **ADD or EXTEND an agreed request model** | M4.1 design; M4.3 implementation | Common state, lock/lease, idempotency, worker/job correlation and recovery |

## 3. API and controller layer

| File | Current responsibility | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| dt_master/api/infrastructure_node.py | Node and Bench operations | **REFACTOR + MOVE** | M4.3 | Split Node API from Bench Environment API; route through runtime adapter |
| dt_master/api/tenant_site_executor.py | Site execution through direct Node link and SSH | **REFACTOR** | M4.6 | Resolve Site → Bench → Node; use Site lifecycle service and adapter |
| dt_master/api/tenant_installed_app_executor.py | Direct app execution through SSH | **REPLACE execution strategy** | M4.5 | All actions enter App Installation Request and unified operation service |
| dt_master/api/marketplace.py | Listing/release discovery and app request enqueue | **REFACTOR** | M4.5 | NDF descriptor authority, app availability validation, request/idempotency and one async path |
| dt_master/api/extensions.py | Marketplace listing and compatibility | **EXTEND** | M4.5 | Consume NDF descriptor metadata and Bench availability |
| dt_master/api/framework_version.py | Dependency preview and direct dependency application | **REFACTOR** | M4.5 | Keep compatibility catalogue; dependency resolution and composition move to candidate/NDF workflow |
| dt_master/api/script_contract.py | Constructs execution environments and SSH commands | **SPLIT + REFACTOR** | M4.3 | Define Node, Bench, Site and Mail execution contexts; adapter-safe secret delivery |
| dt_master/api/mail_server_executor.py | Mail workload orchestration | **RETAIN/REFACTOR** | M4.3 or separate mail workstream | Preserve Mail Server as Node workload; use common operation, auth and transport standards |
| dt_master/controllers/provisioning.py | Tenant bootstrap and remote service user creation | **REFACTOR** | M4.6 | Create least-privilege tenant service identity, bind it to Site, resolve Site through Bench |
| dt_master/api/subscription.py | Tenant subscription and billing reads/mutations | **REFACTOR** | M4.6 | Derive tenant ownership from authenticated service identity; enforce plan transitions |
| dt_master/api/billing.py | Invoice/payment/payment-method actions | **REFACTOR** | M4.6 | Explicit invoice/customer/subscription authorization; remove unsafe Administrator elevation |
| dt_master/api/tenant_capabilities.py | Tenant capability snapshot | **EXTEND** | M4.6 | Bind request to authenticated Tenant Site identity; keep capability data separate from platform roles |
| dt_master/api/flutterwave_linker.py | Payment integration | **REVIEW/RETAIN** | M4.6 | Verify caller authorization and tenant/payment ownership before any changes |
| dt_master/api/script_contract.py realtime helpers | Publish execution logs | **REFACTOR** | M4.3 | Operation-ID correlation, authorized recipient scoping and safe diagnostics |

## 4. Shell scripts and execution runtime

| File/component | Current responsibility | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| dt_master/scripts.zip | Historical script archive | **REMOVE** | Repository cleanup after audit | Not consumed by active deployment tooling; Git history preserves it |
| dt_master/scripts_new.zip | Historical script archive | **REMOVE** | Repository cleanup after audit | Not consumed by active deployment tooling; Git history preserves it |
| dt_master/scripts/ | Active directory-based script tree | **RETAIN/REFACTOR** | M4.3/M4.5/M4.6 | Keep only behind explicit adapter contracts |
| dt_master/scripts/CONTRACTS.md | Python-to-shell execution contract | **REVISE** | M4.3 | Define adapter execution, timeout, cancellation, secret and result contracts |
| dt_master/scripts/lib/node_lifecycle.sh | Host/Node lifecycle | **REFACTOR** | M4.3 | Node prerequisites and host operations only |
| dt_master/scripts/lib/bench_lifecycle.sh | Bench installation/start/update/migrate/backup | **MOVE/REPLACE** | M4.3 | Bench Environment runtime adapter owns lifecycle; Host implementation retained as one adapter |
| dt_master/scripts/lib/site_lifecycle.sh | Site creation/configuration and service mutation | **REFACTOR** | M4.6 | Site-only operations; remove shared Bench Supervisor/service changes |
| dt_master/scripts/lib/app_lifecycle.sh | App fetch/install/build/reload | **SPLIT/REPLACE** | M4.5 | Remove tenant-triggered shared Bench code mutation; keep only approved site-level install operations |
| dt_master/scripts/lib/runtime.sh | Host user and privilege helpers | **RETAIN** for Host adapter | M4.3 | Docker runtime uses a separate implementation |
| dt_master/scripts/lib/backup.sh | Site/Bench backup and restore | **EXTEND/REFACTOR** | M4.3/M4.6 | Use Bench storage profiles and managed backup references, not free-form paths |
| dt_master/scripts/bin/* | Script dispatchers | **REFACTOR** | M4.3/M4.5/M4.6 | Preserve dispatch where useful, but bind commands to adapter-owned execution contexts |

## 5. Marketplace, release and dependency catalogue

| File/component | Current responsibility | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| Extension DocType/controller/JS | Marketplace listing and commercial identity | **RETAIN/EXTEND** | M4.5 | DT Master remains catalogue/commercial owner; add descriptor reference and approval workflow |
| Extension Version DocType/controller/JS | Release and compatibility records | **EXTEND** | M4.5 | Store immutable NDF descriptor reference/digest and release approval state |
| Marketplace Category | Listing taxonomy | **RETAIN** | M4.5 | No ownership conflict identified |
| Extension Screenshot | Listing media | **RETAIN** | M4.5 | No ownership conflict identified |
| Framework Version | Framework compatibility baseline | **RETAIN/EXTEND** | M4.5 | Exact deployed runtime versions belong in Bench Runtime Component |
| Framework Version Dependency | Existing dependency catalogue | **RECLASSIFY/REFACTOR** | M4.5 | Separate Node prerequisites, image/runtime dependencies and Bench external services |
| System Dependency | Existing system dependency catalogue | **RECLASSIFY/REFACTOR** | M4.5 | NDF owns image/runtime resolution; DT Master owns Node prerequisites/external service references |
| Git-tag release discovery | Current release source in Marketplace paths | **REPLACE** | M4.5 | NDF immutable Marketplace App Descriptor is technical release authority |

## 6. Logs, scripts, mail and infrastructure catalogues

| File/component | Current responsibility | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| Script Definition | Script catalogue | **RETAIN/EXTEND** | M4.3 | Bind definitions to supported adapter operations and authorization |
| Script Execution | Command execution record | **RETAIN/EXTEND** | M4.3 | Correlate with durable operation and deployment stage |
| Execution Log | Command output history | **RETAIN/EXTEND** | M4.3 | Sanitize, scope and retain safe diagnostics |
| Node Action Log | Node/Site action history | **RETAIN** | M4.3 | Keep generic Node/Site history; Bench deployment stage has dedicated record |
| Mail Server and Master Mail Users | Separate mail service and users | **RETAIN/EXTEND** | Outside Bench ownership; M4.3 adapter alignment | Keep Node workload boundary; improve secret references, operation and authorization |
| Mail Server Log | Mail execution history | **RETAIN/EXTEND** | Mail workstream | Sanitize and correlate with operation IDs |
| Infrastructure Profile/Provider/Region | Provisioning catalogue | **RETAIN/EXTEND** | M4.3 | Add only infrastructure requirements needed by adapter/provisioning contract |
| Machine Constraint | Product/tenant infrastructure constraints | **RETAIN/EXTEND** | M4.4 | Map validated product deployment requirements to Node capacity |
| Frameworks | Framework catalogue | **RETAIN** | M4.5 | Compatibility catalogue; not the live runtime owner |
| Capability Module/Profile/Role | Tenant entitlement and capability mapping | **RETAIN/CLARIFY** | M4.6 | Keep distinct from platform operator roles and runtime app inventory |

## 7. UI, Workspace and navigation

| File/component | Proposed disposition | Target milestone | Rationale |
|---|---|---|---|
| dt_master/dt_master/workspace/master/master.json | **REFACTOR** | M4.3/M4.6 | Restrict platform workspace roles and separate operations from billing/tenant administration |
| dt_master/workspace_sidebar/master.json | **REFACTOR** | M4.3/M4.6 | Group navigation by Node, Bench, Site, Marketplace, Billing and Mail ownership |
| dt_master/dt_master/sidebar/dt_master/dt_master.json | **REFACTOR** | M4.3/M4.6 | Remove duplicate/stale links; reflect target hierarchy |
| dt_master/hooks.py | **REVIEW/EXTEND** | M4.6 | Decide whether app-screen visibility needs explicit permission callback |
| dt_tenant/dt_tenant/workspace/tenant/tenant.json | **RETAIN/REFACTOR** | M4.6 | Keep tenant-facing shortcuts and avoid exposing privileged configuration |
| dt_tenant/workspace_sidebar/tenant.json | **RETAIN/REFACTOR** | M4.6 | Align navigation with Tenant Manager/Tenant User roles |
| dt_tenant/dt_tenant/page/add_ons/* | **RETAIN/EXTEND** | M4.5 | Marketplace access for permitted tenant roles; server authorization remains authoritative |
| dt_tenant/dt_tenant/page/tenant_subscription/* | **RETAIN** | M4.6 | Keep Tenant Manager access and enforce same scope in proxy/Master APIs |
| dt_tenant/dt_tenant/doctype/tenant_settings/* | **REFACTOR** | M4.6 | Protect Master URL/API credentials; restrict settings mutation |
| dt_tenant/dt_tenant/doctype/tenant_roles/* | **RETAIN/EXTEND** | M4.6 | Preserve tenant role overlay; reject platform-privileged roles |
| dt_tenant/dt_tenant/doctype/tenant_role_profile/* | **RETAIN/EXTEND** | M4.6 | Preserve capability-constrained user role profiles |
| dt_tenant/controllers/user_roles.py | **RETAIN/REFACTOR** | M4.6 | Preserve tenant permission synchronization; add negative role/capability tests |
| dt_tenant/hooks.py | **RETAIN/EXTEND** | M4.6 | Preserve six-hour capability sync with service auth, lock and audit |

## 8. Tests, migration and documentation

| File/component | Proposed disposition | Target milestone | Required work |
|---|---|---|---|
| tests/test_automation_contracts.py | **RETAIN/REFACTOR** | M4.3 | Test Node/Bench/Site adapter contracts and authorization |
| dt_master/testing/docker_sandbox.py | **EXTEND** | M4.7 | Multi-Bench/multi-Site, Host and Docker scenarios |
| dt_master/testing/integration.py | **REFACTOR/EXTEND** | M4.7 | Test target hierarchy, async operations, cutover and rollback |
| tests/test_migration_plan.py | **EXTEND** | M4.7 | Verify existing Host Bench grouping, idempotency, resumability and recovery |
| tests/README.md | **REVISE** | M4.7 | Document target test profiles and environment requirements |
| dt_master/patches.txt | **EXTEND** only when implementation authorized | M4.6 | Ordered, idempotent and preflighted migration patches |
| docs/migrations/M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md | **RETAIN/UPDATE** | M4.1 | Canonical target schema, resolved decisions and implementation contracts |
| docs/migrations/M4_DT_MASTER_FILE_LEVEL_AUDIT.md | **RETAIN/UPDATE** | M4.1 | First-pass file audit and API/security findings |
| docs/migrations/M4_API_CALLER_AND_SECURITY_AUDIT_ADDENDUM.md | **RETAIN/UPDATE** | M4.1 | Cross-repository caller/security findings |
| docs/migrations/M4_UI_WORKSPACE_ROLE_AUDIT.md | **RETAIN/UPDATE** | M4.1 | UI/workspace/role findings and proposed dispositions |
| This disposition matrix | **RETAIN/UPDATE** | M4.1 | Consolidated file-level migration plan |

## 9. Cross-cutting controls required before implementation

1. A single server-side operation/request contract with idempotency, lock/lease, queue/job correlation, timeout, cancellation and recovery.
2. A server-side authorization matrix distinguishing Platform Operator, Platform Marketplace Operator, Tenant Service Identity, Tenant Manager, Tenant User and read-only/support actors.
3. Tenant identity binding from authenticated service credentials, not caller-supplied FQDN.
4. Runtime Secret provider/reference contract and safe delivery to Host/Docker adapters.
5. Realtime event authorization, operation-ID correlation and sanitized diagnostics.
6. NDF Marketplace App Descriptor, Product Composition Manifest and Product Deployment Descriptor contracts.
7. Bench Environment hierarchy and migration contract.
8. Explicit Host and Docker adapter interface with common operation results and health reporting.

## 10. Explicit exclusions and safeguards

- This matrix does not authorize implementation.
- No role names, DocTypes, fields, migrations, APIs, scripts or runtime configuration should be changed until the relevant M4 implementation milestone is authorized.
- The existing Host Bench migration baseline remains one initial Bench Environment per existing Node only after verifying the actual shared Bench directory and site membership.
- Target architecture supports multiple Bench Environments per Node.
- Tenant Site has exactly one parent Bench Environment; Node is derived through that parent.
- Tenant Installed App remains site-level; Bench Environment Application is the availability inventory.
- Mail Server remains a separate Node workload, not a Bench Environment child.
- Historical script ZIP archives are classified for removal from the active tree; do not delete until an explicitly authorized cleanup change.
- Runtime/app changes use immutable NDF deployment artifacts and replacement Bench Environments; do not mutate shared Bench code in place.

## 11. Matrix status and remaining verification

This is the consolidated proposed disposition for the files/components inspected in the M4.0/M4.1 audit. It is not yet a proof that every repository path has been individually read.

Still required before declaring the repository-wide audit complete:
- Trace all remaining API callers, including external integrations not represented in the inspected DT Tenant client.
- Verify realtime event publisher/subscriber identity and worker-session behavior.
- Finish full sensitive-field consumer/export/report/backup tracing.
- Verify Frappe runtime permission behavior for child DocTypes with no explicit permission rows.
- Complete exact role/service identity bootstrap contract.
- Perform a final path-by-path comparison against both repository trees to identify unclassified files.

No code, DocTypes, migrations, scripts or runtime configuration were changed.
