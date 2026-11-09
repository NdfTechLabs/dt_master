# M4.1 — UI, Workspace and Role-Definition Audit

**Repositories:** NdfTechLabs/dt_master (develop), NdfTechLabs/dt_tenant (develop)  
**Audit date:** 2026-11-09  
**Scope:** Desk forms, workspace/sidebar definitions, page access, DocType permissions, role models and proposed dispositions.  
**Status:** Read-only audit. No implementation changes authorized or performed.

## 1. Executive findings

1. DT Master currently presents Bench lifecycle as Infrastructure Node actions. This confirms that the current Desk information architecture reflects the old Node-centric ownership model.
2. DT Master Workspace and sidebar definitions are broadly visible and not role-scoped in their serialized definitions. Underlying DocType permissions still apply, but navigation visibility is not an authorization control.
3. DT Tenant has an explicit tenant-facing role model: Tenant Manager and Tenant User. The Add-ons page is available to both. Correction: Subscription is currently exposed through a workspace available to both; no page-level Tenant Manager-only gate was found.
4. Tenant Settings grants both Tenant Manager and System Manager read/write access while storing Master API key and API secret as ordinary Data fields. This is a material credential exposure risk.
5. Tenant role profiles and role assignments are capability-constrained mappings to existing Frappe Roles. They must remain separate from platform operator and tenant-service identities.
6. Several UI controls are hidden or shown based on state/role, but backend APIs remain the security boundary. The Tenant Installed App buttons directly call executor methods, bypassing the marketplace queue wrapper.

## 2. DT Master workspace and navigation

### Audited files

- dt_master/dt_master/workspace/master/master.json
- dt_master/workspace_sidebar/master.json
- dt_master/dt_master/sidebar/dt_master/dt_master.json
- dt_master/hooks.py

### Findings

- Master Workspace has public=1 and roles=[].
- Workspace Quick Links include Infrastructure Profile, Infrastructure Provider, Infrastructure Node, Tenant Site and Extension.
- Workspace Sidebar includes Nodes, Tenants, Extensions, Extension Versions, Subscriptions, Plans, Capability Profiles, Machine Constraints, Invoices and Customers.
- App Sidebar includes Infrastructure Provider, Infrastructure Node, Infrastructure Profile, Tenant Site, Capability Profile, Extension, Extension Version, Marketplace Category, Machine Constraint, Mail Server, Script Definition, Script Execution, Frameworks, Framework Version and System Dependency.
- Serialized Workspace/Sidebar entries have no per-link role restrictions. DocType permissions remain effective, but the navigation exposes management surfaces broadly.
- hooks.py publishes the Master app on the Apps screen; the has_permission callback is commented out.
- No app-owned Role fixtures or explicit custom Frappe Role definitions were found in the inspected DT Master tree. System Manager is the explicit role used by reviewed DocType permissions and some UI gates.
- Capability Role is a capability mapping DocType, not a Frappe Role definition.

### Proposed disposition

| File/component | Proposed disposition | Milestone |
|---|---|---|
| Master Workspace | **REFACTOR**: explicitly scope platform-operator dashboards and separate operational from tenant/business administration | M4.3/M4.6 |
| Workspace Sidebar | **REFACTOR**: group Node, Bench, Site, Marketplace, Billing and Mail surfaces; apply intended role visibility | M4.3/M4.6 |
| dt_master Sidebar | **REFACTOR**: remove duplicate/stale links and align navigation to target DocType ownership | M4.3/M4.6 |
| hooks.py app-screen permission | **REVIEW/EXTEND** if Master app visibility should be restricted | M4.6 |
| Role definitions/fixtures | **ADD** only after authorization matrix approval; define platform operator and scoped service roles | M4.1/M4.6 |

## 3. DT Master form actions and ownership impact

### Infrastructure Node

Audited file: dt_master/dt_master/doctype/infrastructure_node/infrastructure_node.js

- Node form exposes Install Dependencies, Install Bench, Initialize Project, Prepare Project, Start Project, Stop Project and Check System Status.
- Backup Bench, Update Bench, Migrate Bench, Remove Bench and Restore Backup are shown only when the browser user has System Manager.
- The Bench actions are dispatched to dt_master.api.infrastructure_node methods, confirming Bench lifecycle is currently attached to Node.
- Most ordinary action buttons are gated by status/state in JavaScript rather than an explicit client role check. Server-side authorization remains mandatory.
- Restore Backup asks for a Tenant Site filtered by the legacy Node link and a free-form absolute backup directory. The target should select a Bench Environment/site and a managed backup reference, not accept arbitrary filesystem paths.

**Disposition:** **REFACTOR** Infrastructure Node JS to Node-only actions. Move Bench actions to Bench Environment UI, use deployment/operation records, and replace free-form restore paths with backup references. **M4.3**.

### Tenant Site

Audited file: dt_master/dt_master/doctype/tenant_site/tenant_site.js

- Site form exposes Create, Install Apps, Verify, Configure Proxy, Suspend, Resume and Destroy based on status/automation_state, without explicit role checks in the client handlers.
- Tenant Installed App child-row controls expose install, upgrade, downgrade, reinstall, uninstall, remove and check actions.
- Child-row actions call tenant_installed_app_executor directly, bypassing the marketplace queue wrapper and creating the synchronous path identified in the API audit.
- Hiding buttons while last_action_status is Queued/Running is only a presentation guard; backend operation claims are required.
- The Site form has no Bench Environment selector because current ownership is directly through Node.

**Disposition:** **REFACTOR** Site UI to show mandatory Bench Environment and derived read-only Node; retain Site-only lifecycle actions; route all mutations through one authorized operation/request service. **M4.5/M4.6**.

### Mail Server

Audited file: dt_master/dt_master/doctype/mail_server/mail_server.js

- Mail stack and mailbox actions are state-gated in JavaScript.
- Mail Server remains a separate Node workload, not a Bench Environment child.

**Disposition:** **RETAIN/EXTEND** the separate mail UI; route mutations through the shared operation service and strengthen backend authorization. Outside Bench ownership; execution adapter alignment in M4.3.

### Framework Version

Audited file: dt_master/dt_master/doctype/framework_version/framework_version.js

- The form exposes Clone Dependencies and Apply through framework_version.apply_selected_dependencies.
- This is a runtime/dependency composition action. In the target, exact runtime and dependency resolution belongs to the NDF candidate/build workflow.

**Disposition:** **REFACTOR/REPLACE** dependency cloning as a production runtime mutation with candidate composition and compatibility preview; retain Framework Version as compatibility catalogue. **M4.5**.

### Extension and Extension Version

Audited files: extension.js and extension_version.js. Both are currently empty/commented.

**Disposition:** **EXTEND** with NDF descriptor synchronization/provenance and DT Master marketplace approval controls; technical release data should be descriptor-backed/read-only. **M4.5**.

## 4. DT Master DocType permission and action-field review

The inspected primary operational DocTypes (Infrastructure Node, Tenant Site, Extension, Extension Version, Framework Version, Framework Version Dependency, System Dependency, Script Definition, Script Execution, Mail Server, Infrastructure Profile, Infrastructure Provider, Capability Profile, Machine Constraint, Marketplace Category and Frameworks) grant full listed CRUD/read/export/print/report/share permissions to System Manager only.

Tenant Installed App is a child table with no independent permissions. Node Action Log, Execution Log, Mail Server Log, Master Mail Users, Infrastructure Region, Capability Module and Capability Role also have no independent permission rows in their DocType JSON; access therefore depends on parent/controller/framework behavior and needs runtime permission verification rather than assuming the child table is independently protected.

The Infrastructure Node, Tenant Site, Tenant Installed App and Mail Server DocTypes define Button fields for privileged lifecycle operations. Button fields are UI controls, not authorization boundaries. Backend whitelisted methods must enforce role and document/parent ownership.

Sensitive field check reconfirmed Tenant Site Data fields:
- tenant_api_key
- tenant_api_secret
- admin_password
- mysql_root_user_password

These fields are not protected by field permlevel in the inspected DocType JSON.

## 5. DT Tenant workspace, pages and role model

### Audited files

- dt_tenant/dt_tenant/workspace/tenant/tenant.json
- dt_tenant/workspace_sidebar/tenant.json
- dt_tenant/dt_tenant/page/add_ons/add_ons.json
- dt_tenant/dt_tenant/page/tenant_subscription/tenant_subscription.json
- dt_tenant/dt_tenant/doctype/tenant_settings/tenant_settings.json
- dt_tenant/dt_tenant/doctype/tenant_settings/tenant_settings.js
- dt_tenant/dt_tenant/doctype/tenant_roles/tenant_roles.json
- dt_tenant/dt_tenant/doctype/tenant_role_profile/tenant_role_profile.json
- dt_tenant/controllers/user_roles.py

### Findings

- Tenant Workspace is assigned to Tenant Manager and Tenant User.
- Add-ons Page is assigned to Tenant User and Tenant Manager.
- Correction from the full develop tree review: the Tenant Workspace is assigned to Tenant Manager and Tenant User, and its shortcuts/sidebar expose Subscription to both. The page definition and page JavaScript contain no Tenant Manager-only gate. The earlier statement that the Subscription Page is Manager-only was incorrect; current access must be treated as available to both until explicitly restricted.
- Tenant Settings DocType grants read/write to Tenant Manager and System Manager.
- Tenant Settings fields api_key and api_secret are Data, not Password fields. Tenant Manager has DocType read/write access to the singleton containing these service credentials. This is a high-priority exposure/control issue.
- Tenant Roles links to an existing Frappe Role. Tenant Role Profile combines role/module assignments.
- user_roles.py intersects profile/direct assignments with the allowed_roles and allowed_modules capability snapshot, then updates the user's system roles and blocked modules.
- Tenant Manager and Tenant User are tenant-facing identities. They must not be treated as platform operators or accepted by Master for Node/Bench operations.
- Tenant Settings JS displays Re-sync Capabilities only to System Manager, but the whitelisted endpoint must independently enforce that restriction.
- Tenant workspace links include Settings, Sync Logs, Add-ons, Subscription, Users, Roles, Profiles and Modules.

### Proposed disposition

| File/component | Proposed disposition | Milestone |
|---|---|---|
| Tenant Workspace | **RETAIN/REFACTOR**: keep tenant-facing shortcuts; separate sensitive service configuration from ordinary settings | M4.6 |
| Tenant Workspace Sidebar | **RETAIN/REFACTOR**: currently exposes Subscription to both workspace roles; align links with Tenant Manager/Tenant User scopes | M4.6 |
| Add-ons Page definition/UI | **RETAIN/EXTEND**: retain permitted tenant access; enforce role and site ownership server-side | M4.5 |
| Tenant Subscription Page | **REFACTOR/RESTRICT**: currently exposed through a workspace available to both tenant roles and has no page-level role gate; restrict to Tenant Manager and enforce the same policy in proxy APIs and Master | M4.6 |
| Tenant Settings DocType | **REFACTOR**: protect Master URL/API credentials, split service configuration from ordinary tenant settings, restrict mutation | M4.6 |
| Tenant Settings JS | **REFACTOR**: retain privileged sync affordance but rely on authorized server operation | M4.6 |
| Tenant Roles/Tenant Role Profile | **RETAIN/EXTEND**: preserve capability-constrained tenant role mapping; reject privileged platform roles | M4.6 |
| controllers/user_roles.py | **RETAIN/REFACTOR**: preserve tenant role/module enforcement; test forbidden roles and capability changes | M4.6 |
| DT Tenant hooks.py | **RETAIN/EXTEND**: preserve six-hour sync; secure service identity, lock, idempotency and audit | M4.6 |

## 6. Target actor boundaries

| Actor class | Responsibility | Must not imply |
|---|---|---|
| Platform Operator | Nodes, Bench Environments, deployments, infrastructure and platform recovery | Tenant end-user authority by default |
| Platform Marketplace Operator | Extension/listing approval and product inclusion governance | Node/Bench authority unless separately granted |
| Tenant Service Identity | DT Tenant-to-Master authentication for one bound Tenant Site and approved API scopes | System Manager or platform operator privileges |
| Tenant Manager | Tenant users, role profiles, allowed modules, marketplace requests and subscription workflows | Infrastructure Node/Bench management |
| Tenant User | Tenant-authorized marketplace and application functions | Tenant administration, billing or infrastructure actions |
| Read-only/Support | Specifically authorized operational or tenant information | Write, execute, export sensitive data or retrieve secrets by default |

Do not introduce role names until the authorization matrix and bootstrap/migration strategy are approved. Reduce System Manager use to platform administration/break-glass, not tenant service authentication.

## 7. Updated audit status

The consolidated disposition matrix has been extended against the full develop
tree snapshots of both repositories. It records grouped path dispositions and
additional source-level findings. External callers outside the accessible
repository trees remain unverified.
Runtime-only verification is tracked separately and is not treated as a
prerequisite for the source-level disposition matrix.

| Area | Status |
|---|---|
| DT Master workspace/sidebar and form actions | Reviewed |
| DT Tenant workspace/pages and role mapping | Reviewed |
| Primary operational DocType permissions/action fields | Reviewed from DocType JSON |
| Tenant Settings credential field types and roles | Confirmed: Data fields; Tenant Manager read/write |
| Full Git tree inventory and grouped disposition matrix | Completed for both develop trees; external/private callers remain unverified |
| Realtime worker identity/event subscription authorization | Source-level findings recorded; final service/event authorization contract remains to be consolidated. Runtime behavior verification is deferred to the relevant implementation/testing stage. |
| Sensitive-field consumer tracing | Completed for principal inspected consumers; live export/report/print/backup behavior verification is deferred to implementation/testing. |
| Child-DocType schema and parent-authorization source review | Completed for inspected child DocTypes; live Frappe behavior verification is deferred to implementation/testing. |
| Final cross-repository role and service identity contract | Outstanding — M4.1 architecture work |

No application code, DocTypes, migrations, scripts or runtime configuration were changed.


### 8. Correction recorded during full develop audit

The previous audit statement that the Tenant Subscription Page was assigned only to Tenant Manager was incorrect. The serialized Tenant Workspace is assigned to Tenant Manager and Tenant User, and the Subscription shortcut is present in both the workspace and sidebar. The page definition and page JavaScript do not implement a Tenant Manager-only access check. Until the route and API are restricted, treat Subscription as exposed to both tenant roles. The corrected disposition is recorded above and in M4_FILE_DISPOSITION_MATRIX.md.
