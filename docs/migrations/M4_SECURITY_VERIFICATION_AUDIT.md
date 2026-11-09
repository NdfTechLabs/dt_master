# M4 Security Verification Audit — Realtime, Secrets and Child DocTypes

**Repository:** NdfTechLabs/dt_master  
**Branch:** develop  
**Related repository:** NdfTechLabs/dt_tenant (version-1)  
**Audit mode:** Read-only source review; no application code or runtime configuration changed  
**Milestone:** M4.1 — Integration Contract and Architecture  
**Status:** Source review completed for the inspected paths; live child-DocType permission verification remains pending.

## 1. Scope and evidence

Reviewed current source in both repositories:

- `dt_master/api/script_contract.py`
- `dt_master/api/tenant_installed_app_executor.py`
- `dt_master/api/tenant_site_executor.py`
- `dt_master/api/mail_server_executor.py`
- `dt_master/api/marketplace.py`
- `dt_master/controllers/provisioning.py`
- `dt_master/scripts/lib/backup.sh`
- `dt_master/dt_master/doctype/tenant_site/tenant_site.json`
- `dt_master/dt_master/doctype/tenant_site/tenant_site.js`
- `dt_master/dt_master/doctype/tenant_installed_app/tenant_installed_app.json`
- `dt_master/dt_master/doctype/node_action_log/node_action_log.json`
- `dt_tenant/api/marketplace.py`
- `dt_tenant/api/settings.py`
- `dt_tenant/api/extensions.py`
- `dt_tenant/controllers/capability_sync.py`
- `dt_tenant/controllers/capability_scheduler.py`
- `dt_tenant/doctype/tenant_settings/tenant_settings.json`
- `dt_tenant/doctype/tenant_settings/tenant_settings.js`
- `dt_tenant/page/add_ons/add_ons.js`
- `dt_tenant/hooks.py`

The findings below distinguish source-level facts from runtime behavior that still requires a live Frappe site test.

## 2. Realtime execution identity and event authorization

### 2.1 DT Master remote execution stream

`run_remote_script()` publishes each output line and the terminal status using:

- Event name supplied by the executor (`tenant_app_log`, `tenant_script_log`, or `mail_executor_log`).
- Payload context supplied by the caller.
- `user=frappe.session.user`.

The executor does not pass an explicit initiating-actor ID or durable operation ID into this function. Marketplace app actions are enqueued through `api.marketplace._trigger_tenant_executor()`; the queue call does not explicitly pass a persisted actor identity. The worker's effective `frappe.session.user` therefore needs a runtime test before it can be treated as the original requester.

The Desk form listeners subscribe to shared event names and filter payloads in browser JavaScript by tenant/row. Site logs filter by tenant; app logs filter by tenant and child-row name. These filters are useful for UI display but are not authorization controls. The handlers also call `frappe.realtime.off(event)`, which removes listeners for that event globally in the current client context and can interfere with another active dialog/listener.

**Finding RT-01 — High:** Event routing is tied to the worker's current session user, but the actor-to-job-to-event identity chain is not explicitly persisted or verified. A wrong or missing worker identity can misroute or suppress logs; a shared execution identity can broaden visibility to all sessions subscribed as that user.

**Finding RT-02 — Medium:** Event correlation relies on tenant/row context and browser-side filtering rather than a durable operation ID and explicit server-side authorization for the intended recipient.

**Proposed disposition: REFACTOR.**
- Capture initiating actor, tenant/site, target record and operation ID when the request is accepted.
- Persist them with the durable operation/request record and explicitly propagate them into the worker.
- Publish to an authorized user or narrowly scoped authorized room; never rely on client-side payload filtering as access control.
- Include operation ID and target identity in every event.
- Use listener-specific callbacks and unsubscribe only the current listener.
- Test direct synchronous execution, queued execution, scheduler execution, reconnect and multiple concurrent dialogs.

### 2.2 DT Master → DT Tenant marketplace status event

The Master app executor calls the tenant endpoint `dt_tenant.api.marketplace.publish_install_update` with extension, status, action status, error and site values.

The tenant endpoint is `@frappe.whitelist()`, accepts those values from request arguments, prints the supplied values (including error text), and publishes `extension_install_update` with `after_commit=True` but without a `user` target. This is a site-wide event, not a user-targeted event.

The Add Ons page subscribes to this event and updates the card based on extension and status. It does not verify `data.site` or a durable operation ID. The endpoint has no explicit service-identity check, actor authorization, signature, replay protection or schema validation in the inspected implementation.

**Finding RT-03 — High:** Any authenticated caller who can invoke the whitelisted endpoint can attempt to inject marketplace status/error events into all connected sessions on that tenant site. The event's error field may expose internal diagnostics. The Master-to-Tenant API token is used by the legitimate caller, but the receiver does not establish that the request came from that service identity.

**Proposed disposition: REPLACE/RESTRICT.**
- Convert this into a service-only callback with narrowly scoped authentication and tenant binding.
- Validate the expected Master service identity, extension, site and operation against an outstanding operation/request.
- Make callbacks idempotent and replay-safe.
- Publish a sanitized status to the intended authorized user/role or a narrowly scoped channel.
- Do not log or broadcast raw exception text; expose a safe error code and diagnostic reference.

## 3. Sensitive-field consumer and persistence tracing

### 3.1 Tenant Site credentials

The Tenant Site DocType defines these as ordinary `Data` fields:

- `tenant_api_key`
- `tenant_api_secret`
- `admin_password`
- `mysql_root_user_name`
- `mysql_root_user_password`

Tenant Site's DocType permissions grant System Manager read, write, export, print, report and share. The sensitive fields have no field-level permission level in the inspected schema.

Consumers verified:

- `script_contract.tenant_environment()` reads Administrator and database credentials and places them into the remote execution environment.
- `build_remote_command()` serializes every environment value into shell `export` statements embedded in the SSH remote command string.
- `script_contract.mail_environment()` reads MySQL root credentials from a linked Tenant Site for mail-server operations.
- `tenant_installed_app_executor._tenant_api_call()` and `controllers/provisioning._tenant_api_call()` use Tenant Site API key/secret in HTTP Authorization headers.
- Site and app executors persist command output/error information into Tenant Site child logs or app-row diagnostic fields.

The script runner redacts values for environment keys containing PASSWORD, SECRET or TOKEN from streamed and returned stdout/stderr. This is useful but does not eliminate exposure in the remote command transport, process/shell inspection, exception messages, transformed/partial output, or other logging paths. The redaction list is inferred from environment variable names, not from a central secret registry.

**Finding SEC-01 — Critical:** Tenant Site credentials are stored as ordinary Data fields and are available to System Manager export/print/report paths. They are also copied into remote command strings.

**Proposed disposition: REPLACE.**
- Migrate credential values to protected Runtime Secret references/provider-backed storage.
- Separate tenant API identity, Frappe bootstrap/admin credentials and database administration credentials.
- Resolve secrets only inside the authorized execution boundary.
- Replace shell-command interpolation with a protected delivery mechanism (for example, a restricted stdin/file-descriptor or short-lived secret handoff supported by the Node execution adapter).
- Remove plaintext fields after migration, consumer conversion and rotation.
- Sanitize all diagnostic and export surfaces.

### 3.2 Tenant Settings Master API credentials

Tenant Settings defines `api_key` and `api_secret` as ordinary Data fields. Its permissions grant Tenant Manager read/write access as well as System Manager read/write access.

The credentials are consumed by:
- DT Tenant marketplace API calls.
- Marketplace listing calls.
- Capability synchronization.
- The scheduled capability sync.

`dt_tenant.api.settings.configure()` accepts arbitrary keyword arguments, assigns any key matching an attribute on Tenant Settings, and saves with `ignore_permissions=True`. It has no explicit caller role/service gate or field allowlist in the inspected code. The Desk button visibility restriction for capability resync is only a UI condition; the underlying method is whitelisted.

**Finding SEC-02 — Critical:** The Master service credentials are stored in ordinary Data fields accessible to Tenant Manager and the settings configuration endpoint accepts arbitrary matching fields.

**Proposed disposition: REFACTOR.**
- Move API secret to protected secret storage; expose only a masked/set indicator.
- Restrict credential read/rotation to a bootstrap or platform service identity.
- Restrict settings configuration to an explicit field allowlist and authorized provisioning/System Manager path.
- Prevent Tenant Manager from reading or replacing service credentials.
- Add negative tests for ordinary authenticated users and Tenant Manager.

### 3.3 Logs, errors and backups

- Tenant Site's `automation_logs` uses the `Node Action Log` child DocType, which stores stdout and stderr as Long Text.
- Mail Server automation logs also store stdout/stderr; exception handling appends Python traceback text and writes an Error Log.
- The app executor stores tracebacks in `Tenant Installed App.last_error` and Error Log.
- Marketplace event publishing prints caller-supplied error text and broadcasts it.
- Backup script uses Frappe's `bench backup --with-files`, copies database, public files, private files and `site_config.json` into a per-site backup directory, and applies `chmod -R go-rwx`. This is a useful local permission baseline, but encryption at rest, off-host access controls and retention/restore access governance are not established by this script alone.

**Finding SEC-03 — High:** Output and exception fields are durable diagnostic stores and must be treated as potentially sensitive. Backup contents include site configuration and full database/files, which may contain credentials and tenant data.

**Proposed disposition: REFACTOR/RETAIN.**
- Retain structured operational logs but store safe summaries, codes and diagnostic references rather than raw secrets or unrestricted tracebacks.
- Apply centralized redaction before persistence, realtime publication and error reporting.
- Restrict log read/export permissions by parent ownership and operator role.
- Treat backups as secrets: encrypt at rest and in transit, tightly restrict access, audit retrieval, define retention and verify restore controls.
- Audit backup destination ownership and off-host replication separately.

## 4. Child-DocType permission behavior

### 4.1 Schema facts verified

- `Tenant Installed App` has `istable: 1` and an empty `permissions` array.
- `Node Action Log` has `istable: 1` and an empty `permissions` array.
- `Tenant Site` grants full document permissions only to System Manager in the inspected DocType JSON.
- Tenant Site contains the `installed_apps` and `automation_logs` child tables.

This confirms that the two records are configured as Frappe child DocTypes, not independent role-governed documents. Their access is expected to be governed through the owning parent document and the parent-document operation path. The source schema alone does not prove the behavior of every REST/resource/list/export path on the deployed Frappe version.

The app executor's `_load_action_context()` loads the Tenant Site, checks its write permission, resolves the requested child row from that parent's `installed_apps`, then loads and checks the linked Node. This is the correct parent-first pattern for that path.

Other paths mutate child rows using `ignore_permissions=True`; those paths still need an explicit caller/tenant authorization check before the bypass. The presence of no child permissions is not itself a vulnerability if the parent boundary is consistently enforced; it becomes one when APIs bypass the parent boundary or accept an untrusted parent/row pair.

**Finding CHILD-01 — Source review confirmed; live behavior pending:** Child DocTypes have no independent permissions. Parent document authorization is the intended security boundary, but live Frappe behavior has not yet been exercised against the deployed site/version.

**Proposed disposition: RETAIN WITH EXPLICIT PARENT AUTHORIZATION.**
- Keep child DocTypes as child tables; do not create independent roles for these rows.
- Every API must load the parent, validate parent read/write permission and tenant ownership, then resolve the child row by name from that parent.
- Never trust a child-row name supplied by the caller without verifying that it belongs to the authorized parent.
- Any use of `ignore_permissions=True` must occur only after an explicit authorization decision and be covered by a negative test.
- Verify direct REST/resource access and export behavior on the actual deployed Frappe site before finalizing the enforcement contract.

### 4.2 Required live verification

Run on a non-production test site using representative accounts:
1. System Manager.
2. Ordinary authenticated user without System Manager.
3. Tenant Manager on the DT Tenant site.
4. Guest.

For Tenant Site and child rows, test:
- Parent read/write checks.
- Loading child rows through an authorized parent.
- Direct child DocType resource/API access by child-row name.
- List/query/export attempts against child DocTypes.
- Updating/deleting a child row through the parent.
- Attempting to pair a valid child-row name with a different parent.
- Calling each whitelisted mutation endpoint with unauthorized actor, parent and child-row combinations.

Record the deployed Frappe version, effective user, endpoint, HTTP/result status and whether data was read or changed. Use disposable test records; do not test with production secrets or destructive actions.

## 5. Consolidated proposed disposition

| Component | Proposed disposition | Target |
|---|---|---|
| `run_remote_script` realtime identity/context | REFACTOR | M4.1 contract; implement in M4.3/M4.5 |
| Master app/site log subscribers | REFACTOR | M4.1 contract; implement with operation service |
| Master → Tenant install callback | REPLACE/RESTRICT | M4.1 service identity contract |
| Tenant Site credential fields | REPLACE | M4.1 secret contract; implementation with migration |
| Tenant Settings API credentials | REFACTOR | M4.1 secret/bootstrap contract |
| Remote command secret delivery | REPLACE | M4.3 Node execution adapter |
| Automation/error logs | REFACTOR/RETAIN | M4.3/M4.7 operational controls |
| Backup artifacts and access | RETAIN/EXTEND | M4.3/M4.7 storage and recovery controls |
| Tenant Installed App / Node Action Log | RETAIN as child DocTypes | M4.1 parent authorization contract |
| Child-row API access and negative tests | VERIFY | M4.1 runtime verification |

## 6. Remaining verification gates

The following are not claimed as complete:

1. **Worker identity runtime test:** prove whether Frappe queue workers preserve the initiating user for the marketplace job path; test scheduler/system jobs separately.
2. **Realtime delivery test:** verify recipient isolation, multi-session behavior, reconnect behavior and whether tenant-wide status events reach users beyond the intended operator.
3. **Child DocType runtime test:** execute the matrix in §4.2 on the actual deployed Frappe version/site.
4. **Sensitive-field surface test:** verify actual export, report, print, REST serialization and backups on the deployed site; inspect any custom reports not represented in the reviewed source paths.
5. **Secret lifecycle:** confirm encryption, key custody, rotation, backup encryption and access audit in the chosen secret provider.

M4.1 remains in progress. This audit does not authorize application-code changes or start M4.2/M4.3 implementation.
