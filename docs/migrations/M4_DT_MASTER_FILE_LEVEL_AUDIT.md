# M4 — DT Master File-Level Audit

**Status:** Source audit complete for the reviewed M4.1 scope (2026-11-09); implementation not authorized  
**Repository:** NdfTechLabs/dt_master  
**Branch:** develop  
**Audited baseline:** 4d7cd677155f5ccee9e915b8508a9276f382ef9d  
**Updated audit record:** 2026-11-09  
**Scope:** Existing implementation, API dependencies/callers, background execution, concurrency, permissions, secrets and proposed dispositions.  
**Implementation status:** No implementation changes authorized or performed.

> This is an audit of the existing repository snapshot. Proposed dispositions are migration decisions, not claims that the target architecture has been implemented.

## 1. Executive finding

The existing DT Master implementation remains primarily Node-centric. Infrastructure Node currently owns Bench installation, initialization, service lifecycle and maintenance. Tenant Site directly references Infrastructure Node. Site and app operations use SSH scripts against a fixed Bench path. Marketplace app installation can mutate shared Bench code and reload shared services.

The agreed target makes Bench Environment the runtime owner. Infrastructure Node owns host-level infrastructure. DT Master coordinates Host/Docker adapters, Bench deployment, Tenant Site lifecycle, runtime endpoints and ingress.

The implementation has reusable parts, but its ownership boundaries must be refactored before Docker orchestration is layered over it.

## 2. Script archives — corrected classification

| Path | Audit finding | Proposed disposition |
|---|---|---|
| dt_master/scripts.zip | Historical archive; not consumed by active deployment tooling | **REMOVE** from the active repository in a later cleanup change. Git history preserves it. |
| dt_master/scripts_new.zip | Historical archive; not consumed by active deployment tooling | **REMOVE** from the active repository in a later cleanup change. Git history preserves it. |
| dt_master/scripts/ | Current directory-based script tree referenced by the Python execution contract | **RETAIN**, then refactor into adapter-aligned Node, Bench, Site and Mail execution surfaces. |
| dt_master/scripts/CONTRACTS.md | Documents the active Python-to-shell execution contract | **REVISE** in M4.3 to define the runtime-adapter execution contract. |

The two ZIP archives are not unresolved deployment dependencies. Their removal is repository hygiene, not a runtime migration prerequisite.

## 3. First-pass file-level findings and proposed disposition

### 3.1 Infrastructure Node and Bench lifecycle

**Observed:** api/infrastructure_node.py, Infrastructure Node DocType JSON/JS and scripts/lib/node_lifecycle.sh combine Node provisioning with Bench lifecycle. The Node state machine includes Bench states such as Dependencies Installed, Project Installed, Initialized, Healthy and Running. The Node API exposes install Bench, initialize, prepare, start/stop, update, migrate, backup, restore and remove operations.

**Target:** Infrastructure Node owns host identity, provider/region, OS, SSH access, Docker Engine readiness, capacity, Node health and Node-level services. Bench Environment owns Bench identity, runtime type, framework/runtime composition, services, storage, site membership and Bench lifecycle.

| Component | Proposed disposition | Milestone |
|---|---|---|
| Infrastructure Node DocType/controller | **REFACTOR**: retain Node fields; move Bench fields/actions and Bench state machine | M4.3 |
| Infrastructure Node API | **REFACTOR**: Node-only operations; relocate Bench operations | M4.3 |
| Infrastructure Node form JS | **REFACTOR**: retain Node actions; move Bench actions to Bench Environment UI | M4.3 |
| scripts/lib/node_lifecycle.sh | **REFACTOR/RETAIN**: isolate host prerequisites and Node operations | M4.3 |
| scripts/lib/bench_lifecycle.sh | **REPLACE/RELOCATE** behind Bench runtime adapters | M4.3 |
| scripts/CONTRACTS.md | **REVISE** to adapter contract | M4.3 |

### 3.2 Tenant Site ownership and lifecycle

**Observed:** tenant_site_executor.py and tenant_installed_app_executor.py resolve Node through tenant.node. script_contract.py builds the Bench path from Node fields. Site lifecycle scripts also manipulate shared Supervisor/Bench service configuration.

**Target:** Tenant Site belongs to one Bench Environment and resolves Node through that parent. Site lifecycle operates against a supplied Bench runtime; it must not independently start/stop or regenerate shared Bench services. Bench services and endpoint registration belong to Bench Environment; Nginx/TLS/port allocation belong to Infrastructure Node.

| Component | Proposed disposition | Milestone |
|---|---|---|
| Tenant Site DocType JSON/controller | **EXTEND/REPLACE** relationship with mandatory Bench Environment; migrate old Node link | M4.6 |
| Tenant Site executor | **REFACTOR** to resolve Site → Bench → Node and use runtime adapter | M4.3/M4.6 |
| Tenant Site form JS | **REFACTOR** to dispatch through Bench-aware service | M4.6 |
| scripts/lib/site_lifecycle.sh | **REFACTOR** to site-only lifecycle; remove Bench-wide service mutation | M4.6 |
| scripts/lib/backup.sh | **EXTEND** to use Bench storage/backup references | M4.3/M4.6 |

### 3.3 Marketplace and application execution

**Observed:** scripts/lib/app_lifecycle.sh can use bench get-app, bench install-app, bench build and service reload. A tenant app request can therefore mutate shared Bench code. Marketplace release discovery queries Git tags directly. The app executor can create Extension and Extension Version records from discovered Git information and mark new Extension records Approved.

**Target:** Bench Environment Application is the authoritative inventory of apps available in the shared runtime. Tenant Installed App remains site-specific state. If an app is not available in the parent Bench, DT Master creates an App Installation Request/Deployment Candidate and obtains an immutable NDF-built replacement Bench. Only after availability is verified may site-level installation run. NDF owns technical release descriptors; DT Master owns marketplace approval and commercial governance.

| Component | Proposed disposition | Milestone |
|---|---|---|
| Tenant Installed App DocType | **EXTEND** with controlled request/job state and Bench-availability validation | M4.5 |
| Tenant Installed App executor | **REPLACE execution strategy**; remove shared Bench code mutation | M4.5 |
| Marketplace API | **REFACTOR** around app availability, App Installation Request and Deployment Candidate | M4.5 |
| scripts/lib/app_lifecycle.sh | **SPLIT** site installation from Bench composition/build operations | M4.5 |
| Extension/Extension Version DocTypes | **EXTEND** with immutable NDF descriptor references/digests and synchronized technical metadata | M4.5 |
| Git-tag release discovery | **REPLACE** as release authority with NDF descriptor synchronization | M4.5 |
| Tenant Installed App buttons | **RETAIN** as user actions; change backend orchestration | M4.5 |

### 3.4 Shared execution and supporting files

| Component | Current role | Proposed disposition | Milestone |
|---|---|---|---|
| api/script_contract.py | Builds Node/framework/site/app/mail environments; SSH transport | **SPLIT** into Node, Bench, Site and Mail contexts; retain transport behind adapters | M4.3 |
| api/framework_version.py | Framework dependency preview/application | **REFACTOR** dependency ownership; retain compatibility catalogue | M4.5 |
| api/extensions.py | Marketplace listing and compatibility | **EXTEND** with NDF descriptor metadata and Bench availability | M4.5 |
| controllers/provisioning.py | Tenant bootstrap and API credential provisioning | **EXTEND/REFACTOR** to resolve Site through Bench; migrate credentials | M4.6 |
| hooks.py | App metadata and Frappe hooks | **RETAIN**; extend only for required lifecycle events | M4.6 |
| scripts/lib/runtime.sh | Host user/privilege management | **RETAIN** for Host adapter; Docker gets separate implementation | M4.3 |
| scripts/lib/backup.sh | Backup/restore/retention | **EXTEND** for storage profiles and backup references | M4.3/M4.6 |
| Script Execution/Execution Log | Command execution and output history | **RETAIN/EXTEND** for adapter traceability | M4.3 |
| Node Action Log | General Node/Site operational history | **RETAIN**; Bench Deployment gets dedicated stage history | M4.3 |
| Mail Server executor/DocTypes | Separate mail workload hosted on Node | **RETAIN** as Node workload; adapt Node access contract if needed | Outside Bench ownership |

### 3.5 Tests and migration infrastructure

Existing tests/test_automation_contracts.py, dt_master/testing/docker_sandbox.py, dt_master/testing/integration.py and tests/test_migration_plan.py are useful baseline assets. Current integration coverage exercises the old Node → Tenant Site relationship and SSH/shell execution contract. It does not prove the new Bench Environment hierarchy or Docker adapter.

| Component | Proposed disposition | Required target coverage |
|---|---|---|
| Existing automation contract tests | **RETAIN/REFACTOR** | Node/Bench/Site adapter contract tests |
| Docker sandbox/integration runner | **EXTEND** | Multiple Benches per Node; multiple Sites per Bench; Host and Docker adapters |
| Migration plan tests | **EXTEND** | Host Bench grouping, relationship migration, idempotency and recovery |
| patches.txt | **EXTEND** when implementation is authorized | Ordered, idempotent, preflighted and resumable M4 patches |
| Historical script ZIP archives | **REMOVE** from active tree | No active deployment consumers |

Required tests include multiple Bench Environments on one Node, multiple Tenant Sites on one Bench, Site → Bench → Node resolution, app availability enforcement, replacement Bench/cutover/rollback, descriptor digest validation and migration recovery.

## 4. API dependency and caller graph

### 4.1 Infrastructure and Bench operations

    Infrastructure Node form JS
      └── api.infrastructure_node
           ├── Node state/transition validation
           ├── Framework Version + Framework Version Dependency
           ├── api.script_contract
           │    ├── SSH configuration
           │    ├── runtime/framework environment
           │    └── run_remote_script
           └── scripts/bin/node + scripts/lib/node_lifecycle.sh
                and current Bench lifecycle scripts

**Finding:** UI visibility is a presentation gate only. Backend authorization and state validation must remain authoritative. Bench operations are exposed under the Node API and must move to Bench Environment operations.

### 4.2 Tenant Site operations

    Tenant Site form JS
      └── api.tenant_site_executor
           ├── Tenant Site + Infrastructure Node (tenant.node)
           ├── Site state/status transition rules
           ├── app rows + Extension Version + Extension (install_apps)
           ├── api.script_contract.tenant_environment
           └── SSH → scripts/bin/site → site_lifecycle.sh

**Finding:** The executor checks Tenant Site and Node write permission, but execution context and script contract are Node/Bench-coupled. Target caller chain is Tenant Site → Bench Environment → Infrastructure Node → runtime adapter.

### 4.3 Marketplace and app operations

    DT Tenant marketplace request / Tenant Installed App form JS
      ├── api.marketplace
      │    ├── Extension + Extension Version approval lookup
      │    ├── Git tag discovery
      │    ├── Tenant Installed App child-row mutation
      │    └── frappe.enqueue(queue="long", timeout=1500)
      │          └── api.tenant_installed_app_executor
      │               ├── Tenant Site → Infrastructure Node
      │               ├── Extension/Extension Version
      │               ├── api.script_contract.app_environment
      │               └── SSH → scripts/bin/app → app_lifecycle.sh
      └── Tenant Installed App child-row buttons
           └── executor direct API methods

**Findings:**
- Marketplace install/upgrade/uninstall uses the long queue; asynchronous only through this wrapper.
- Desk child-row buttons call executor endpoints directly, bypassing the queue wrapper and running SSH in the web request.
- Executor itself is synchronous. The same app action has two execution paths with different timeout and worker behavior.
- Marketplace writes Pending before enqueueing but does not establish an atomic per-app operation claim.
- The Uninstalled-row retry path calls the queue helper without the required action argument.
- The queue job ID is returned but is not consistently persisted to Tenant Installed App.last_job_id.

**Target:** All mutating app actions enter one DT Master request service. It validates authorization, Site/Bench relationship and Bench app availability, creates an idempotent App Installation Request, then schedules execution. Worker execution cannot bypass the same validation.

### 4.4 Mail operations

    Mail Server / Mail User form actions
      └── api.mail_server_executor
           ├── Mail Server + Infrastructure Node
           ├── Mail Server state transition
           ├── api.script_contract.mail_environment
           └── SSH → scripts/bin/mail → mail lifecycle scripts

Mail Server is a separate Node workload, not a Bench Environment child. Preserve that ownership boundary while applying the same operation, authorization and secret-transport standards.

### 4.5 Provisioning, subscription, billing and capability APIs

The reviewed API surface also includes controllers/provisioning.py (tenant bootstrap and remote API user creation), api/subscription.py (plan listing, billing history and plan mutation), api/billing.py (invoice PDF, gateway discovery and payment operations), api/tenant_capabilities.py (plan/modules/roles/machine constraints), api/extensions.py (marketplace listing and per-FQDN installed state), and api/framework_version.py (dependency preview/mutation).

These endpoints are part of the privileged control plane and must be included in the authorization review even when they are not M4 runtime execution surfaces.

## 5. Background jobs and concurrency

### 5.1 Current execution modes

| Operation | Current mode | Finding |
|---|---|---|
| Marketplace app install/upgrade/uninstall | Frappe long queue, timeout 1500 seconds | Asynchronous only through marketplace wrapper |
| Tenant Installed App form actions | Direct whitelisted executor | Synchronous SSH in web request |
| Tenant Site create/install/verify/suspend/resume/destroy/proxy | Direct whitelisted executor | Synchronous SSH in web request |
| Infrastructure Node/Bench operations | Direct whitelisted executor | Synchronous SSH in web request |
| Mail Server operations | Direct whitelisted executor | Synchronous SSH in web request |
| Tenant provisioning | Direct whitelisted controller | Synchronous remote HTTP/API sequence |
| DT Master scheduled tasks | No active scheduler_events registration in hooks.py | Scheduler examples are commented out |

### 5.2 Concurrency and idempotency risks

1. **No shared operation coordinator:** executors own separate state checks and transitions; no durable operation/lock spans Node, Bench, Site and app actions.
2. **Check-then-act races:** reviewed paths do not consistently use row locks or atomic compare-and-set claims. Concurrent requests may pass the same initial state check.
3. **Marketplace duplicate race:** API checks for a child row, marks/appends Pending, commits and enqueues. Concurrent requests may both pass the duplicate check or schedule overlapping work.
4. **Split app execution paths:** direct Desk actions bypass queue wrapper. UI state hiding does not prevent direct RPC calls or guarantee a queued action has claimed the operation.
5. **Partial commits:** intermediate state is committed before remote work and final state/logs afterwards. Failures between commits and remote side effects can leave ambiguous outcomes.
6. **No universal idempotency key:** APIs do not consistently store a request identifier that deduplicates UI, DT Tenant or network retries.
7. **No operation-level mutual exclusion:** Site and Bench-wide actions can overlap; multiple Site actions on one Bench can mutate shared service configuration.
8. **Unbounded SSH wait:** run_remote_script calls proc.wait() without its own timeout/cancellation handling. Marketplace has an RQ timeout, but direct web requests and other operations lack consistent deadlines.
9. **Realtime correlation:** event names are shared by action category. UI code calls frappe.realtime.off(event) globally, which can interfere with concurrent listeners in one browser session.

### 5.3 Proposed target disposition

- Introduce a durable operation/request model: operation type, target, actor, idempotency key, state, queue/job reference, timestamps, attempts, lock/lease, result and safe diagnostic summary.
- Route every mutating UI/API action through one server-side operation service; no direct UI-to-executor bypass.
- Use atomic claims/row locking or database-backed compare-and-set before dispatch.
- Define conflicts: Node maintenance vs Bench deployment; Bench deployment vs Site lifecycle; Site lifecycle vs app installation; concurrent actions on one Site.
- Run long SSH/Docker work in workers or a Node-side execution component, not web requests. Define queue classes, deadlines and cancellation.
- Persist operation/job IDs; reconcile stale Running/Queued state after worker loss.
- Make remote stages idempotent and reconcile state after timeout before retrying side-effecting work.
- Correlate realtime updates by operation ID and authorize subscriptions.
- Keep operation coordination in DT Master; NDF build workflows retain their workflow/run identity and are tracked as Deployment Build.

**Milestone placement:** operation schema and execution contract in M4.1; Node/Bench orchestration in M4.3; app request execution in M4.5; Site lifecycle in M4.6.

## 6. Permissions and authorization

### 6.1 DocType permissions observed

Reviewed Infrastructure Node, Tenant Site, Extension and Extension Version DocTypes grant CRUD/read/export/print/report/share to System Manager only. Tenant Installed App is a child table with no independent permissions. This is a restrictive Desk permission baseline, but it does not secure whitelisted Python methods by itself.

### 6.2 Backend authorization findings

- @frappe.whitelist() makes a method callable through Frappe RPC; it does not establish intended business role, customer ownership or document-level authorization.
- Some executor paths explicitly call doc.check_permission("write"); preserve these checks.
- infrastructure_node._run_bench_action also uses frappe.only_for("System Manager"). Other privileged Node actions rely primarily on document write permission.
- tenant_site_executor checks Tenant Site and Node write permission.
- tenant_installed_app_executor checks Tenant Site and Node write permission.
- mail_server_executor checks Mail Server and Node write permission.
- Marketplace request methods mutate Tenant Site child rows with ignore_permissions=True without an explicit caller/tenant authorization check.
- controllers.provisioning.provision_tenant changes Tenant Site status and provisions a remote System Manager API user without an explicit document permission or role check at its public entry point.
- Subscription APIs read billing data and mutate Subscription without explicit caller-to-tenant ownership checks. change_subscription_plan uses ignore_permissions=True.
- Billing APIs load Sales Invoices and perform payment actions without explicit invoice/customer authorization checks in the reviewed methods. download_invoice_pdf temporarily switches to Administrator before fetching the print, without first validating caller access.
- tenant_capabilities.get_tenant_capabilities and extensions.get_extensions accept an FQDN and return tenant-specific data without explicit caller-to-tenant authorization.
- framework_version.apply_selected_dependencies is a mutating whitelisted API and should have an explicit privileged-role gate.
- Frontend button visibility and role checks are UX only. Authorization must be repeated server-side.

**Priority: High.** The callable API surface is broader than System Manager-only DocType permissions suggest. Target roles must distinguish platform operators, tenant service identities, tenant administrators, marketplace publishers and read-only users.

### 6.3 Proposed authorization contract

- Define an API authorization matrix by operation, target DocType, actor class and scope.
- Platform infrastructure/deployment operations require explicit platform operator role and document permission.
- Tenant-facing operations verify authenticated tenant identity and ownership from trusted context; caller-supplied FQDN is not proof of ownership.
- Tenant API service identities receive narrowly scoped methods and cannot invoke Node/Bench infrastructure operations.
- Validate parent-child ownership (Site → Bench → Node; child row belongs to supplied parent) before every operation.
- Do not use ignore_permissions=True as a substitute for an authorization decision. For privileged writes, authorize first and record actor/reason.
- Check invoice/customer/subscription ownership before PDF, payment, billing-history or plan-change actions.
- Avoid switching to Administrator for reads unless caller authorization has already been checked and the operation is narrowly constrained.
- Add negative tests for Guest, ordinary authenticated user, tenant service user, tenant administrator, marketplace publisher and System Manager.

## 7. Secrets and sensitive data

### 7.1 Current storage and flow

Tenant Site stores ordinary Data fields for tenant_api_key, tenant_api_secret, admin_password, mysql_root_user_name and mysql_root_user_password. These are read by provisioning, tenant API calls, backup/restore and script environment builders. Ordinary Data fields are not an acceptable secret store. System Manager-only DocType permissions do not provide secret-at-rest semantics, field-level isolation or safe export/report behavior.

Mail user passwords are retrieved through Frappe's Password field API (get_password), which is a better storage pattern, but still needs provider abstraction, access policy and rotation controls.

### 7.2 Secret handling and transport findings

1. **Shell command transport:** script_contract.build_remote_command serializes environment values into shell export statements and passes the complete command string to SSH. Passwords are embedded in the remote command payload rather than delivered through a dedicated secret channel. This creates process/diagnostic exposure risk.
2. **Output redaction:** run_remote_script redacts values whose environment key contains PASSWORD, SECRET or TOKEN before publishing/storing stdout/stderr. Useful defense-in-depth, but cannot guarantee redaction of transformed, encoded or otherwise derived secrets.
3. **Traceback/error persistence:** app/site/mail executors store error text and tracebacks in DocType fields or Error Log. A secret returned by a remote command or included in an exception could bypass stdout/stderr redaction.
4. **Realtime output:** output is published through realtime events. Redaction happens before publish in the shared runner, but event authorization/correlation and recipient identity in worker context require verification.
5. **Credential scope:** Tenant Site holds database root credentials and Tenant API credentials together, coupling unrelated trust boundaries.
6. **Provisioning:** tenant bootstrap creates a remote user with the System Manager role and stores its API key/secret on Tenant Site. Target should use a dedicated least-privilege service identity and protected references.
7. **Exports/reports:** ordinary Data fields may be exposed through authorized exports, reports, API serialization or broad document reads. Hiding fields in the form is not a fix.
8. **Rotation:** no common provider-backed secret rotation/version lifecycle was found for Node, Bench, Site, database, mail and external service credentials.

### 7.3 Proposed target disposition

- Implement the already-agreed standalone Runtime Secret model/provider abstraction.
- Replace ordinary credential fields with protected references; migrate values without duplicating plaintext and retire legacy fields after consumer cutover.
- Separate Tenant API service credentials from database administrator and Frappe Administrator/bootstrap credentials.
- Resolve secrets only inside authorized server-side execution paths.
- Never place secret values in Deployment Descriptors, Compose definitions, command-line arguments, logs, realtime messages or ordinary DocType fields.
- Replace SSH command-string secret injection with an adapter-safe secret delivery channel.
- Sanitize stdout/stderr, exceptions, tracebacks and API error responses.
- Record secret access audit events (actor, purpose, provider reference/version) without logging values.
- Implement rotation as a lifecycle operation with validation, staged reference switch, recovery window and outcome record.

**Milestone placement:** secret schema/provider and execution contract in M4.1; protected field migration and consumers in M4.3/M4.6; mail credentials remain under the separate Mail Server workload.

## 8. Cross-repository DT Tenant caller and authorization findings

DT Tenant (NdfTechLabs/dt_tenant, branch develop) is a service client of DT Master.

### 8.1 Caller graph

- Marketplace: DT Tenant Add Ons page → dt_tenant.api.marketplace → Master api.marketplace → long queue → Master app executor → Tenant status update → DT Tenant publish_install_update realtime event.
- Marketplace listing: DT Tenant api.extensions → Master api.extensions.get_extensions; request includes FQDN and Frappe version.
- Subscription/billing: DT Tenant api.tenant_subscription → Master api.subscription and api.billing methods.
- Capability synchronization: DT Tenant cron every six hours → capability_scheduler.run_scheduled_sync → capability_sync.resync_capabilities → Master api.tenant_capabilities.get_tenant_capabilities → local role/module/user updates.

### 8.2 DT Tenant findings

- hooks.py actively schedules capability synchronization every six hours. A one-hour failure cooldown is present. This corrects the earlier broad assumption: DT Master hooks has no active scheduler registration, but DT Tenant has an active scheduler.
- subscription_guard.enforce_subscription is commented out as a before_request hook; it is not an active global request gate in this snapshot.
- settings.configure accepts arbitrary keyword attributes that exist on Tenant Settings and saves with ignore_permissions=True. Restrict to provisioning service/System Manager and a strict field allowlist.
- capability_sync.resync_capabilities is whitelisted and can synchronize roles/modules and update every enabled System User. It lacks explicit role restriction at entry. Treat as service-only or tightly privileged, serialized and auditable.
- marketplace.publish_install_update accepts caller-supplied extension/status/error/site values and publishes realtime. Restrict to authenticated Master service identity and correlate with a valid operation.
- system.is_app_installed and system.has_active_system_manager reject Guest but otherwise accept any authenticated caller. Restrict to bootstrap service identity.
- Marketplace and billing proxy methods lack explicit tenant role gates; Master must independently authorize service identity and resource ownership.
- DT Tenant user hooks actively assign/synchronize permissions and must be considered alongside capability synchronization.

### 8.3 Authorization risk register

| ID | Surface | Finding | Severity | Required control |
|---|---|---|---|---|
| AUTH-01 | Master marketplace APIs | FQDN-based lookup and child-row mutation without explicit caller/tenant authorization | High | Bind service credential to one Tenant Site; validate ownership and role |
| AUTH-02 | Master subscription APIs | Billing history and plan mutation lack tenant ownership checks | High | Derive subscription from authenticated tenant identity |
| AUTH-03 | Master billing APIs | Invoice/payment operations lack explicit invoice/customer ownership checks | Critical | Authorize invoice/customer/subscription before every action |
| AUTH-04 | Master invoice PDF | Administrator elevation without pre-validating caller invoice access | Critical | Authorize first; avoid broad elevation |
| AUTH-05 | Master provisioning API | Tenant bootstrap and privileged API user creation lack explicit entry authorization | High | Restrict to provisioning service/operator |
| AUTH-06 | Master tenant capability/extension APIs | Tenant information retrieved by caller-supplied FQDN | High | Resolve tenant from service identity, not FQDN alone |
| AUTH-07 | Tenant settings.configure | Arbitrary settings attributes saved with ignore_permissions=True | Critical | Role/service check and allowlist; protect Master URL/credentials |
| AUTH-08 | Tenant capability resync | Authenticated caller can trigger role/module/user synchronization | Critical | Service-only, locked, audited, idempotent |
| AUTH-09 | Tenant marketplace actions | Authenticated caller can request install/upgrade/uninstall | High | Tenant Manager gate; Master independently authorizes |
| AUTH-10 | Tenant billing proxy | Authenticated caller can invoke billing/payment proxy | High | Tenant billing role gate; Master checks invoice ownership |
| AUTH-11 | Tenant publish_install_update | Caller-controlled realtime payload | High | Authenticate Master; validate operation; scope recipients |
| AUTH-12 | Tenant bootstrap helpers | Any authenticated identity can call bootstrap helpers | Medium/High | Dedicated bootstrap service identity |

Severity indicates potential impact from the code path, not evidence of an incident.

## 9. Required cross-repository API contract

Before M4.3/M4.5/M4.6 implementation, define:

- **Service identity:** each Tenant Site has a dedicated service identity at Master, mapped server-side to exactly one Tenant Site and its current Bench Environment.
- **Authentication:** rotatable credentials; no shared System Manager account; no caller-controlled tenant identity.
- **Authorization scopes:** marketplace read, app installation request, app upgrade/uninstall, subscription read, billing read, payment initiation, capability fetch/sync and bootstrap.
- **Request envelope:** request ID/idempotency key, authenticated actor/service identity, operation, target resource and schema version.
- **Tenant binding:** Master derives Tenant Site from authenticated service identity, then resolves Bench Environment and Node through the authoritative hierarchy.
- **Audit:** actor/service identity, tenant/site, action, authorization result, request ID, timestamp and safe outcome.
- **Replay protection:** idempotency and replay-safe state transitions.
- **Realtime:** operation-ID correlation, authenticated source and recipient scoping.
- **Secrets:** protected references, rotation lifecycle, no secret values in payloads/logs.
- **Error contract:** machine-readable safe error code/summary; no raw traceback or credential disclosure.

## 10. Consolidated disposition by milestone

| Milestone | Audit-derived scope |
|---|---|
| M4.1 Integration Contract and Architecture | Finalize adapter boundaries, operation/idempotency/locking contract, API authorization matrix, secret provider/transport contract and NDF descriptor/deployment contracts |
| M4.2 Runtime Infrastructure Enhancements (frappe_docker) | Publish NDF runtime/service/deployment contracts consumed by DT Master |
| M4.3 Docker Orchestration Engine (dt_master) | Refactor Node/Bench ownership; implement adapters, durable operations, worker execution, timeouts, endpoint registration and safe secret resolution |
| M4.4 Product Image Deployment (dt_master) | Candidate/build/descriptor verification and immutable product deployment |
| M4.5 Runtime Application Management (Both) | Enforce Bench app availability; App Installation Requests; replace Git-tag release discovery and shared-Bench app mutation |
| M4.6 Tenant Site and DT Tenant Integration (Both + dt_tenant) | Migrate Site → Bench relationship; refactor Site lifecycle/provisioning; migrate tenant credentials and enforce tenant-scoped authorization |
| M4.7 Integration Testing and Production Readiness | End-to-end concurrency, permissions, secret handling, rollback, migration and recovery validation |

## 11. Updated audit position and M4.1 completion record

The full Git tree inventories for dt_master:develop and dt_tenant:develop have
been compared against the consolidated disposition matrix. The matrix now
records grouped dispositions for additional DocTypes, API/controller paths,
shell libraries/dispatchers, UI/workspaces, configuration, tests and assets.
The detailed new findings and path groups are recorded in
docs/migrations/M4_FILE_DISPOSITION_MATRIX.md.

Completed at source-review level:
1. DT Master UI/workspace/role impact review, including the corrected Tenant
   Subscription visibility finding.
2. Principal DocType permission and sensitive-field consumer review.
3. Principal realtime worker/event source review.
4. DT Tenant API caller, role, lifecycle, workspace and page source review.
5. Full tree inventory on both requested develop branches and matrix extension.

M4.1 completion record:
The cross-repository contracts, caller/security audit and NDF artifact/deployment architecture were reconciled in the canonical specification. The final 13-point acceptance review was completed and the platform owner explicitly approved M4.1 completion on 2026-11-09. No M4.1 acceptance work remains open.

Live Frappe permission behavior, worker identity, realtime delivery, exports,
reports, print and backup-surface tests are implementation/runtime verification
requirements. They are deferred to the relevant implementation/testing stage;
they are not a reason to build a separate CI test platform as part of this
architecture audit.

M4.1 is Complete (2026-11-09). The source audit did not change application code, DocTypes, migrations, scripts or runtime configuration. Implementation authorization remains a separate, ungranted gate.
