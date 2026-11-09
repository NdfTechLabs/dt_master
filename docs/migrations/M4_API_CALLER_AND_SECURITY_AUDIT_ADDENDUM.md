# M4.1 — DT Master API Caller and Security Audit Addendum

**Related audit:** docs/migrations/M4_DT_MASTER_FILE_LEVEL_AUDIT.md  
**DT Master baseline:** develop at 4d7cd677155f5ccee9e915b8508a9276f382ef9d  
**DT Tenant baseline:** version-1 (repository snapshot inspected during this audit)  
**Status:** Read-only audit findings recorded; implementation not authorized.

## 1. Cross-repository API caller graph

The DT Tenant repository is NdfTechLabs/dt_tenant, branch version-1. Its callers confirm that DT Master is being used as a remote control-plane API by the tenant application.

### 1.1 Marketplace

    DT Tenant Add Ons page
      └── dt_tenant.api.marketplace.install_extension / upgrade_extension / uninstall_extension
           └── Tenant Settings master_url + api_key/api_secret
                └── DT Master api.marketplace.install_extension_for_tenant /
                    upgrade_extension_for_tenant / uninstall_extension_for_tenant
                     ├── Tenant Site lookup by caller-supplied FQDN
                     ├── Extension / Extension Version lookup
                     ├── Tenant Installed App child-row mutation
                     └── frappe.enqueue(long)
                          └── DT Master tenant_installed_app_executor
                               └── SSH / app lifecycle scripts
                                    └── Tenant Site app state update
                                         └── DT Tenant api.marketplace.publish_install_update
                                              └── extension_install_update realtime event

The DT Tenant Add Ons page listens to extension_install_update and renders installation status. The caller supplies the extension name; DT Tenant derives the FQDN from its own site URL and authenticates to Master using the stored service credentials.

### 1.2 Marketplace listing

    DT Tenant Add Ons page
      └── dt_tenant.api.extensions.get_marketplace_extensions
           └── DT Master api.extensions.get_extensions
                ├── Approved/Public Extension records
                ├── approved Extension Version compatibility
                └── Tenant Site installed_apps state

The Master endpoint receives the FQDN and Frappe version from the tenant caller. The tenant UI performs compatibility presentation, but Master must enforce compatibility and Bench availability as authoritative server-side rules.

### 1.3 Subscription and billing

    DT Tenant Tenant Subscription page
      └── dt_tenant.api.tenant_subscription
           ├── get_available_plans → Master api.subscription.get_available_plans
           ├── get_billing_history → Master api.subscription.get_billing_history
           ├── download_invoice → Master api.billing.download_invoice_pdf
           ├── change_subscription_plan → Master api.subscription.change_subscription_plan
           ├── get_payment_gateways → Master api.billing.get_payment_gateways
           ├── get_payment_methods → Master api.billing.get_payment_methods
           ├── pay_invoice → Master api.billing.pay_invoice
           ├── create_payment_method → Master api.billing.create_payment_method
           └── get_encryption_key → Master api.billing.get_encryption_key

These are authenticated service-to-service requests using Tenant Settings credentials. DT Master must derive and verify the tenant/subscription/customer relationship from the authenticated service identity. An FQDN or invoice name in the request body is not sufficient authorization.

### 1.4 Capability synchronization

    DT Tenant scheduler (cron: every 6 hours)
      └── capability_scheduler.run_scheduled_sync
           └── capability_sync.resync_capabilities
                ├── capabilities.fetch_capabilities
                │    └── Master api.tenant_capabilities.get_tenant_capabilities
                ├── lifecycle.enforce_lifecycle
                ├── sync_tenant_roles / sync_tenant_modules
                └── apply_capabilities_to_user for every enabled System User

The scheduler is actively registered in DT Tenant hooks.py. It runs every six hours when capability sync is enabled and credentials exist, and has a one-hour failure cooldown. This corrects the earlier assumption that the whole platform had no scheduled task registration: DT Master hooks has no active scheduler registration, but DT Tenant has an active six-hour capability sync.

## 2. Additional authorization findings from DT Tenant

### 2.1 Tenant API endpoints

The reviewed DT Tenant API methods use frappe.whitelist() but generally do not apply an explicit role/permission check at their entry point.

- marketplace.install_extension, upgrade_extension and uninstall_extension allow any authenticated caller who can invoke the endpoint to request privileged marketplace changes. The intended actor should be Tenant Manager or another explicitly authorized tenant role.
- tenant_subscription methods proxy billing and payment operations to Master using the tenant-wide service credentials. They need tenant-side role gates as well as Master-side identity/ownership checks.
- settings.configure accepts arbitrary keyword arguments, assigns any attribute that exists on Tenant Settings and saves with ignore_permissions=True. This is a privileged configuration mutation and must be restricted to a platform-provisioning service identity or an explicit System Manager-only operation with an allowlist of mutable fields.
- capability_sync.resync_capabilities can trigger global role/module synchronization and user updates. It is called by the scheduler and provisioning flow, but its whitelisted public entry point has no explicit role gate. It must not be callable by an ordinary tenant user.
- marketplace.publish_install_update accepts caller-supplied extension/status/error/site data and publishes a realtime event. It should be restricted to the authenticated Master service identity, validate the source operation and tenant/site context, and scope the event to authorized recipients.
- system.is_app_installed and system.has_active_system_manager reject Guest but do not restrict the authenticated caller to the provisioning service identity. Their intended use is Master-to-Tenant bootstrap and should be narrowly scoped.

### 2.2 Hooks and lifecycle enforcement

- DT Tenant hooks.py actively schedules capability synchronization every six hours.
- The subscription_guard.enforce_subscription before_request hook is commented out. Therefore it is not a globally active request gate in this snapshot.
- The app permission configuration and auth hook examples are commented out.
- User document hooks actively call assign_roles after insert and sync_tenant_permissions before save. These need review alongside capability sync and lifecycle enforcement because they mutate user access state.

### 2.3 Capability sync impact

resync_capabilities fetches the Master capability snapshot, enforces subscription lifecycle, synchronizes tenant roles/modules, then iterates over enabled System Users and rewrites their roles and blocked modules. It also deletes obsolete tenant role/module records and their child references.

This is a high-impact administrative operation. It must be service-only or explicitly authorized, serialized against concurrent syncs, auditable and safe to retry. A normal authenticated tenant user must not be able to trigger it.

## 3. Revised authorization risk register

| ID | Surface | Finding | Severity | Required control |
|---|---|---|---|---|
| AUTH-01 | DT Master marketplace APIs | FQDN-based lookup and child-row mutation without explicit caller/tenant authorization | High | Authenticate service identity; map credential to one Tenant Site; verify target ownership and role |
| AUTH-02 | DT Master subscription APIs | Billing history and plan mutation lack explicit tenant ownership checks | High | Derive subscription from authenticated tenant identity; verify plan transition and actor |
| AUTH-03 | DT Master billing APIs | Invoice/payment operations lack explicit invoice/customer ownership checks | Critical | Authorize invoice/customer/subscription before every read, payment, method or PDF action |
| AUTH-04 | DT Master invoice PDF | Temporarily switches to Administrator without pre-validating caller access | Critical | Authorize first; avoid broad Administrator elevation |
| AUTH-05 | DT Master provisioning API | Tenant bootstrap and privileged API user creation lack explicit entry-point authorization | High | Restrict to platform provisioning role/service identity; enforce state and ownership |
| AUTH-06 | DT Master tenant capabilities/extensions | Tenant-specific information retrieved using caller-supplied FQDN without explicit tenant authorization | High | Resolve tenant identity from service credential, not caller-supplied FQDN alone |
| AUTH-07 | DT Tenant settings.configure | Arbitrary Tenant Settings attributes can be changed with ignore_permissions=True | Critical | Explicit role/service check and strict field allowlist; protect Master URL and credentials |
| AUTH-08 | DT Tenant capability resync | Authenticated caller can invoke role/module/user synchronization | Critical | Scheduler/provisioning service-only entry point; lock, audit and idempotency |
| AUTH-09 | DT Tenant marketplace actions | Any authenticated user may request install/upgrade/uninstall | High | Tenant Manager role gate; Master independently authorizes |
| AUTH-10 | DT Tenant billing proxy | Any authenticated user may invoke billing/payment proxy endpoints | High | Tenant billing role gate; Master independently authorizes invoice ownership |
| AUTH-11 | DT Tenant publish_install_update | Caller-controlled event payload and realtime publication | High | Master-service authentication, payload validation and recipient scoping |
| AUTH-12 | DT Tenant bootstrap helpers | Guest is rejected, but any authenticated identity may call provisioning helper endpoints | Medium/High | Dedicated bootstrap service identity and narrow endpoint contract |

Severity reflects potential impact from the code path, not evidence that an incident has occurred.

## 4. Updated background-job and scheduling picture

| Component | Active behavior | Audit disposition |
|---|---|---|
| DT Master marketplace | Long-queue job for app actions through marketplace wrapper | **REFACTOR** to unified operation/request service in M4.5 |
| DT Master other Node/Site/Mail APIs | Synchronous SSH or remote HTTP in request | **REFACTOR** to worker/Node-side execution with deadlines in M4.3/M4.6 |
| DT Master hooks.py | No active scheduler_events registration | **RETAIN** unless later operations require scheduled reconciliation |
| DT Tenant capability scheduler | Active cron every six hours; calls resync_capabilities | **RETAIN/REFINE** with service-only authorization, lock, idempotency and audit |
| DT Tenant manual capability resync | Whitelisted endpoint also callable directly | **RESTRICT** to authorized System Manager/provisioning service |

## 5. Secret and service identity implications

1. DT Tenant stores Master API credentials in the singleton Tenant Settings and uses them for marketplace, billing and capability calls. Confirm their field types and encryption behavior; never expose them through generic settings APIs or normal serialization.
2. These credentials are tenant-wide service credentials. They must identify the tenant to Master independently of the caller-supplied FQDN.
3. Master must not trust a request body FQDN as proof that the authenticated service may operate on that site.
4. The bootstrap flow currently creates a remote Master API user with the System Manager role. Replace this with a dedicated least-privilege service identity and an explicit API scope.
5. Provisioning currently generates and stores the service API key/secret in ordinary Tenant Site Data fields on Master. This remains part of the Runtime Secret migration.
6. The DT Tenant publish-install-update call returns status/error data to realtime. Scope this to the correct tenant audience and ensure errors are sanitized.
7. Protect Master URL and API credential configuration against arbitrary mutation. A compromised settings endpoint could redirect outbound requests or replace the trusted Master destination.

## 6. Required cross-repository API contract

Before M4.3/M4.5/M4.6 implementation, define the following shared contract:

- **Service identity:** each Tenant Site has a dedicated service identity at Master, mapped server-side to exactly one Tenant Site and its current Bench Environment.
- **Authentication:** short-lived or rotatable credentials; no shared System Manager account; no caller-controlled tenant identity.
- **Authorization:** separate scopes for marketplace read, app installation request, app upgrade/uninstall, subscription read, billing read, payment initiation, capability fetch, capability sync and bootstrap.
- **Request envelope:** request ID/idempotency key, authenticated actor/service identity, operation, target resource and schema version.
- **Tenant binding:** Master derives the Tenant Site from authenticated service identity, then resolves Bench Environment and Node through the authoritative hierarchy.
- **Audit:** actor/service identity, tenant/site, action, authorization result, request ID, timestamp and safe outcome.
- **Replay protection:** idempotency and replay-safe state transitions for mutating requests.
- **Realtime:** operation-ID correlation, authenticated source and recipient scoping; no untrusted tenant event broadcast.
- **Secrets:** credentials stored as protected references, rotated through lifecycle operations and excluded from payloads/logs.
- **Error contract:** safe machine-readable error code and diagnostic summary; no raw traceback or credentials returned to tenant clients.

## 7. Audit status after this addendum

The API module inventory for DT Master has been reviewed, and the principal UI-to-API-to-executor flows have been traced. The DT Tenant repository was inspected to validate its callers and reveal the active capability scheduler.

Still outstanding before declaring the entire file-level audit complete:
1. Full UI/workspace and role-definition impact review in both repositories.
2. Complete DocType field-level permission and sensitive-field consumer inventory.
3. Verification of realtime worker session identity and event subscription authorization.
4. Confirmation of Tenant Settings credential field types and all service-credential consumers.
5. Final disposition matrix covering all relevant files, not only the primary execution path.

No application code, DocTypes, migrations, scripts or runtime configuration were changed.
