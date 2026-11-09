# M4.1 — DT Master API Caller and Security Audit Addendum

**Related audit:** docs/migrations/M4_DT_MASTER_FILE_LEVEL_AUDIT.md  
**DT Master baseline:** develop at 4d7cd677155f5ccee9e915b8508a9276f382ef9d  
**DT Tenant baseline:** develop  
**Status:** Read-only findings; implementation not authorized.

## 1. Cross-repository API caller graph

DT Tenant (NdfTechLabs/dt_tenant) is a service client of DT Master.

### 1.1 Marketplace

    DT Tenant Add Ons page
      └── dt_tenant.api.marketplace.install_extension / upgrade_extension / uninstall_extension
           └── Tenant Settings master_url + api_key/api_secret
                └── DT Master api.marketplace.install_extension_for_tenant /
                    upgrade_extension_for_tenant / uninstall_extension_for_tenant
                     ├── Tenant Site lookup by caller-supplied FQDN
                     ├── Extension + Extension Version lookup
                     ├── Tenant Installed App child-row mutation
                     └── frappe.enqueue(long)
                          └── tenant_installed_app_executor
                               └── SSH / app lifecycle scripts
                                    └── Tenant Site status update
                                         └── DT Tenant publish_install_update
                                              └── extension_install_update realtime

The DT Tenant Add Ons page listens to extension_install_update. DT Tenant derives FQDN from its own site URL and authenticates to Master using stored service credentials.

### 1.2 Marketplace listing

    DT Tenant api.extensions.get_marketplace_extensions
      └── DT Master api.extensions.get_extensions
           ├── Approved/Public Extension records
           ├── approved Extension Version compatibility
           └── Tenant Site installed_apps state

Master receives FQDN and Frappe version from the tenant caller. Tenant UI compatibility is presentation only; Master must enforce compatibility and Bench availability.

### 1.3 Subscription and billing

    DT Tenant api.tenant_subscription
      ├── get_available_plans → Master api.subscription.get_available_plans
      ├── get_billing_history → Master api.subscription.get_billing_history
      ├── download_invoice → Master api.billing.download_invoice_pdf
      ├── change_subscription_plan → Master api.subscription.change_subscription_plan
      ├── get_payment_gateways → Master api.billing.get_payment_gateways
      ├── get_payment_methods → Master api.billing.get_payment_methods
      ├── pay_invoice → Master api.billing.pay_invoice
      ├── create_payment_method → Master api.billing.create_payment_method
      └── get_encryption_key → Master api.billing.get_encryption_key

These are authenticated service-to-service requests. Master must derive and verify tenant/subscription/customer relationships from authenticated service identity. FQDN or invoice name in a request body is not sufficient authorization.

### 1.4 Capability synchronization

    DT Tenant scheduler (cron every 6 hours)
      └── capability_scheduler.run_scheduled_sync
           └── capability_sync.resync_capabilities
                ├── capabilities.fetch_capabilities
                │    └── Master api.tenant_capabilities.get_tenant_capabilities
                ├── lifecycle.enforce_lifecycle
                ├── sync_tenant_roles / sync_tenant_modules
                └── apply_capabilities_to_user for enabled System Users

DT Tenant hooks.py actively registers this six-hour scheduler. It checks the opt-in setting and credentials and has a one-hour failure cooldown. This corrects the broad assumption that the platform has no scheduled tasks: DT Master has no active scheduler registration in its hooks, while DT Tenant does.

## 2. Additional DT Tenant authorization findings

The reviewed DT Tenant APIs use frappe.whitelist() but generally have no explicit role/permission check at entry.

- marketplace.install_extension, upgrade_extension and uninstall_extension allow any authenticated caller to request privileged marketplace changes. Intended actor should be Tenant Manager or another explicitly authorized tenant role.
- tenant_subscription methods proxy billing/payment operations with tenant-wide service credentials. They need tenant-side role gates and independent Master-side ownership checks.
- settings.configure accepts arbitrary keyword arguments, writes attributes found on Tenant Settings and saves with ignore_permissions=True. Restrict to provisioning service/System Manager and a strict field allowlist.
- capability_sync.resync_capabilities can synchronize roles/modules and update all enabled System Users. It is whitelisted without an explicit entry role gate. Treat it as service-only or tightly privileged, serialized and audited.
- marketplace.publish_install_update accepts caller-supplied extension/status/error/site values and publishes a realtime event. Restrict to authenticated Master service identity and correlate with a valid operation.
- system.is_app_installed and system.has_active_system_manager reject Guest but accept any authenticated caller. Restrict to bootstrap service identity.
- subscription_guard.enforce_subscription is commented out as a before_request hook; it is not an active global request gate in this snapshot.
- DT Tenant User hooks actively assign/synchronize permissions and must be reviewed alongside capability synchronization.

## 3. Authorization risk register

| ID | Surface | Finding | Severity | Required control |
|---|---|---|---|---|
| AUTH-01 | Master marketplace APIs | FQDN-based lookup and child-row mutation without explicit caller/tenant authorization | High | Bind service credential to one Tenant Site; validate ownership and role |
| AUTH-02 | Master subscription APIs | Billing history and plan mutation lack tenant ownership checks | High | Derive subscription from authenticated tenant identity |
| AUTH-03 | Master billing APIs | Invoice/payment operations lack explicit invoice/customer ownership checks | Critical | Authorize invoice/customer/subscription before every action |
| AUTH-04 | Master invoice PDF | Administrator elevation without pre-validating caller invoice access | Critical | Authorize first; avoid broad elevation |
| AUTH-05 | Master provisioning API | Tenant bootstrap and privileged API user creation lack explicit entry authorization | High | Restrict to provisioning service/operator |
| AUTH-06 | Master capability/extension APIs | Tenant information retrieved by caller-supplied FQDN | High | Resolve tenant from service identity, not FQDN alone |
| AUTH-07 | Tenant settings.configure | Arbitrary settings attributes saved with ignore_permissions=True | Critical | Role/service check and allowlist; protect Master URL/credentials |
| AUTH-08 | Tenant capability resync | Authenticated caller can trigger role/module/user synchronization | Critical | Service-only, locked, audited, idempotent |
| AUTH-09 | Tenant marketplace actions | Authenticated caller can request install/upgrade/uninstall | High | Tenant Manager gate; Master independently authorizes |
| AUTH-10 | Tenant billing proxy | Authenticated caller can invoke billing/payment proxy | High | Tenant billing role gate; Master checks invoice ownership |
| AUTH-11 | Tenant publish_install_update | Caller-controlled realtime payload | High | Authenticate Master; validate operation; scope recipients |
| AUTH-12 | Tenant bootstrap helpers | Any authenticated identity can call bootstrap helpers | Medium/High | Dedicated bootstrap service identity |

Severity reflects potential impact from the code path, not evidence of an incident.

## 4. Background-job and scheduling update

| Component | Active behavior | Proposed disposition |
|---|---|---|
| DT Master marketplace | Long-queue job for app actions through wrapper | REFACTOR to unified operation/request service in M4.5 |
| DT Master other Node/Site/Mail APIs | Synchronous SSH or remote HTTP in request | REFACTOR to worker/Node-side execution with deadlines in M4.3/M4.6 |
| DT Master hooks.py | No active scheduler_events registration | RETAIN unless later operations require scheduled reconciliation |
| DT Tenant capability scheduler | Active cron every six hours; calls resync_capabilities | RETAIN/REFINE with service-only auth, lock, idempotency and audit |
| DT Tenant manual capability resync | Whitelisted endpoint also callable directly | RESTRICT to authorized operator/provisioning service |

## 5. Secret and service identity implications

1. DT Tenant stores Master API credentials in singleton Tenant Settings and uses them for marketplace, billing and capability calls. Confirm field types/encryption; never expose them through generic settings APIs or normal serialization.
2. These credentials must identify the tenant to Master independently of caller-supplied FQDN.
3. Master must not trust a request-body FQDN as proof that the authenticated service may operate on that site.
4. Bootstrap currently creates a remote Master API user with System Manager role. Replace with dedicated least-privilege service identity and API scope.
5. Provisioning generates and stores service API key/secret in ordinary Tenant Site Data fields on Master. This remains in the Runtime Secret migration.
6. DT Tenant publish-install-update returns status/error data to realtime. Scope this to the correct tenant audience and sanitize errors.
7. Protect Master URL and API credential configuration against arbitrary mutation; a compromised settings endpoint could redirect outbound calls or replace the trusted Master destination.

## 6. Required cross-repository API contract

Before M4.3/M4.5/M4.6 implementation, define:

- **Service identity:** each Tenant Site has a dedicated service identity at Master, mapped to exactly one Tenant Site and current Bench Environment.
- **Authentication:** rotatable credentials; no shared System Manager account; no caller-controlled tenant identity.
- **Authorization scopes:** marketplace read, app installation request, app upgrade/uninstall, subscription read, billing read, payment initiation, capability fetch/sync and bootstrap.
- **Request envelope:** request ID/idempotency key, authenticated actor/service identity, operation, target resource and schema version.
- **Tenant binding:** Master derives Tenant Site from authenticated service identity, then resolves Bench Environment and Node through authoritative hierarchy.
- **Audit:** actor/service identity, tenant/site, action, authorization result, request ID, timestamp and safe outcome.
- **Replay protection:** idempotency and replay-safe state transitions.
- **Realtime:** operation-ID correlation, authenticated source and recipient scoping.
- **Secrets:** protected references, rotation lifecycle, no secret values in payloads/logs.
- **Error contract:** machine-readable safe error code/summary; no raw traceback or credential disclosure.

## 7. Updated audit position and remaining M4.1 work

The cross-repository source review has been extended against dt_master:develop
and dt_tenant:develop. The full repository tree inventory and grouped proposed
dispositions are recorded in M4_FILE_DISPOSITION_MATRIX.md.

Completed at source-review level:
1. Principal DT Tenant-to-Master marketplace, billing, provisioning and
   capability caller paths.
2. Tenant-side API role gates, service credential consumers and callback
   behavior.
3. Workspace/page role exposure and User permission synchronization hooks.
4. Principal child DocType, sensitive-field and realtime findings.

Remaining M4.1 work:
1. Finalize the cross-repository service identity, authentication, tenant
   binding and authorization-scope contract.
2. Finalize operation/request, idempotency, replay protection and realtime
   recipient contracts.
3. Consolidate secret-provider, credential rotation and safe transport.
4. Verify external/private callers outside the two accessible develop trees.
5. Complete the NDF descriptor/deployment contracts and final M4.1 review.

Live runtime verification is deferred to the relevant implementation/testing
stage. No application code, DocTypes, migrations, scripts or runtime
configuration were changed.
