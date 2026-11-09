# M4 — Consolidated Repository File Disposition Matrix

**Repository:** NdfTechLabs/dt_master  
**Branch:** develop  
**Related audits:**
- docs/migrations/M4_DT_MASTER_FILE_LEVEL_AUDIT.md
- docs/migrations/M4_API_CALLER_AND_SECURITY_AUDIT_ADDENDUM.md
- docs/migrations/M4_UI_WORKSPACE_ROLE_AUDIT.md

**Status:** Proposed disposition baseline from read-only audit. This is not implementation authorization.  
**Audit baseline:** DT Master develop and DT Tenant develop tree snapshots, plus NDF Frappe Docker main source snapshot, reviewed during M4.1 repository-wide inventory and integration audit.

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
| dt_tenant/dt_tenant/page/tenant_subscription/* | **REFACTOR/RESTRICT** | M4.6 | Currently exposed through a workspace available to both tenant roles; restrict route and enforce Tenant Manager/billing scope in proxy/Master APIs |
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

## 12. Repository-wide inventory and additional dispositions

### 12.1 Audit snapshot and coverage boundary

| Repository | Branch | Git tree SHA | Files | Result |
|---|---|---|---:|---|
| NdfTechLabs/dt_master | develop | d9559e7db450fbf847ba58e0aa1990ae6dcca169 | 341 | Full tree retrieved and compared against this matrix. |
| NdfTechLabs/dt_tenant | develop | a3f65950440508622c00a76580814acf8e257bbb | 88 | Full tree retrieved and compared against this matrix. |

These are tree snapshot identifiers, not commit IDs. The audit is read-only. Dispositions describe future target changes and do not imply implementation has begun.

The 82 files under dt_master/docs/migrations/same-vm-docker/ are historical Dalali migration/rehearsal records. RETAIN AS ARCHIVE. They are not active M4 runtime source and must not be deleted as part of this audit. The two root script ZIP archives remain separately classified for later authorized cleanup.

### 12.2 Additional DT Master dispositions

| Path / component | Current responsibility or finding | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| dt_master/dt_master/doctype/capability_module/**, capability_profile/**, capability_role/** | Capability profile and allowed module/role child mappings; Capability Role links to Frappe Role | RETAIN/CLARIFY | M4.6 | These are entitlement mappings, not Bench app inventory or platform operator roles. Reject platform-privileged roles in tenant assignments. |
| dt_master/dt_master/doctype/execution_log/**, node_action_log/** | Child execution output and Node/Site action history | RETAIN/EXTEND | M4.3/M4.7 | Preserve parent ownership and history. Sanitize before persistence, scope read/export through parent authorization and retain deployment stages separately. |
| dt_master/dt_master/doctype/extension_screenshot/**, marketplace_category/** | Marketplace listing media and taxonomy | RETAIN | M4.5 | DT Master remains listing/commercial owner. Apply publisher authorization, media validation and safe rendering; technical release provenance comes from NDF. |
| dt_master/dt_master/doctype/frameworks/** | Framework catalogue | RETAIN | M4.5 | Keep as catalogue metadata; it is not the live Bench runtime inventory. Exact runtime components belong in Bench Runtime Component/NDF descriptor. |
| dt_master/dt_master/doctype/infrastructure_profile/**, infrastructure_provider/**, infrastructure_region/** | Infrastructure selection policy, provider catalogue and provider-owned region child rows | RETAIN/EXTEND | M4.3/M4.4 | Preserve infrastructure selection. Map NDF descriptor requirements to Node capabilities; keep provider-region child rows parent-owned. |
| dt_master/dt_master/doctype/machine_constraint/** | Resource limits plus container_image, container_template and node_selector | REFACTOR | M4.4 | Retain resource/capacity constraints and Node selection policy. Remove independent image/template authority: image digest and service topology come from the NDF Product Deployment Descriptor. |
| dt_master/dt_master/doctype/mail_server/**, mail_server_log/**, master_mail_users/** | Separate mail workload, operation logs and mailbox child records | RETAIN/EXTEND | M4.3 plus separate Mail workload | Mail Server remains a Node workload, not a Bench child. Preserve its Node relationship. Protect mailbox Password fields, authorize through Mail Server parent, sanitize logs and route operations through authorized execution. |
| dt_master/dt_master/doctype/script_definition/**, script_execution/** | Script catalogue and execution records, including Dynamic Link target and trigger source | RETAIN/REFACTOR | M4.3 | Bind scripts to allowlisted adapter operations, authorized targets and durable operation IDs. Do not allow user-controlled script paths/arbitrary commands. Correct triggere_source only through controlled migration. |
| dt_master/dt_master/dashboard_chart/**, dt_master/dt_master/dt_master_dashboard/**, dt_master/dt_master/number_card/** | Tenant count/acquisition charts and Total Tenants card | REFACTOR | M4.3/M4.6 | Tenants chart and Total Tenants Number Card are marked public; chart roles are empty. Restrict aggregates to intended platform-operator roles. |
| dt_master/hooks.py | Publishes Master app on Apps screen; app permission callback is commented out | REFACTOR/REVIEW | M4.6 | Align app visibility with platform operator access. App-screen visibility is not backend API authorization. |
| dt_master/modules.txt, dt_master/dt_tenant/__init__.py | Module list includes Dt Tenant inside Master; corresponding package marker exists | VERIFY; RETIRE IF UNUSED | M4.6 | Confirm installed-module and migration dependencies before removing the legacy marker. Do not conflate the separate dt_tenant app with the Master module. |
| dt_master/scripts/lib/runtime.sh | Creates sudoers rule granting frappe NOPASSWD:ALL | REPLACE PRIVILEGE MODEL | M4.3 | Gives the Bench OS user unrestricted root escalation. Replace with narrowly scoped, auditable privileged operations owned by Node execution adapter. |
| dt_master/scripts/lib/deps.sh | Dependency script method executes remote content through curl -fsSL ... \| bash | REPLACE/RESTRICT | M4.2/M4.3 | Do not execute arbitrary mutable remote scripts from dependency records. Require approved source allowlists and immutable commit/checksum/signature. Image/runtime dependencies belong to NDF. |
| dt_master/scripts/lib/state.sh | Local flock process lock | RETAIN AS LOCAL GUARD; EXTEND CONTROL PLANE | M4.3 | Useful for same-host mutual exclusion, but not a substitute for durable operation records, idempotency, leases or distributed conflict policy. |
| dt_master/scripts/lib/bench.sh, python_env.sh, gitlib.sh | Fixed Host Bench path, Host virtualenv invocation and Git working-tree inspection | RETAIN FOR HOST ADAPTER / REFACTOR | M4.3/M4.5 | Keep only as Host adapter during transition. Docker adapter has a separate execution model. Git branch/tag state must not be production release authority. |
| dt_master/scripts/lib/db.sh, env.sh, json.sh, core.sh, system.sh, log.sh | Shared script bootstrap, context/input helpers, DB, host prerequisite and logging utilities | RETAIN/REFACTOR | M4.3 | Preserve reusable helpers behind adapter contracts. Validate external inputs and centralize secret-safe diagnostics. |
| dt_master/scripts/lib/mail_stack.sh | Postfix/Dovecot/Rspamd/OpenDKIM and virtual mailbox operations | RETAIN/REFACTOR | Separate Mail workload; M4.3 execution alignment | Keep separate from Bench. Mailbox password is passed to doveadm pw as a process argument; replace with protected stdin/file-descriptor delivery. |
| dt_master/scripts/lib/bench_lifecycle.sh, node_lifecycle.sh, site_lifecycle.sh, app_lifecycle.sh, backup.sh | Existing lifecycle operations | REFACTOR/MOVE | M4.3/M4.5/M4.6 | Apply existing matrix dispositions. Site lifecycle currently edits shared Supervisor/Nginx config; app lifecycle fetches/builds shared Bench code; cleanup/backup must be scoped to an explicit Bench/Site owner. |
| dt_master/scripts/bin/{app,mail,node,node_setup,site,helpers}/** | Operation dispatchers for active shell library tree | RETAIN/REFACTOR | M4.3/M4.5/M4.6 | Keep only as Host adapter compatibility dispatch. Validate operation/target and route through durable operation service. Docker adapter must not call Host-only dispatchers. |
| dt_master/scripts/html_error_defaults/** | Static Nginx error-page defaults | MOVE/RETAIN IF REQUIRED | M4.3 | If needed, manage as Node ingress-owned artifacts, not Site lifecycle-owned Nginx config. |
| dt_master/config/**, dt_master/templates/**, package __init__.py files and dt_master/dt_master/.frappe | Empty package markers/framework scaffolding | RETAIN | Normal maintenance | No independent runtime ownership identified. Remove only after a later cleanup proves unused. |
| dt_master/public/images/** | Branding and static assets | RETAIN | Normal maintenance | Verify references when workspaces/app visibility are refactored. |
| Supporting files under dt_master/dt_master/doctype/** not individually named above | DocType package markers, standard controllers, form scripts, schema JSON and generated test scaffolds | RETAIN WITH PARENT DISPOSITION | Corresponding DocType milestone | The DocType-specific disposition governs its schema/behavior. Keep package markers; generated empty tests are scaffolding, not proof of behavioral coverage. Add meaningful tests in M4.7. |
| dt_master/__init__.py, dt_master/api/__init__.py, dt_master/controllers/__init__.py, dt_master/dt_master/__init__.py, dt_master/dt_master/doctype/__init__.py | Python package markers | RETAIN | Normal maintenance | Required package structure; no independent behavior. |
| dt_master/public/.gitkeep | Static asset directory placeholder | RETAIN | Normal maintenance | Preserve app asset path. |
| docs/migrations/*.md excluding same-vm-docker/** | Current M4 architecture, audit, specification and disposition records | RETAIN/UPDATE | M4.1 | Keep the milestone tracker, schema specification and audit matrix synchronized. Historical same-VM records remain a separate archive. |
| Root README.md, pyproject.toml, license.txt, .dockerignore, .editorconfig, .eslintrc, .gitignore, .pre-commit-config.yaml | App documentation, package metadata, license and developer configuration | RETAIN/MAINTAIN | Normal maintenance | Align README with current architecture; keep package and tooling configuration reproducible. |
| .dockerignore, .editorconfig, .eslintrc, .gitignore, .pre-commit-config.yaml, license.txt | Repository and development configuration | RETAIN/MAINTAIN | Normal maintenance | No M4 ownership change identified. |
| pyproject.toml, .github/workflows/ci.yml, .github/workflows/linter.yml | Package metadata, Frappe CI and lint/security checks | RETAIN/REFINE | M4.7 | Current CI uses Python 3.10/Node 18 and initializes Bench without pinning tested Frappe release. Align with supported Frappe/runtime combinations and reproducible dependencies. |
| README.md | App setup and operational documentation | RETAIN/UPDATE | M4.1/M4.3 | Keep installation instructions consistent with Host/Docker adapter boundary and canonical M4 contracts. |
| tests/**, dt_master/testing/** | Existing SSH/script sandbox, migration and automation contract tests | RETAIN/EXTEND | M4.7 | Preserve as Host compatibility evidence. Add target hierarchy, adapter, authorization, immutable deployment, cutover and rollback cases when M4.7 is authorized. |

### 12.3 Additional DT Master security and runtime findings

| ID | Path / component | Source-level finding | Proposed control | Target |
|---|---|---|---|---|
| DM-SEC-01 | scripts/lib/runtime.sh | frappe receives passwordless unrestricted sudo. | Replace with scoped privileged helpers, authorization and audit. | M4.3 |
| DM-SEC-02 | scripts/lib/deps.sh | Dependency script method executes remote content through curl piped to bash. | Allowlist and pin immutable source; move image/runtime dependencies to NDF. | M4.2/M4.3 |
| DM-SEC-03 | scripts/lib/mail_stack.sh | Mailbox password is passed to doveadm pw as a command-line argument. | Protected secret handoff; no secrets in process arguments. | Mail workload/M4.3 |
| DM-SEC-04 | Tenant-count Dashboard Chart and Number Card | Both definitions are public; chart roles are empty. | Restrict to platform operator roles and verify dashboard visibility. | M4.3/M4.6 |
| DM-SEC-05 | Mail Server Log, Node Action Log, Execution Log | Raw stdout/stderr and errors are durable records; browser filtering is not access control. | Central redaction, parent authorization, least-privilege export and operation-scoped realtime. | M4.3/M4.7 |
| DM-SEC-06 | Machine Constraint | Container image/template fields can create a second image/runtime authority beside NDF. | Make NDF descriptor authoritative; Machine Constraint expresses resource/capacity requirements only. | M4.4 |

### 12.4 DT Tenant repository dispositions

| Path / component | Current responsibility or finding | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| dt_tenant/api/extensions.py | Reads marketplace listing from Master using Tenant Settings credentials and caller-derived FQDN | REFACTOR | M4.5/M4.6 | Keep marketplace read API; use tenant-bound service identity, validate responses and enforce tenant role scope. |
| dt_tenant/api/marketplace.py | Tenant install/upgrade/uninstall proxies and Master-to-Tenant install callback | REFACTOR/RESTRICT | M4.5/M4.6 | Mutation requests require Tenant Manager authorization. Callback must accept authenticated Master service calls, validate pending operation, be replay-safe and publish scoped sanitized event. |
| dt_tenant/api/settings.py | configure(**kwargs) writes arbitrary matching Tenant Settings attributes with ignore_permissions=True | REPLACE/RESTRICT | M4.6 | Explicit field allowlist and bootstrap/platform-service authorization; prevent ordinary callers changing Master URL or credentials. |
| dt_tenant/api/system.py | Bootstrap helpers deny Guest but otherwise allow authenticated callers; one enumerates enabled users/roles | RESTRICT | M4.6 | Dedicated bootstrap service identity, narrow response and audit controls. |
| dt_tenant/api/tenant_subscription.py | Proxies plan, invoice, gateway, payment and encryption-key calls; methods lack consistent tenant-role gates | REFACTOR | M4.6 | Apply Tenant Manager/billing role policy at tenant entry and retain independent Master-side service identity and invoice/customer ownership checks. |
| dt_tenant/controllers/audit.py | Creates Capability Sync Log with ignore_permissions=True | RETAIN/EXTEND | M4.6 | Preserve audit records, sanitize errors and bind records to protected sync operation/actor. |
| dt_tenant/controllers/capabilities.py | Fetches capability snapshot from Master using stored API credentials and FQDN | REFACTOR | M4.6 | Protected credential references, tenant-bound service authentication, response validation and safe persistence. |
| dt_tenant/controllers/capability_scheduler.py | Active six-hour sync with opt-in and 60-minute failure cooldown | RETAIN/EXTEND | M4.6 | Preserve cadence/cooldown; add single-flight locking, durable operation identity, idempotency and safe diagnostics. |
| dt_tenant/controllers/capability_sync.py | Whitelisted sync mutates enabled System Users, role/module overlays and child rows using permission bypasses | RESTRICT/REFACTOR | M4.6 | Service/operator-only authorization, serialized execution, capability allowlist validation, recoverable changes and audit. |
| dt_tenant/controllers/lifecycle.py | Subscription lifecycle deletes role/permission rows and disables users; restoration re-enables all disabled non-Administrator users, not only users disabled by this lifecycle. Role backups are cache-backed. | REFACTOR | M4.6 | Durable lifecycle-owned state; restore only changes made by subscription enforcement; make transitions idempotent and recoverable. |
| dt_tenant/controllers/user_roles.py | Active User hooks intersect role/module assignments with capability snapshot; alternate sync helpers check enabled flags but are not registered in hooks | RETAIN/REFACTOR | M4.6 | Preserve tenant permission overlay, reject platform roles and validate linked role/profile/module. The active assign_roles path must also enforce Tenant Roles/Tenant Modules enabled state; confirm unused helper callers before retirement. |
| dt_tenant/security/subscription_guard.py | Contains Desk subscription gate, but before_request hook in hooks.py is commented out | REVIEW/ACTIVATE ONLY BY POLICY | M4.6 | Current global request gate is inactive. Finalize policy; do not claim this module currently blocks requests. |
| dt_tenant/dt_tenant/doctype/tenant_settings/** | Singleton stores Master URL, API key and API secret as ordinary Data fields; Tenant Manager has read/write permission | REPLACE/RESTRICT | M4.6 | Protected secret references, split ordinary preferences from service bootstrap, mask values and restrict rotation. |
| dt_tenant/dt_tenant/doctype/capability_sync_log/** | Tenant Manager and Tenant User can read/export/report/print rows containing error_message | RETAIN/RESTRICT | M4.6 | Keep sync history, sanitize error content and limit detailed diagnostics/export. |
| dt_tenant/dt_tenant/doctype/tenant_roles/**, tenant_modules/**, tenant_role_profile/** | Tenant Manager can maintain role/module catalogues and profiles | RETAIN/EXTEND | M4.6 | Preserve tenant administration overlay; enforce capability allowlists and reject System Manager/platform roles. |
| dt_tenant/dt_tenant/doctype/tenant_user_role/**, tenant_user_role_profile/**, tenant_allowed_module/** | Child tables on User and Tenant Role Profile with no independent permission rows | RETAIN WITH PARENT AUTHORIZATION | M4.6 | Resolve child rows through authorized parent and validate role/module membership. |
| dt_tenant/fixtures/custom_field.json | Adds tenant role/profile/module fields to User | RETAIN/EXTEND | M4.6 | Preserve fixture; validate migration idempotency and ensure fields do not grant privilege outside capability overlay. |
| dt_tenant/dt_tenant/page/add_ons/** | Marketplace UI and realtime status listener | REFACTOR | M4.5 | Escape/sanitize Master/publisher HTML fields, authorize mutations server-side and correlate realtime updates to site and operation. |
| dt_tenant/dt_tenant/page/tenant_subscription/** | Subscription, invoice and payment UI | REFACTOR/RESTRICT | M4.6 | Escape API-supplied values, enforce Tenant Manager/billing role at page/API boundaries, protect invoice/payment data. Review externally loaded CryptoJS/asmCrypto assets for integrity/CSP policy. |
| dt_tenant/dt_tenant/workspace/tenant/tenant.json, dt_tenant/workspace_sidebar/tenant.json | Workspace available to Tenant Manager and Tenant User; both expose Subscription navigation | REFACTOR | M4.6 | Current workspace roles include both and page definition/JS has no Tenant Manager-only gate. Restrict Subscription route and enforce same policy in APIs. |
| dt_tenant/dt_tenant/workspace/workspace_filter.py | Filters Subscription shortcut only for Unpaid status; does not establish general role authorization | RETAIN/REFACTOR | M4.6 | Presentation filtering only; server-side role and subscription policy remains authoritative. |
| dt_tenant/hooks.py | Registers User permission hooks and active six-hour scheduler; subscription before_request hook commented | RETAIN/REFACTOR | M4.6 | Preserve active hooks, secure service/scheduler execution and make lifecycle policy explicit. |
| dt_tenant/patches.txt | Empty pre/post model-sync patch sections | RETAIN/EXTEND WHEN AUTHORIZED | M4.6 | Add only ordered, idempotent, preflighted migrations when schema/secret changes are approved. |
| dt_tenant/README.md | Installation/configuration and lifecycle documentation | RETAIN/UPDATE | M4.1/M4.6 | Correct role/page access and describe protected service identity and lifecycle recovery. |
| dt_tenant/pyproject.toml, .github/workflows/ci.yml, .github/workflows/linter.yml | Package metadata and CI; CI uses Python 3.10/Node 18 and unpinned Bench/Frappe initialization | RETAIN/REFINE | M4.7 | Pin supported Frappe/runtime matrix and reproducible dependencies. Existing DocType tests are mostly generated empty scaffolds; add meaningful API, role, lifecycle and secret tests in M4.7. |
| dt_tenant/public/images/**, dt_tenant/templates/**, dt_tenant/**/__init__.py, dt_tenant/config/** | Branding, empty template/package scaffolding | RETAIN | Normal maintenance | No separate M4 ownership change identified. |
| Supporting files under dt_tenant/dt_tenant/doctype/** not individually named above | DocType package markers, standard controllers, form scripts, schema JSON and generated test scaffolds | RETAIN WITH PARENT DISPOSITION | M4.6 | Parent/child schema dispositions above govern behavior. Generated empty IntegrationTestCase classes are scaffolding, not meaningful security or lifecycle coverage; add focused tests in M4.7. |
| dt_tenant/api/__init__.py, controllers/__init__.py, dt_tenant/__init__.py, dt_tenant/dt_tenant/__init__.py, dt_tenant/dt_tenant/doctype/__init__.py, dt_tenant/dt_tenant/page/__init__.py, dt_tenant/dt_tenant/.frappe | Python package markers and Frappe app marker | RETAIN | Normal maintenance | No independent runtime behavior identified. |
| dt_tenant/modules.txt | Tenant app module declaration | RETAIN | Normal maintenance | Keep module declaration aligned with DocType module metadata. |
| dt_tenant/dt_tenant/page/** and dt_tenant/dt_tenant/workspace/** | Page definitions/scripts and workspace presentation assets | RETAIN/REFACTOR BY PAGE DISPOSITION | M4.5/M4.6 | Specific Add-ons and Subscription security/access dispositions above take precedence. |
| dt_tenant/public/.gitkeep | Static asset directory placeholder | RETAIN | Normal maintenance | Preserve asset path. |
| Root README.md, pyproject.toml, license.txt, .editorconfig, .eslintrc, .gitignore, .pre-commit-config.yaml | App documentation, package metadata, license and developer configuration | RETAIN/MAINTAIN | Normal maintenance | Keep install/runtime compatibility requirements explicit and tooling reproducible. |
| .editorconfig, .eslintrc, .gitignore, .pre-commit-config.yaml, license.txt | Repository/development configuration | RETAIN/MAINTAIN | Normal maintenance | No M4 ownership change identified. |

### 12.5 Additional DT Tenant security and behavior findings

| ID | Path / component | Source-level finding | Proposed control | Target |
|---|---|---|---|---|
| DT-SEC-01 | Add-ons and Subscription page JavaScript | Marketplace metadata, plan descriptions, invoice fields, payment gateway labels and other API values are interpolated into HTML templates without consistent escaping. | Escape text/attributes, sanitize approved rich text and avoid direct HTML insertion of untrusted publisher/API data. | M4.5/M4.6 |
| DT-SEC-02 | Tenant Workspace and Subscription page | Workspace assigned to Tenant Manager and Tenant User; Subscription shortcut present for both; page JSON/JS has no Tenant Manager-only gate. | Restrict route/navigation and enforce Tenant Manager/billing role in every corresponding API. | M4.6 |
| DT-SEC-03 | Capability Sync Log | Tenant User and Tenant Manager can read/export/report/print rows containing error messages. | Sanitize errors and restrict detailed logs. | M4.6 |
| DT-SEC-04 | controllers/lifecycle.py | Re-enables every disabled non-Administrator user when subscription returns active/grace, including users disabled for unrelated reasons; role restoration is cache-backed. | Persist lifecycle-owned changes and restore only those changes. | M4.6 |
| DT-SEC-05 | subscription_guard.py / hooks.py | Subscription request gate is implemented but not registered; only capability sync invokes lifecycle transitions. | Finalize intended policy and wire only through authorized, tested design. | M4.6 |
| DT-SEC-06 | api/settings.py, api/system.py, controllers/capability_sync.py | Whitelisted privileged endpoints lack adequate role/service checks; some use ignore_permissions=True and perform broad state changes. | Service-only/role-gated entry points, tenant binding, allowlists, audit and idempotency. | M4.6 |
| DT-SEC-07 | api/tenant_subscription.py | Billing/payment methods lack consistent Tenant Manager/billing role gates and log remote response bodies on errors. | Tenant role gates, safe errors and independent Master resource ownership checks. | M4.6 |
| DT-SEC-08 | api/marketplace.py callback | Callback publishes site-wide realtime data from caller-supplied fields without validating Master service identity or pending operation. | Authenticated, replay-safe callback bound to outstanding operation and scoped recipient. | M4.5/M4.6 |
| DT-SEC-09 | controllers/user_roles.py | Active assign_roles path intersects with Master capability allowlists but does not check enabled state of Tenant Roles/Tenant Modules; alternate helper functions that check enabled state are not registered in current User hooks. | Enforce enabled state in the active hook path, validate role/module references and prohibit platform-privileged roles. | M4.6 |

### 12.6 Cross-repository caller discovery

The develop tree comparison confirms the known in-repository DT Tenant → DT Master call paths. Organization-scoped GitHub code searches for dt_master.api., /api/method/dt_master., publish_install_update and dt_tenant.api. returned no additional indexed matches. This is not proof that no external/private/unindexed caller exists. Before API contracts are frozen, verify deployment integrations, installed apps and private repositories/scripts outside these two accessible trees.

### 12.7 Updated audit status

| Area | Status |
|---|---|
| DT Master full Git tree inventory on develop | Completed |
| DT Tenant full Git tree inventory on develop | Completed |
| Existing disposition matrix comparison | Extended with additional DocType, API, controller, shell, UI, configuration, test and asset groups |
| DT Master source audit | Substantially complete for architecture-relevant paths; remaining caller confirmation is listed above |
| DT Tenant source audit | Source review completed for principal API, controller, role, lifecycle, workspace, page and DocType paths |
| External callers outside the two accessible repository trees | Platform owner confirms no additional consumers are known outside DT Tenant (2026-11-09); independent runtime, deployment and credential-inventory verification remains an implementation prerequisite |
| Cross-repository role/service identity and authorization contract | Accepted Section 15 policy reconciled in the canonical specification; concrete role fixtures, endpoint enforcement, credential migration and runtime verification remain implementation work |
| NDF descriptor/deployment integration contract consolidation | Section 16 artifact/build/deployment contract accepted; formal schemas, workflows and implementation remain assigned to M4.2–M4.7 |
| External/private caller verification | In-repository caller discovery completed to the documented boundary; private, deployed and unindexed callers are not proven absent |
| Final M4.1 architecture review and acceptance | Outstanding; authorization policy acceptance alone does not close M4.1 |

No application code, DocTypes, migrations, scripts, workflows or runtime configuration were changed by this audit. This matrix update records proposed dispositions only.


## 13. NDF Frappe Docker integration source dispositions

**Repository:** NdfTechLabs/frappe_docker  \
**Branch:** main  \
**Tree SHA:** 7c16ac4cf56226e417b941144da272267937dd84  \
**Files:** 198

| Path / component | Current responsibility or finding | Proposed disposition | Target milestone | Rationale / target |
|---|---|---|---|---|
| images/ndf/schemas/application-manifest-v1.schema.json | Existing recursive composition manifest schema | RETAIN/EXTEND | M4.2 | Preserve backward-compatible composition and resolver inputs. Add separate Marketplace App Descriptor and Deployment Candidate/Deployment Descriptor schemas; do not overload this schema as a per-app release descriptor. |
| images/ndf/schemas/image-definitions-v1.schema.json and images/ndf/image-definitions/images-v1.json | Product image IDs, manifests and GHCR repositories | RETAIN/EXTEND | M4.2 | Keep product/image registry mapping. Add versioned build/deployment metadata references without making this file the runtime service descriptor. |
| images/ndf/manifests/** | Core, capability, master and tenant product compositions | REFACTOR/EXTEND | M4.2/M4.5 | Preserve product separation and required/optional semantics. Transition app entries from mutable refs/name-only identity to registered app_id, immutable release descriptor and digest-backed composition. |
| images/ndf/locks/*.lock.json | Deterministic resolved app inventory and commit SHAs | RETAIN/EXTEND | M4.2 | Preserve lock/checksum model. Include exact runtime and dependency locks, descriptor digests and base image digests. Ensure the actual build consumes these resolved identities. |
| images/ndf/scripts/resolve_manifest.py | Recursive manifest loader, dependency graph and immutable ref resolver | RETAIN/REFACTOR | M4.2 | Preserve resolver. Enforce development-branch policy, compatibility status/ranges, required checks and service/application separation. |
| images/ndf/scripts/prepare_build.py | Generates apps.json and build metadata | REFACTOR | M4.2 | Current apps.json uses target_ref rather than locked resolved SHA. Generate Bench inputs from exact immutable commits and include service/runtime descriptor inputs separately. |
| images/ndf/scripts/build_image.py | Buildx command, OCI labels, digest/build result | RETAIN/EXTEND | M4.2 | Preserve Buildx and digest metadata. Pin/record base image digests and emit build provenance/composition hash/descriptor linkage. |
| images/ndf/scripts/validate_image.py | Checks app directories, apps.txt and metadata labels | REFACTOR/EXTEND | M4.2 | Inspect actual installed Git HEAD for every app and Frappe against the lock; verify base image identity, dependency inventory and product smoke/readiness checks. |
| images/layered/Containerfile | Installs Frappe and apps using FRAPPE_BRANCH/apps.json | REFACTOR | M4.2 | Consume immutable commit SHAs for Frappe and all apps; pin base/build images by digest or an equivalently verified immutable base reference. |
| images/ndf/manifests/capabilities/ai-automation-v16.json services entries | Declares separate services such as frappe-ai-agent and frappe-mcp-server | EXTEND/IMPLEMENT SERVICE CONTRACT | M4.2 | Current resolver ignores services and only composes applications. Define service schema, build/publish behavior, service topology and runtime health declarations; do not pass service entries to bench apps.json. |
| images/ndf/.build/** | Generated apps.json and build-metadata.json are tracked | REMOVE GENERATED OUTPUT FROM VERSION CONTROL; IGNORE | M4.2 | Generate ephemeral outputs from immutable lock files. Keep committed deterministic locks, not build workspace artifacts. |
| .github/workflows/ndf-build-product.yml | Fixed product matrix for build/validation on main/PR/manual dispatch | RETAIN/EXTEND | M4.2 | Preserve product CI. Add candidate-driven validation/build path while retaining static product builds for regression coverage. |
| .github/workflows/ndf-publish-product.yml | Manual product selection, GHCR publish and digest validation | RETAIN/EXTEND | M4.2/M4.4 | Preserve least-privilege GHCR publishing. Support authorized candidate builds and publish only after SHA/descriptor/image validation. |
| .github/workflows/ndf-release-product.yml | Manual semantic/stable promotion for DhanaERP Master only | RETAIN/GENERALIZE | M4.2/M4.4 | Preserve exact digest promotion and no-overwrite policy; support approved product release channels consistently across products. |
| .github/workflows/ndf-validate.yml | Manifest, lock, script and test validation | RETAIN/EXTEND | M4.2 | Add policy enforcement and descriptor/service schema validation. Ensure required product acceptance checks are actually executed rather than merely listed in manifest metadata. |
| compose.yaml and overrides/compose.*.yaml | Upstream Frappe Docker runtime and deployment variants | RETAIN AS UPSTREAM FOUNDATION | M4.2 | Do not replace upstream Compose. Add versioned NDF product/service descriptors and only the supported NDF-specific Compose generation/overrides needed by DT Master. |
| images/ndf/tests/** | Resolver, lock, build preparation, image definition and validation tests | RETAIN/EXTEND | M4.2 | Add tests proving SHA-to-build enforcement, policy gates, service descriptor handling, descriptor/digest binding and candidate result contract. |
| images/ndf/README.md and docs/11-how-to/09-build-app-images-with-github-actions.md | NDF product image factory and build guidance | RETAIN/UPDATE | M4.1/M4.2 | Distinguish implemented M1–M3 capabilities from proposed M4 contracts; document exact descriptor/build contract after approval. |

### 13.1 NDF critical findings

| ID | Finding | Required resolution | Target |
|---|---|---|---|
| NDF-01 | Lock contains resolved app SHAs, but prepare_build.py and Containerfile build from target_ref; validator does not inspect installed commits. | Build Frappe and every app from locked SHA; verify actual Git HEADs in the resulting image. | M4.2 blocker |
| NDF-02 | Base/build images use branch tags rather than recorded immutable base-image digests. | Pin or verify base image digests and record them in provenance/descriptor. | M4.2 |
| NDF-03 | allow_development_branches and fail_on_unverified_compatibility are not enforced; required_checks are declarative. | Implement fail-closed policy and execute declared acceptance checks. | M4.2 |
| NDF-04 | Marketplace App Descriptor/app_id/release registry absent. | Add versioned immutable app descriptor and registered identity contract. | M4.2 |
| NDF-05 | AI capability service declarations are ignored by resolver/build. | Add separate service composition/build/runtime contract. | M4.2 |
| NDF-06 | Product Deployment Descriptor and candidate-driven build/result flow absent. | Add descriptor generation, candidate workflow and digest-bound result. | M4.2/M4.4 |
| NDF-07 | Generated .build artifacts are tracked. | Remove generated build workspace outputs from version control and ignore them. | M4.2 |

