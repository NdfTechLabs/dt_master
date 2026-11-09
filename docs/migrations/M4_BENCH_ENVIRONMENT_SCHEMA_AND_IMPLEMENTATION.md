# M4.1 — Bench Environment Schema and Implementation Specification

**Status:** Architecture and schema approved; implementation not started  
**Repository:** `NdfTechLabs/dt_master`  
**Branch baseline:** `develop`  
**Scope:** DT Master DocTypes, ownership rules, validation, deployment records and implementation/migration sequence  
**Related contract:** NDF Product Deployment Descriptor (defined by NDF Frappe Docker)

> This document records the agreed target design. It does not claim that the proposed DocTypes, fields, APIs or validators have already been implemented. Existing implementation facts are identified separately from target requirements.

---

## 1. Architecture decisions

The following principles are approved and binding for M4.1:

1. **Hierarchy:** Infrastructure Node (1) → Bench Environment (N) → Tenant Site (N). A Tenant Site has exactly one Bench Environment and resolves its Node through that relationship.
2. **Application inventory:** Bench Environment Application contains the complete runtime application inventory, including apps baked into the product image and marketplace/custom apps. Tenant Installed App remains site-specific.
3. **Storage ownership:** Each Bench Environment owns its storage configuration and lifecycle references for site files, database data, queue persistence where required, logs, configuration and backups. Storage may be local, remote or managed.
4. **Service definitions:** NDF owns service definitions and the Compose-compatible runtime topology. DT Master consumes them and may change only explicitly overridable deployment parameters.
5. **Deployment history:** Every Bench Deployment retains the exact descriptor, image digest, Compose definition reference and deployment inputs it used.
6. **Single active composition:** A Bench Environment has one active runtime composition. A replacement may coexist temporarily during preparation, migration and rollback, but it is not a second active composition.
7. **Runtime compatibility:** Host and Docker environments coexist during migration. Runtime-specific operations are implemented behind adapters.
8. **Immutable runtime:** Image tags are informational. Docker deployments use immutable OCI image digests. Shared Bench application/runtime changes use a candidate and replacement deployment, not in-place mutation of the active runtime.
9. **Secret handling:** Passwords, API secrets and credentials are never placed in descriptors, Compose files, ordinary DocType fields or logs. DT Master stores protected secret references and resolves values at execution time.
10. **Upstream alignment:** Docker service topology, frontend routing and Frappe service behavior follow the upstream `frappe_docker` production model. The disposable `pwd.yml` topology is not the production contract.

## 2. Relationship model

```text
Infrastructure Node (1)
  └── Bench Environment (N)
       ├── Bench Environment Application (child)
       ├── Bench Runtime Component (child)
       ├── Bench Storage Reference (child)
       ├── Bench Service Definition (child)
       └── Tenant Site (N)
            └── Tenant Installed App (child)

Deployment Candidate
  └── Deployment Candidate Application (child)

Deployment Candidate
  └── Deployment Build (build tracking)

Bench Environment
  └── Bench Deployment (deployment history)
```

The four Bench Environment child tables are owned by the parent and are not independently created through normal user-facing APIs. Tenant Installed App remains a child of Tenant Site.

A Tenant Site must not retain an independently editable Node relationship alongside Bench Environment. This would create two sources of truth. The Node is resolved through the Bench Environment.

## 3. Existing schema audit

The current `develop` branch was inspected before writing this target specification.

| Existing DocType | Current fields/behavior relevant to M4 | Target treatment |
|---|---|---|
| Infrastructure Node | Node identity/provider/region/type, capacity, SSH access, OS, scripts path, project base/log paths, project service name, WSGI port, Frappe Version, Node state and action buttons including install Bench/start/stop. | Retain infrastructure identity, provider, OS, SSH, Docker Engine, capacity and Node health. Move Bench-specific paths, ports, runtime version and Bench lifecycle actions to Bench Environment. |
| Tenant Site | Direct `node` Link; `deployment_type` values Shared/Dedicated; region; ERPNext version; database name; site lifecycle buttons and credentials. | Add mandatory `bench_environment`. Migrate Node through the existing relationship. Do not reinterpret Shared/Dedicated as Host/Docker. Runtime type comes from Bench Environment. Derive Node and runtime version through the parent. |
| Tenant Installed App | Site child table with app name/type, Extension and Extension Version, status, enabled flag, installed/target versions, Git metadata, action state and buttons. | Retain as site-level installation state. Enforce that the app is available in the parent Bench Environment before installation. |
| Framework Version | Framework, version, Python version, Node version and Bench package. | Retain as compatibility baseline and extend only with compatibility metadata that is not duplicated by runtime observations. |
| Framework Version Dependency | Framework Version → System Dependency, enabled flag and install order. | Split infrastructure prerequisites from image/runtime dependencies. NDF resolves image dependencies; DT Master validates Node capabilities. |
| Node Action Log | Child table containing action, actor, timestamps, exit code, stdout/stderr, state before/after and result. Reused by Node and Tenant Site. | Preserve existing records and realtime behavior during transition. Bench Deployment uses its dedicated Bench Deployment Stage child table; do not force deployment lifecycle into this generic log. |
| Script Execution | Generic script, Node, Dynamic Link target, status, timing and Execution Log child table. | Retain for generic script execution. Bench Deployment remains authoritative for deployment candidate, descriptor, digest, affected sites, migration and rollback history. |

### Existing security migration

Tenant Site currently contains ordinary fields for tenant API key/secret, administrator password and MySQL root credentials. These are not suitable as the target secret model. M4 migration must:

- introduce protected secret references managed by the approved secret provider;
- migrate active credentials without exposing values in logs, exports or deployment descriptors;
- verify that new operations resolve secrets through the secret service;
- remove or retire plaintext fields only after migration and compatibility checks;
- rotate credentials where safe migration cannot prove that existing values were protected.

Do not copy existing plaintext values into new ordinary Data fields.

## 4. Bench Environment parent DocType

**DocType:** `Bench Environment`  
**Module:** Infrastructure (existing DT Master module)  
**Type:** Standard parent DocType; not a child table  
**Naming:** stable generated document name, with a separate immutable `environment_code`. A naming series may be used, but the exact Frappe autoname implementation must be confirmed against the app's supported Frappe version before coding.

### 4.1 Identity and ownership

| Fieldname | Frappe field type | Required | Rules |
|---|---|---:|---|
| `environment_code` | Data | Yes | Unique, generated, immutable |
| `infrastructure_node` | Link → Infrastructure Node | Yes | Immutable after provisioning; moving Nodes is a migration |
| `environment_type` | Select | Yes | Production, Staging, Development, Testing |
| `product` | Data | Yes | Stable NDF product identifier |
| `product_release` | Data | Yes | Product release/channel identifier |
| `framework_version` | Link → Framework Version | Yes | Must be compatible with product descriptor |
| `runtime_type` | Select | Yes | Host, Docker |
| `runtime_adapter` | Data | Yes | Versioned adapter ID, e.g. `host-v1`, `docker-compose-v1` |
| `status` | Select | Yes | Draft, Provisioning, Active, Updating, Migrating, Degraded, Stopped, Failed, Decommissioning, Retired |
| `health_status` | Select | Yes | Unknown, Healthy, Degraded, Unhealthy |
| `is_active` | Check | Yes | Derived from lifecycle state; not independently editable |
| `description` | Small Text | No | Operational notes |

### 4.2 Runtime and deployment identity

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `runtime_profile` | Data | Yes | Supported deployment profile identifier |
| `descriptor_schema_version` | Data | Docker | Supported descriptor schema version |
| `deployment_descriptor_ref` | Data | Docker | Immutable artifact reference for active deployment |
| `deployment_descriptor_hash` | Data | Docker | SHA-256 digest |
| `product_manifest_hash` | Data | Docker | SHA-256 digest |
| `composition_hash` | Data | Docker | SHA-256 of resolved composition |
| `image_repository` | Data | Docker | OCI image repository |
| `image_digest` | Data | Docker | Immutable `sha256:` digest; never deploy by tag alone |
| `image_tag` | Data | No | Informational display value only |
| `image_platform` | Data | Docker | e.g. `linux/amd64` |
| `compose_project_name` | Data | Docker | Stable per Bench Environment; unique on Node |
| `compose_definition_ref` | Data | Docker | Immutable NDF Compose definition reference |
| `deployment_id` | Data | No | Current successful deployment identifier |
| `deployment_candidate` | Link → Deployment Candidate | No | Candidate for active composition |
| `last_deployment` | Link → Bench Deployment | No | Latest deployment attempt or successful deployment according to defined semantics; prefer separate last-successful pointer if needed |

Active deployment fields are convenience pointers. They must be updated only after successful verification and activation. Historical truth belongs to Bench Deployment. A failed candidate must not overwrite the previous successful image digest or descriptor.

### 4.3 Bench configuration

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `bench_name` | Data | Yes | Unique within Node |
| `bench_user` | Data | Yes | Runtime OS/container user identity |
| `bench_root_path` | Data | Host | Host Bench root; Docker path is descriptor-defined |
| `sites_path` | Data | Runtime-derived | Resolved from storage/service mapping |
| `logs_path` | Data | Runtime-derived | Bench logs, separate from Node logs |
| `config_path` | Data | Runtime-derived | Bench configuration path |
| `timezone` | Data | Yes | Valid IANA timezone |
| `site_name_header_mode` | Select | Yes | Host Header, Fixed Site, Descriptor Defined |
| `default_site` | Link → Tenant Site | No | Must belong to this Bench Environment |
| `maintenance_mode` | Check | Yes | Controlled by lifecycle service |

Container paths are not host paths. For Docker, mount mappings belong in Bench Storage Reference and the deployment descriptor, not in duplicated editable path fields.

### 4.4 Database and Redis profiles

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `database_profile` | Select | Yes | Dedicated, Shared, External |
| `database_endpoint_ref` | Data | Conditional | Non-secret service/endpoint reference |
| `database_name_strategy` | Select | Yes | Per Site, Profile Defined |
| `database_secret_ref` | Data | Conditional | Protected secret-manager reference |
| `redis_profile` | Select | Yes | Dedicated, Shared, External |
| `redis_cache_endpoint_ref` | Data | Conditional | Cache endpoint reference |
| `redis_queue_endpoint_ref` | Data | Conditional | Queue endpoint reference |
| `redis_secret_ref` | Data | No | Protected secret-manager reference |

Credentials are never stored in these fields. Cache and queue endpoints remain separate logical references even if a provider hosts them on the same Redis server. Shared profiles require explicit isolation for each Bench Environment.

The upstream production Compose model permits external database and Redis endpoints. DT Master must therefore support dedicated or shared/external profiles without assuming that MariaDB and Redis containers always belong to the Frappe Compose project. The disposable upstream `pwd.yml` is not the production model.

### 4.5 Capacity and observability

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `cpu_limit` | Data | No | Adapter-compatible CPU quantity |
| `memory_limit_mb` | Int | No | Positive allocation |
| `storage_usage_bytes` | Int | No | Observed, read-only |
| `last_health_check` | Datetime | No | Monitoring-owned |
| `health_details` | Small Text | No | Monitoring-owned summary |
| `last_backup_on` | Datetime | No | Latest successful backup |
| `backup_policy` | Data | Yes | Policy identifier/reference |
| `deployment_lock` | Check | Yes | Orchestration-owned |
| `current_operation` | Link → Bench Deployment | No | Active deployment operation |
| `provisioned_on` | Datetime | No | First successful provisioning |
| `retired_on` | Datetime | No | Retirement timestamp |

Node capacity is measured at Infrastructure Node level. Bench limits are requested allocations and must be validated against available Node capacity before provisioning.

## 5. Bench Environment child tables

All four child DocTypes have `istable = 1`, are attached to the Bench Environment parent, and are written by controlled server-side services.

### 5.1 Bench Environment Application

Complete application inventory available in the shared Bench runtime, including image-baked, marketplace and custom apps.

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `app_name` | Data | Yes | Technical app name; unique per environment |
| `app_title` | Data | No | Display title |
| `app_version` | Data | Yes | Resolved version |
| `app_source_type` | Select | Yes | Product Image, Marketplace, Custom |
| `app_source` | Data | Yes | Repository/source identifier |
| `git_commit` | Data | Conditional | Immutable commit for Git source |
| `is_framework_app` | Check | Yes | Framework-level application |
| `is_required` | Check | Yes | Required by product composition |
| `installation_order` | Int | Yes | Dependency-resolved order |
| `dependency_apps` | Code | No | Validated JSON array of app names |
| `compatibility_status` | Select | Yes | Compatible, Incompatible, Unverified |
| `availability_status` | Select | Yes | Pending, Available, Failed, Removed |
| `source_descriptor_ref` | Data | No | App descriptor/source record reference |

Rules:
- No duplicate app names in one environment.
- Every declared dependency must exist in the inventory.
- Installation order must respect the resolved dependency graph.
- Available means code is present in the deployed runtime and verification passed.
- An app cannot be removed while any site in the Bench has it installed.
- Site installation is rejected unless the app is Available.
- Do not store a site-level installed flag here; that belongs to Tenant Installed App.

### 5.2 Bench Runtime Component

Exact runtime components resolved and observed after deployment.

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `component_name` | Data | Yes | Unique component identifier per environment |
| `component_category` | Select | Yes | Framework, Language Runtime, Package Manager, System Library, Database Client, Other |
| `version` | Data | Yes | Exact deployed version |
| `version_constraint` | Data | No | Original compatibility range |
| `source` | Data | Yes | Image, OS Package, Python Package, Node Package, External |
| `is_required` | Check | Yes | Required component |
| `is_verified` | Check | Yes | Runtime verification result |
| `verification_details` | Small Text | No | Verification summary |
| `verified_on` | Datetime | No | Verification timestamp |

A descriptor declaration alone does not prove a component is installed. Failed verification blocks activation. External database/Redis services are represented as service/profile dependencies, not as installed Bench Runtime Components.

### 5.3 Bench Storage Reference

Logical storage resources owned or allocated to this Bench Environment.

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `storage_key` | Data | Yes | Stable unique key per environment |
| `storage_name` | Data | Yes | Human-readable name |
| `storage_role` | Select | Yes | Sites, Database Data, Redis Queue Data, Logs, Backups, Configuration, Assets, Other |
| `storage_backend` | Select | Yes | Docker Named Volume, Host Bind Mount, Object Storage, Managed Database Storage, External Volume |
| `storage_scope` | Select | Yes | Local, Remote, Managed |
| `host_path` | Data | Conditional | Required for Host Bind Mount |
| `container_path` | Data | Conditional | Required for container mount |
| `volume_name` | Data | Conditional | Required for Docker Named Volume |
| `external_resource_ref` | Data | Conditional | Provider/resource reference |
| `service_key` | Data | Conditional | Consuming service |
| `access_mode` | Select | Yes | Read/Write, Read Only |
| `is_persistent` | Check | Yes | Persistence requirement |
| `is_encrypted` | Check | Yes | Encryption requirement |
| `backup_enabled` | Check | Yes | Backup inclusion |
| `backup_policy` | Data | Conditional | Policy reference |
| `retention_days` | Int | No | Positive retention period |
| `storage_status` | Select | Yes | Pending, Provisioning, Ready, Failed, Retiring, Retired |

Rules:
- A storage reference identifies one resource or one explicit provisioning specification.
- Docker Named Volume requires `volume_name`; Host Bind Mount requires `host_path`; external/object/managed storage requires `external_resource_ref`.
- Container-mounted storage requires `container_path`.
- `service_key`, when supplied, must resolve to a service definition.
- Production persistent data is never deleted as a side effect of container replacement or Bench retirement.
- Storage cannot be retired while active services or sites depend on it.
- Backups must be recoverable independently of active runtime storage.
- Sites, database data, backups and logs have separate identities and lifecycle policies.
- Shared managed database infrastructure may be used, but each environment's allocated database/storage and isolation boundary must be explicit.

Redis cache data is not automatically persistent. Redis queue persistence is determined by the selected service profile and recovery policy.

### 5.4 Bench Service Definition

Descriptor-derived logical inventory of services and external dependencies. It is not an editable Compose generator.

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `service_key` | Data | Yes | Stable unique service identifier |
| `service_name` | Data | Yes | Display name |
| `service_category` | Select | Yes | Frappe Application, Database, Redis, Proxy, Supporting |
| `service_type` | Select | Yes | Configurator, Backend, Frontend, Websocket, Worker, Scheduler, Database, Redis Cache, Redis Queue, Other |
| `service_origin` | Select | Yes | NDF Compose, External Profile |
| `enabled` | Check | Yes | Descriptor-derived |
| `image_repository` | Data | Conditional | OCI repository |
| `image_digest` | Data | Conditional | Immutable digest |
| `command` | Code | No | Descriptor-derived structured command |
| `replica_count` | Int | Yes | Positive integer |
| `network_name` | Data | No | Internal network reference |
| `depends_on` | Code | No | Validated JSON dependencies/readiness |
| `healthcheck_definition` | Code | No | Descriptor-derived health check |
| `resource_limits` | Code | No | Validated JSON resource limits |
| `storage_keys` | Code | No | JSON array of storage keys |
| `exposed_ports` | Code | No | JSON port definitions |
| `service_status` | Select | Yes | Pending, Starting, Healthy, Degraded, Stopped, Failed |
| `last_health_check` | Datetime | No | Observed health check |

Rules:
- NDF owns service definitions. DT Master may change only descriptor-declared overrides.
- External Profile services do not require a container image.
- Dependencies and storage keys must resolve.
- Service status is observed, not user-editable.
- Configurator is a one-shot service with a successful-completion condition, not a permanently running service.
- The standard Frappe service inventory includes configurator, backend, frontend, websocket, queue-short, queue-long and scheduler. Database and Redis cache/queue are represented as service dependencies and may be external, dedicated or separately provisioned.
- Compose definition remains an immutable NDF artifact/reference.

## 6. Tenant Site and Tenant Installed App target

### Tenant Site

| Fieldname | Type | Required | Treatment |
|---|---|---:|---|
| `bench_environment` | Link → Bench Environment | Yes | New authoritative parent |
| `node` | Link → Infrastructure Node | No during migration | Legacy/derived compatibility only; remove as independent source of truth |
| `deployment_type` | Existing Select (Shared/Dedicated) | Transitional | Do not reinterpret as Host/Docker; retain only if still needed for tenant hosting allocation |
| `erpnext_version` | Existing Data | Transitional | Derive from active Bench Runtime Component where practical |
| `database_name` | Data | Yes | Site-specific DB identity under environment profile |
| `installed_apps` | Table → Tenant Installed App | Yes | Retain site-level inventory |

Validation:
1. Every active site belongs to exactly one Bench Environment.
2. The environment must be active and accepting sites.
3. Node is resolved through Bench Environment.
4. Site cannot independently select another Node.
5. Site name/FQDN uniqueness follows existing rules.
6. Database allocation follows the environment profile and naming strategy.
7. Site app installation requires the app to be Available in Bench Environment Application.
8. Bench Environment cannot be decommissioned while active sites remain.
9. Moving a site requires an explicit site migration operation.

### Tenant Installed App

Retain existing fields for app identity, Extension/Extension Version, status, enabled state, installed/target versions, Git metadata and latest action diagnostics. Add/ensure validation against the parent Bench Environment Application inventory. Do not duplicate the complete Bench app source inventory in this site child table.

## 7. Deployment records

### 7.1 Deployment Candidate

Represents the complete desired runtime composition, not only the changed app.

| Fieldname | Type | Required |
|---|---|---:|
| `candidate_code` | Data | Yes |
| `bench_environment` | Link → Bench Environment | Yes |
| `candidate_type` | Select | Yes |
| `product` | Data | Yes |
| `product_release` | Data | Yes |
| `framework_version` | Link → Framework Version | Yes |
| `base_deployment` | Link → Bench Deployment | No |
| `composition_hash` | Data | Calculated |
| `status` | Select | Yes |
| `requested_by` | Link → User | Yes |
| `requested_on` | Datetime | Yes |

Candidate types: Initial, Add App, Remove App, Upgrade App, Runtime Upgrade, Product Upgrade, Configuration Change.

Candidate statuses: Draft, Validating, Ready for Build, Building, Built, Rejected, Deploying, Completed, Failed, Cancelled.

Child `Deployment Candidate Application`:

| Fieldname | Type | Required |
|---|---|---:|
| `app_name` | Data | Yes |
| `app_source_type` | Select | Yes |
| `app_source` | Data | Yes |
| `requested_version` | Data | No |
| `requested_commit` | Data | No |
| `is_required` | Check | Yes |
| `change_action` | Select | Yes |

Actions: Keep, Add, Upgrade, Remove. The resolved candidate must contain the full desired app inventory. A removal is rejected while any affected site still has the app installed.

### 7.2 Deployment Build

Tracks asynchronous NDF/GitHub Actions execution.

| Fieldname | Type | Required |
|---|---|---:|
| `deployment_candidate` | Link → Deployment Candidate | Yes |
| `provider` | Select | Yes |
| `workflow_name` | Data | Yes |
| `workflow_run_id` | Data | No |
| `workflow_url` | Data | No |
| `source_commit` | Data | Yes |
| `build_status` | Select | Yes |
| `image_repository` | Data | No |
| `image_digest` | Data | No |
| `descriptor_ref` | Data | No |
| `descriptor_hash` | Data | No |
| `started_on` | Datetime | No |
| `completed_on` | Datetime | No |
| `error_details` | Long Text | No |

Statuses: Pending, Submitted, Running, Succeeded, Failed, Cancelled. A successful workflow without a verifiable descriptor reference/hash and immutable image digest is not deployable.

### 7.3 Bench Deployment

Durable record of every deployment attempt.

| Fieldname | Type | Required | Rules |
|---|---|---:|---|
| `deployment_code` | Data | Yes | Unique identifier |
| `bench_environment` | Link → Bench Environment | Yes | Stable target |
| `deployment_candidate` | Link → Deployment Candidate | Yes | Desired composition |
| `deployment_build` | Link → Deployment Build | Conditional | Build producing artifact |
| `deployment_type` | Select | Yes | Initial, Replacement, Rollback, Recovery |
| `deployment_status` | Select | Yes | Pending, Provisioning, Starting, Verifying, Migrating, Activating, Successful, Failed, Rolled Back, Cancelled |
| `previous_deployment` | Link → Bench Deployment | No | Previous successful deployment |
| `image_repository` | Data | Yes | Exact repository |
| `image_digest` | Data | Yes | Immutable digest |
| `descriptor_ref` | Data | Yes | Exact descriptor |
| `descriptor_hash` | Data | Yes | Verified SHA-256 |
| `compose_definition_ref` | Data | Yes | Exact Compose definition |
| `compose_project_name` | Data | Yes | Deployment project identity |
| `storage_migration_plan` | Code | No | Validated JSON reuse/copy/migration plan |
| `affected_sites` | Code | Yes | Immutable site snapshot |
| `started_on` | Datetime | No | Start |
| `completed_on` | Datetime | No | Completion |
| `rollback_deployment` | Link → Bench Deployment | No | Rollback target |
| `failure_reason` | Long Text | No | Failure summary |

A deployment must preserve the exact descriptor and deployment inputs, either as immutable stored artifact bytes or a durable immutable artifact reference with verified digest. A mutable URL alone is insufficient.

One deployment lock per Bench Environment prevents conflicting operations. Failed deployment attempts never overwrite active deployment pointers. The prior deployment and recovery material remain available for the agreed rollback window.

A separate Deployment Operation Log child DocType is **not approved by this specification**. Reuse or extension of existing Node Action Log / Script Execution should be decided during the detailed logging/API design.

## 8. NDF Product Deployment Descriptor contract

NDF produces the descriptor after resolving, building and validating the complete composition. DT Master validates and consumes it. It is versioned independently from the product image and tied to the immutable OCI digest.

Required top-level sections:

| Section | Required contents |
|---|---|
| `schema` | Descriptor name and semantic version |
| `identity` | Product, product release, composition hash |
| `build` | Build ID, workflow run, source commit, timestamp |
| `image` | Repository, immutable digest, platform, labels |
| `framework` | Frappe/ERPNext versions and compatibility |
| `applications` | Complete resolved app inventory |
| `runtime` | Exact Python, Node, Yarn and other runtime versions |
| `dependencies` | Resolved Python, Node and OS dependency inventory/lock references |
| `services` | Compose definition reference and complete service definitions |
| `storage` | Required storage roles, mount targets and persistence requirements |
| `networking` | Networks, ports, routing and ingress requirements |
| `infrastructure` | Architecture, Docker/Compose requirements, CPU/memory/storage minimums |
| `secrets` | Required secret names/types/references; never values |
| `health` | Readiness and health checks |
| `operations` | Initialization, migration, backup, restore and recovery capabilities |
| `integrity` | Manifest, lock, composition and descriptor hashes |
| `provenance` | Source repository/commit, workflow and build provenance |

### Descriptor validation

DT Master rejects a descriptor when:
- schema version is unsupported;
- image is not digest-pinned or digest cannot be verified;
- product/release does not match candidate;
- app inventory differs from the resolved candidate;
- dependency graph is incomplete or invalid;
- framework/runtime constraints are incompatible;
- Node architecture, Docker/Compose capabilities or capacity are insufficient;
- required storage/service/secret references cannot be resolved;
- readiness/health or required lifecycle operations are absent;
- hashes or provenance cannot be verified.

### Application entry minimum

Each app entry includes `name`, `version`, `source_type`, `source`, `commit` where applicable, `dependencies`, `installation_order` and `is_required`. Names are unique; all dependencies resolve; Git sources use immutable commits.

### Service entry minimum

Each service entry includes `key`, `type`, `origin`, `enabled`, image/digest when containerized, command, dependencies/readiness, health check, storage, network, resources and an explicit `overrides` allowlist.

DT Master may override only allowlisted deployment parameters such as replica count, resource allocation, host port allocation, storage resource bindings and secret references/external service endpoints. It cannot replace the image, command or application composition without a new candidate/build.

### Storage and networking

Storage requirement entries identify a stable key, role, acceptable backend options, container path where applicable, access mode, persistence, encryption, backup requirement, retention, consumers and replacement-reuse behavior.

Networking entries identify network key/type, internal/external behavior, participating services, ports, external ingress requirement and routing mode. DT Master allocates host ports and external routing resources; NDF owns internal service topology.

The formal JSON Schema is to be maintained in the NDF repository beside the descriptor generator and versioned with the descriptor contract. This DT Master document records consumer-side requirements, not a second competing schema implementation.

## 9. Validation and integrity rules

| Concern | Rule |
|---|---|
| Environment identity | Unique, immutable environment code |
| Node relationship | One Node per Bench Environment; immutable after provisioning |
| Site relationship | One Bench Environment per Tenant Site; Node derived through parent |
| App inventory | Unique app name per environment; dependency-complete |
| Site app installation | App must be Available in parent Bench Environment |
| Storage | Unique storage key; backend-specific fields required; no unsafe deletion |
| Services | Unique service key; dependencies/storage references resolve |
| Descriptor | Supported schema; integrity hash and provenance verified |
| Image | OCI digest required; tag is informational |
| Deployment | One active operation lock per environment |
| Deployment history | Terminal deployment record immutable except controlled annotations |
| Secrets | Secret references only; no plaintext credentials |
| JSON fields | Schema-validated, size-limited, controlled writers |
| Lifecycle | State transitions through server-side services only |
| Capacity | Validate allocation against Node before provisioning |

Recommended indexes:
- Bench Environment: `infrastructure_node`, `status`, `environment_code`
- Tenant Site: `bench_environment`
- Bench Deployment: `bench_environment`, `deployment_status`
- Deployment Candidate: `bench_environment`
- Deployment Build: `workflow_run_id`

Use database uniqueness constraints where they can correctly express the invariant; enforce parent-scoped child uniqueness and cross-document invariants in server-side validation and transactional services.

## 10. Lifecycle rules

### Bench Environment
- Draft → Provisioning → Active
- Active ↔ Updating / Migrating / Stopped
- Provisioning or operational states may transition to Failed/Degraded according to operation outcome
- Active/Stopped → Decommissioning → Retired

Health is separate from lifecycle. An Active environment may be Degraded. A failed deployment does not automatically retire the environment.

### Bench Deployment
Pending → Provisioning → Starting → Verifying → Migrating → Activating → Successful.

Failure at any stage records the failure and triggers a controlled recovery/rollback decision. Active image/descriptor pointers change only after successful verification and activation.

### Site and Bench decommissioning
A Bench Environment cannot be retired while it has active Tenant Sites. Bench retirement must preserve site databases, files, encryption/recovery material and backups. Site destruction remains a separate explicit destructive operation. Retiring one Bench Environment must never retire its Infrastructure Node.

## 11. Implementation and migration sequence

This is the agreed implementation plan; it is not authorization to begin code changes before the M4.1 contract review is complete.

### Phase A — Schema foundation
1. Add Bench Environment parent DocType and four child DocTypes.
2. Add Deployment Candidate and its application child table.
3. Add Deployment Build and Bench Deployment.
4. Add server-side controllers/validators for parent-child uniqueness, immutable identity, descriptor validation and lifecycle state transitions.
5. Add permissions so ordinary tenant-facing users cannot edit runtime composition, service definitions, storage bindings or deployment history.

### Phase B — Existing DocType refactor
1. Infrastructure Node: preserve Node identity, SSH, OS, Docker Engine, provider, capacity and Node-level logs. Relocate Bench-specific paths, WSGI/service ports, framework runtime and Bench lifecycle actions.
2. Tenant Site: add mandatory Bench Environment relationship; retain Node as transitional compatibility data only; resolve Node through parent; remove duplicate editable ownership.
3. Tenant Installed App: preserve site installation state and add Bench availability checks.
4. Framework Version: preserve compatibility baseline; distinguish requested compatibility from actual deployed component versions.
5. Framework Version Dependency/System Dependency: classify dependencies as Node infrastructure prerequisites versus image/runtime dependencies resolved by NDF.
6. Execution/logging: preserve generic Script Execution and existing action history; introduce deployment-specific history only if the detailed audit proves existing records insufficient.

### Phase C — Migration and compatibility
1. Inventory every existing Infrastructure Node and Tenant Site.
2. Create/map Bench Environments for existing Host-native benches; do not assume one Node equals one Bench forever.
3. Populate each Tenant Site's Bench Environment using its existing Node and verified Bench location/runtime.
4. Preserve existing site app state and reconcile it with the Bench app inventory.
5. Migrate secrets to protected references and verify new secret resolution.
6. Validate all site-to-Bench-to-Node relationships before retiring the legacy direct Node field.
7. Keep Host adapter functional throughout the transition.

### Phase D — NDF contract integration
1. Finalize the formal descriptor JSON Schema in NDF.
2. Implement descriptor generation and immutable publication in NDF.
3. Implement DT Master descriptor retrieval, hash/signature/provenance verification and compatibility validation.
4. Populate Bench Runtime Component, Bench Environment Application, Bench Service Definition and storage requirements from verified descriptor data.
5. Keep deployment-time resource assignments in DT Master; do not leak host paths/volume names into NDF product manifests.

### Phase E — Runtime and deployment orchestration
1. Define common Host and Docker adapter interfaces.
2. Implement Node preflight and capacity checks.
3. Implement storage and external service profile resolution.
4. Implement Compose deployment using NDF-provided definition and approved overrides.
5. Implement readiness, site migration, verification, activation and rollback.
6. Ensure all operations are asynchronous, auditable and concurrency-safe.
7. Test failure injection at each deployment stage.

### Phase F — Acceptance
- Multiple Bench Environments can coexist on one Node.
- One Bench Environment can host multiple Tenant Sites.
- Tenant Site Node is derived through Bench Environment.
- Host-native lifecycle still works.
- Docker deployment uses digest-pinned NDF images and descriptor-defined services.
- Product and marketplace app inventories reconcile correctly.
- Site app installation rejects apps unavailable in its Bench.
- Storage survives container replacement and can be restored independently.
- Logs are separated between Node and Bench Environment.
- Failed replacement does not change active deployment.
- Rollback restores a verified previous runtime and site state.
- No production cutover occurs without explicit acceptance.

## 12. Items deliberately not frozen by this document

The following require their own M4.1 contracts or implementation audits:

1. Exact secret-provider implementation and rotation strategy.
2. Exact dedicated/shared database and Redis provisioning profiles.
3. Exact Compose project replacement/blue-green naming and ingress cutover procedure.
4. Exact API endpoints and asynchronous callback/polling contract between DT Master and NDF.
5. Whether deployment stage logs use an extension of Node Action Log, Script Execution or a dedicated child table.
6. Exact Frappe field options, naming-series configuration and migration patch mechanics for the supported Frappe release.
7. Formal NDF JSON Schema and descriptor artifact publication layout.

These are intentionally separated from the approved ownership and schema principles so they can be resolved without reopening the hierarchy.

## 13. Source references

- [DT Master develop branch](https://github.com/NdfTechLabs/dt_master/tree/develop)
- [Current M4 integration record](M4_NDF_FRAPPE_DOCKER_INTEGRATION.md)
- [NDF Frappe Docker architecture](https://github.com/NdfTechLabs/frappe_docker/tree/main/images/ndf)
- [Upstream Frappe Docker production Compose](https://github.com/frappe/frappe_docker/blob/main/compose.yaml)
- [Upstream Frappe Docker disposable demo Compose](https://github.com/frappe/frappe_docker/blob/main/pwd.yml)

**Change control:** changes to the hierarchy, ownership, immutable deployment model or five approved architecture principles require explicit review. Implementation must not silently reinterpret this specification.


## 14. Formal schema migration matrix

**Status:** Reviewed and approved for recording on 2026-11-09.  
**Scope:** Field-level disposition of the existing DT Master schema against the approved Bench Environment ownership model.  
**Implementation status:** Documentation only. No DocTypes, controllers, APIs, migration patches, scripts or runtime configuration are changed by recording this matrix.

### 14.1 Disposition definitions

| Disposition | Meaning |
|---|---|
| **Retain** | Keep the field on its existing DocType as an authoritative field. |
| **Move** | Transfer ownership to the target DocType or contract, preserving the value and relevant history. |
| **Replace** | Introduce a new authoritative field or relationship and migrate the old value before retiring the old field. |
| **Derive** | Resolve the value from another authoritative record; do not maintain an independently editable duplicate. |
| **Remove** | Retire the field or operation after data migration, compatibility checks and the agreed observation period. |

A field marked **Retain / review** remains in place during migration; its final semantics must be resolved before implementation. A field marked **Move / retire** must retain historical evidence where required, but must not remain a competing source of truth.

### 14.2 Infrastructure Node

**Target ownership:** Infrastructure Node represents the machine or infrastructure resource, not an individual Frappe Bench.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `node_name` | Retain | Stable Node identity. |
| `provider` | Retain | Infrastructure provider relationship. |
| `region` | Retain | Authoritative Node region. |
| `node_type` | Retain | Preserve Shared/Dedicated Node classification; it is not Host/Docker runtime type. |
| `hosting_model` | Retain | Preserve Compute/Managed infrastructure classification. |
| `status` | Retain | Node lifecycle: Provisioning, Active, Maintenance, Retired. |
| `state` | Replace | Separate infrastructure readiness from Bench lifecycle. Node readiness covers SSH, OS, Docker Engine and capacity; Bench states move to Bench Environment. |
| `frappe_version` | Move | Move to `Bench Environment.framework_version`; one Node may host multiple Benches with different versions. |
| `cpu_cores` | Retain | Physical/provisioned Node capacity. |
| `ram_gb` | Retain | Node capacity. |
| `storage_gb` | Retain | Node capacity. |
| `max_sites` | Retain | Node-wide capacity guard; Bench-specific limits may be added separately. |
| `ip_address` | Retain | Node access endpoint. |
| `ssh_user` | Retain | Node access identity. |
| `ssh_port` | Retain | Node access configuration. |
| `access_verified` | Retain | Human safety latch for Node access. |
| `access_notes` | Retain | Node-level operational notes. |
| `os_type` | Retain | Node OS compatibility. |
| `os_version` | Retain | Node OS version. |
| `scripts_base_dir` | Retain | Node automation script location; not a Bench runtime directory. |
| `project_base_dir` | Replace | Replace ambiguous project root with a defined Node workload/data root. Migrate existing Host Bench paths to their Bench Environment records. |
| `project_logs_dir` | Replace | Separate Node-level logs from Bench runtime logs. Bench logs become Bench Environment storage/runtime references. |
| `project_service_name` | Move | Move service identity to Bench Environment/service inventory; a Node may host multiple Bench projects. |
| `wsgi_port` | Move | Move to Bench Environment/service networking allocation. |
| `install_dependencies` | Replace | Node action provisions infrastructure prerequisites only. Image/runtime dependencies belong to NDF; Bench provisioning belongs to Bench Environment. |
| `install_bench` | Move | Bench Environment provisioning action. |
| `initialize_project` | Move | Bench Environment initialization action. |
| `prepare_project` | Move | Bench Environment readiness/preparation action. |
| `start_project` | Move | Bench Environment start operation. |
| `stop_project` | Move | Bench Environment stop operation. |
| `check_system_status` | Retain | Node infrastructure health check, distinct from Bench health. |
| `node_action_logs` | Retain | Preserve general Node operational history. Bench Deployment Stage owns deployment-stage history; Script Execution/Execution Log retain command-level detail. |
| `monthly_cost` | Retain | Node cost accounting. |
| `currency` | Retain | Node cost accounting. |
| `billing_owner` | Retain | Node cost allocation. |
| `auto_provisioned` | Retain | Node provisioning metadata. |
| `provisioned_on` | Retain | Node provisioning timestamp. |

Path ownership must be explicit: Node root and automation paths belong to Node; Host Bench root belongs to Bench Environment; container paths come from descriptor/storage mappings; Bench logs and persistent data are Bench Environment-owned. Do not copy one Node path into every Bench Environment.

### 14.3 Tenant Site

**Target ownership:** Tenant Site represents one Frappe site, its site-specific data and installation state. Bench Environment is its only authoritative runtime parent.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `site_name` | Retain | Stable site identity. |
| `domain_type` | Retain | Site domain classification. |
| `protocol` | Retain / review | Preserve endpoint behavior pending ingress contract; later decide whether it is site-owned or derived. |
| `port` | Retain / review | Preserve existing values pending Node ingress and port-allocation contract. Existing field is Data; do not silently coerce. |
| `fqdn` | Retain | Site hostname and routing identity. |
| `status` | Retain | Site lifecycle status. |
| `automation_state` | Retain / refine | Site lifecycle only; Bench deployment state belongs to Bench Deployment. |
| `customer` | Retain | Site ownership relationship. |
| `subscription` | Retain | Site subscription relationship. |
| `infrastructure_profile` | Retain | Preserve requested/assigned profile unless subscription design explicitly relocates it. |
| `node` | Replace | Add mandatory `bench_environment`; migrate existing relationship and then retire direct editable Node ownership. |
| `deployment_type` | Deprecate / retire after migration | Existing Shared/Dedicated values describe tenant allocation semantics, not Host/Docker. Preserve during migration, migrate all consumers to the appropriate allocation model, then retire when no longer authoritative. |
| `region` | Derive | Resolve through Bench Environment → Infrastructure Node → region after reconciling existing free-text values. |
| `erpnext_version` | Derive / replace | Resolve from Bench Environment application inventory; do not maintain as an independent runtime version. |
| `database_name` | Retain | Site-specific database identity, validated against Bench database profile and naming strategy. |
| `installed_apps` | Retain | Keep Tenant Installed App as site-specific child table. |
| `created_on` | Retain | Site creation history. |
| `auto_provisioned` | Retain | Site provisioning metadata. |
| `last_health_check` | Retain | Site-level health observation, distinct from Bench and Node health. |
| `provisioning_stage` | Retain / refine | Site creation stages only; Bench provisioning/deployment stages belong to Bench Deployment. |
| `automation_logs` | Retain | Preserve existing child log history and parentage. |
| `tenant_api_user` | Retain | Non-secret user identity metadata, subject to authentication design. |
| `tenant_api_key` | Move | Migrate to protected secret reference; retire plaintext only after verification. |
| `tenant_api_secret` | Move | Migrate to protected secret reference; never duplicate into ordinary Data fields. |
| `admin_password` | Move / retire | Use protected, purpose-scoped secret only where operationally required; avoid indefinite storage of initial password. |
| `mysql_root_user_name` | Move | Database administration identity belongs to Bench database profile, except a separately justified site-scoped account. |
| `mysql_root_user_password` | Move | Migrate to protected database secret reference; retire plaintext after verification. |
| `create_site` | Retain as site action | Execute against the selected Bench Environment. |
| `install_apps` | Retain as site action | Validate app availability in parent Bench Environment first. |
| `verify_site` | Retain | Site-level verification. |
| `configure_proxy` | Retain as request/action | Actual routing coordinated with Node ingress and Bench topology. |
| `suspend_site` | Retain | Site lifecycle operation. |
| `resume_site` | Retain | Site lifecycle operation. |
| `destroy_site` | Retain | Explicit destructive site operation with existing safety safeguards. |

#### Tenant Site relationship migration rule

For each existing Tenant Site:

1. Read its current `node` value.
2. Resolve or create the corresponding Bench Environment on that Node.
3. Assign `bench_environment`.
4. Validate that the Bench Environment belongs to the same Node as the legacy relationship.
5. Verify site membership, database identity and storage ownership.
6. Retire the direct `node` field only after validation and compatibility checks.

If a Node has one actual Host Bench, the migration may create one Host Bench Environment and associate its sites with it. If a Node has multiple actual Benches, use filesystem/runtime evidence to distinguish them. **Do not automatically group all sites on a Node into one Bench Environment.**

### 14.4 Tenant Installed App

**Target ownership:** Tenant Installed App remains a Tenant Site child. It records installation on that site, not whether app code exists in the shared Bench runtime.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `app_name` | Retain | Technical app identifier; validate against Bench Environment Application. |
| `app_type` | Derive / retain as snapshot | Classification comes from Bench inventory; preserve historical snapshot if useful, but not as a competing source. |
| `extension` | Retain | Marketplace Extension relationship. |
| `extension_version` | Retain | Requested or installed marketplace release reference. |
| `status` | Retain | Site installation state. |
| `enabled` | Retain | Site-level enabled state. |
| `installed_version` | Retain | Snapshot of version installed on this site; reconcile with Bench availability. |
| `target_version` | Derive / replace | Resolve from requested Extension Version or App Installation Request; not proof of runtime availability. |
| `installed_at` | Retain | Site installation audit. |
| `last_updated` | Retain | Site installation audit. |
| `last_error` | Retain | Last site installation error. |
| `last_action` | Retain | Last site-level app operation. |
| `last_action_status` | Retain | Last operation status. |
| `last_job_id` | Retain | Background job correlation. |
| `sync_state` | Retain | Site-level synchronization status; define comparison against Bench availability and requested version. |
| `git_branch` | Move / retire | Source branch belongs to Bench app build provenance; preserve historical evidence if required. |
| `git_commit` | Move | Actual runtime commit belongs in Bench Environment Application and descriptor; retain historical site snapshot only if needed for audit. |
| `git_dirty` | Remove after audit migration | Immutable runtime image must not contain an uncommitted working tree; preserve historical evidence if operationally used. |
| `git_head_state` | Remove after audit migration | Runtime source state belongs to build provenance, not site installation state. |
| Install/upgrade/downgrade/reinstall/uninstall/check/remove buttons | Retain as site actions with orchestration changes | Route through App Installation Request/site operation service. They must not mutate shared Bench code directly. |

Before a site app operation, resolve Site → Bench Environment and verify that the app is Available. If unavailable or the requested version changes the shared composition, create an App Installation Request and Deployment Candidate, build/deploy a replacement Bench, then perform the site installation.

### 14.5 Framework Version

**Target ownership:** Framework Version is the compatibility catalogue, not the exact runtime record for every Bench Environment.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `framework` | Retain | Framework identity. |
| `version` | Retain | Framework release/version. |
| `python_version` | Retain / redefine | Compatibility baseline or supported version metadata; exact deployed Python belongs in Bench Runtime Component. |
| `node_version` | Retain / redefine | Compatibility baseline or supported version metadata; exact deployed Node belongs in Bench Runtime Component. |
| `bench_package` | Retain / extend | Preserve Bench package compatibility metadata; exact deployed version recorded in runtime inventory/provenance. |
| `description` | Retain | Compatibility notes. |

Extend with supported compatibility ranges and relevant package-manager/framework constraints. Keep supported versions distinct from exact installed versions.

### 14.6 Framework Version Dependency and System Dependency

Classify each existing dependency record individually. The existing `dependency_group` does not by itself determine ownership.

#### Framework Version Dependency

| Existing field | Disposition | Target treatment |
|---|---|---|
| `framework_version` | Retain as migration reference | Identifies the compatibility policy from which the dependency was declared. |
| `dependency` | Split / move | Classify as Node prerequisite, NDF image/runtime dependency or external service requirement. |
| `enabled` | Retain as migration metadata | Preserve whether the dependency was active; resolved composition becomes authoritative. |
| `install_order` | Remove from runtime authority | Native ecosystem resolvers and NDF lock determine install order; preserve legacy value as audit metadata if useful. |

#### System Dependency

| Existing field | Disposition | Target treatment |
|---|---|---|
| `dependency_name` | Retain | Stable dependency identity. |
| `install_method` | Replace / classify | Map apt/pip/npm/script/nvm to correct owner and resolver. |
| `dependency_group` | Replace / classify | Introduce explicit ownership/scope classification; current system/python/node values are insufficient. |
| `repository` | Conditional retain / move | Node package repositories remain infrastructure configuration; image package sources belong in NDF build definitions. |
| `version` | Split / move | Image dependency versions belong in NDF lock; Node prerequisite versions belong in Node capability validation. |
| `description` | Retain | Dependency documentation and migration context. |

Dependency ownership:

| Dependency class | Owner | Responsibility |
|---|---|---|
| Node infrastructure prerequisite | DT Master / Infrastructure Node | Docker Engine, Compose plugin, OS-level host requirements and SSH tooling. |
| Image/runtime dependency | NDF Frappe Docker | Python packages, Node packages and OS libraries required by apps/framework. |
| External service dependency | Bench Environment / service profile | External database, Redis, object storage and managed service endpoints. |
| Exact deployed component | Bench Runtime Component | Observed Python, Node, package, library and framework versions. |

Do not classify every apt dependency as Node-level. Some OS libraries must be baked into the product image. Likewise, classify Python/Node packages by where they are installed and consumed. Existing dependency rows are migration inputs; NDF descriptor and lock become authoritative for image/runtime dependencies.

### 14.7 Extension and Extension Version

Separate DT Master marketplace catalogue responsibilities from NDF build/runtime provenance.

#### Extension

| Existing field | Disposition | Target treatment |
|---|---|---|
| `extension_name` | Retain | Marketplace identity. |
| `app_name` | Retain | Technical app binding; must match NDF descriptor and Bench inventory. |
| `publisher` | Retain | Marketplace ownership metadata. |
| `visibility` | Retain | Listing visibility. |
| `pricing_model` | Retain | Marketplace pricing classification. |
| `status` | Retain | Marketplace approval state. |
| `title` | Retain | Listing title. |
| `short_description` | Retain | Listing summary. |
| `description` | Retain | Marketplace description. |
| `category` | Retain | Marketplace classification. |
| `tags` | Retain | Search metadata. |
| `icon` | Retain | Marketplace asset. |
| `screenshots` | Retain | Marketplace assets. |
| `install_type` | Retain / clarify | Preserve System App/Tenant App as catalogue metadata; it cannot bypass Bench availability rules. |
| `requires_restart` | Derive / replace | Runtime restart requirements come from NDF descriptor/service contract. |
| `post_install_hook` | Review / replace | Hooks must be approved lifecycle operations; arbitrary marketplace hooks cannot execute outside deployment contract. |
| `pre_install_checks` | Retain / formalize | Convert machine-checkable requirements into descriptor compatibility/dependency declarations. |
| `supports_uninstall` | Retain | Marketplace capability metadata; does not authorize removal of shared code while sites depend on it. |
| `supports_upgrade` | Retain | Marketplace capability metadata; upgrade still requires compatible Bench composition. |
| `repository_url` | Move / synchronize | Canonical source belongs in NDF Marketplace App Descriptor; DT Master may retain read-only/synchronized reference. |
| `default_branch` | Move / synchronize | Source default belongs in NDF app metadata; branch is not immutable deployment identity. |
| `install_mode` | Replace | fixed/rolling must not independently control runtime mutation; use explicit approved deployment strategy. |
| `price_type` | Retain | Marketplace commercial metadata. |
| `price_amount` | Retain | Marketplace commercial metadata. |
| `billing_interval` | Retain | Marketplace commercial metadata. |
| `trial_days` | Retain | Marketplace commercial metadata. |
| `requires_subscription` | Retain | Marketplace entitlement metadata. |
| `license` | Retain | Marketplace trust/compliance metadata. |
| `data_access_level` | Retain | Marketplace trust metadata. |
| `external_services` | Retain / formalize | Convert required services into structured NDF descriptor dependencies. |
| `security_reviewed` | Retain | Marketplace approval evidence. |
| `documentation_url` | Retain | Marketplace documentation. |
| `support_contact` | Retain | Marketplace support metadata. |

#### Extension Version

| Existing field | Disposition | Target treatment |
|---|---|---|
| `version` | Retain | Marketplace release identity. |
| `frappe_min` | Retain / extend | Compatibility constraint aligned with NDF framework compatibility declaration. |
| `frappe_max` | Retain / extend | Compatibility constraint aligned with NDF framework compatibility declaration. |
| `release_notes` | Retain | Release history. |
| `deprecated` | Retain | Release lifecycle metadata. |
| `extension` | Retain | Parent Extension relationship. |
| `checksum_or_signature` | Retain / formalize | Tie verification metadata to immutable source/artifact or descriptor digest. |
| `source_ref` | Retain / strengthen | Preserve source reference; resolve actual builds to immutable commit. |
| `ref_type` | Retain as source metadata | Preserve branch/tag/commit classification; deployed build uses immutable commit. |
| `install_strategy` | Replace | Direct Git installation is no longer production runtime deployment; release becomes input to Deployment Candidate/NDF build. |
| `status` | Retain | Draft/Approved/Revoked release status. |

Extension Version approval does not prove that an app can be installed into an existing Bench. Bench availability is a separate validation.

### 14.8 Node Action Log

Preserve this child table and its historical records. It is currently reused by Infrastructure Node and Tenant Site.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `action_name` | Retain | Operation name. |
| `action_type` | Retain | Operation classification. |
| `triggered_by` | Retain | Actor/audit identity. |
| `started_at` | Retain | Start timestamp. |
| `finished_at` | Retain | Completion timestamp. |
| `exit_code` | Retain | Execution result. |
| `stdout` | Retain | Preserve historical output; review for leaked secrets. |
| `stderr` | Retain | Preserve historical errors; review for leaked secrets. |
| `state_before` | Retain / generalize | Preserve prior state; existing Node-specific label is too narrow for reuse. |
| `state_after` | Retain / generalize | Preserve resulting state. |
| `result` | Retain | Operation summary. |
| `state` | Retain | Log execution state. |

**Resolved deployment logging decision:** Bench Deployment owns a dedicated child table, **Bench Deployment Stage**, for ordered lifecycle stages and their status, timing, outcome, and linked execution reference. Script Execution and Execution Log remain the technical execution records for commands, exit codes, streamed output, and detailed diagnostics. Node Action Log remains for general Node/Site operational actions and is not overloaded with deployment-stage history. Historical stdout/stderr must still be reviewed for leaked secrets and access controls.

### 14.9 Script Execution and Execution Log

Script Execution remains the generic execution record, not a substitute for Bench Deployment.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `script` | Retain | Script Definition relationship. |
| `script_file` | Retain | Executed script reference. |
| `node` | Retain as execution snapshot / derive where possible | Preserve actual execution Node. For Bench/Site targets, resolve through target Bench Environment; do not use as Site ownership. |
| `target_doctype` | Retain | Generic execution target type. |
| `target_document` | Retain | Generic target; allow Bench Environment/Bench Deployment where appropriate. |
| `status` | Retain | Queued/Running/Completed/Failed state. |
| `exit_code` | Retain | Execution result. |
| `queued_at` | Retain | Queue audit. |
| `started_at` | Retain | Execution audit. |
| `finished_at` | Retain | Execution audit. |
| `duration` | Retain | Execution metric. |
| `triggered_by` | Retain | User audit. |
| `triggere_source` | Retain; correct naming in a controlled migration | Preserve Manual/API/Scheduler semantics; do not silently rename the field. |
| `execution_logs` | Retain | Preserve output/event history; review for secret leakage. |

Execution context rules:
- Node-targeted scripts execute against Infrastructure Node.
- Bench-targeted scripts resolve Bench Environment and then its Node.
- Site-targeted scripts resolve Tenant Site → Bench Environment → Infrastructure Node.
- Deployment lifecycle is recorded in Bench Deployment; Script Execution may be linked as execution detail.

Preserve SSH transport, output/realtime streaming, exit-code handling and secret-redaction guarantees during adapter transition.

### 14.10 Mail Server

Mail Server remains a separate infrastructure workload hosted on an Infrastructure Node. It is not a Bench Environment.

| Existing field | Disposition | Target treatment |
|---|---|---|
| `node` | Retain | Direct Infrastructure Node relationship. |
| `tenant_site` | Retain / validate | Preserve optional tenant/domain association; it does not make Mail Server part of the site's Bench. |
| `domain` | Retain | Mail domain identity. |
| `hostname` | Retain | Mail server hostname. |
| `mail_users` | Retain | Mailbox inventory. |
| `automation_state` | Retain | Mail workload lifecycle. |
| `status` | Retain | Mail workload status. |
| `automation_logs` | Retain | Preserve Mail Server Log history. |
| `smtp_port` | Retain | Mail service port. |
| `imap_port` | Retain | Mail service port. |
| `dkim_selector` | Retain | Mail authentication configuration. |
| `install_mail_stack` | Retain | Mail workload operation. |
| `configure_postfix` | Retain | Mail workload operation. |
| `configure_dovecot` | Retain | Mail workload operation. |
| `configure_rspamd` | Retain | Mail workload operation. |
| `configure_opendkim` | Retain | Mail workload operation. |
| `configure_dns` | Retain | Mail workload operation. |
| `verify_mail_server` | Retain | Mail workload verification. |

Mail execution context must use Mail Server-specific workload paths and logging, not Frappe Bench project paths.

### 14.11 New authoritative fields and relationships

| DocType | New authoritative field/relationship | Purpose |
|---|---|---|
| Bench Environment | `infrastructure_node` | One parent Node for each Bench. |
| Bench Environment | `runtime_type` | Host or Docker, distinct from Node and tenant classifications. |
| Bench Environment | `framework_version` | Bench framework compatibility baseline. |
| Bench Environment | Product/release identity | NDF product composition identity. |
| Bench Environment | Descriptor reference/hash and image digest | Immutable deployed runtime identity. |
| Bench Environment | Compose project identity and runtime adapter | Identifies how the Bench is operated. |
| Bench Environment | DB/Redis profiles and protected secret references | Owns runtime service relationships. |
| Bench Environment | Application/runtime/storage/service child tables | Complete runtime inventory and allocated resources. |
| Tenant Site | `bench_environment` | Mandatory authoritative parent. |
| Bench Environment Application | Complete app inventory | Defines code available to all sites in the Bench. |
| Bench Runtime Component | Exact observed versions | Separates runtime facts from compatibility catalogue. |
| Bench Storage Reference | Storage identity and lifecycle | Makes persistence and ownership explicit. |
| Bench Service Definition | Descriptor-derived service inventory | Exposes topology without making DT Master owner of Compose definitions. |
| Deployment Candidate | Desired complete composition | Records intended Bench composition. |
| Deployment Candidate Application | Desired app inventory | Records retained, added, upgraded and removed apps. |
| Deployment Build | NDF workflow/build result | Tracks asynchronous build and immutable outputs. |
| Bench Deployment | Deployment/migration/rollback history | Records actual deployment lifecycle. |
| App Installation Request | Site app request/orchestration state | Separates tenant requests from shared Bench changes. |

**Inventory distinction:** Bench Environment Application means app code is available in the shared runtime. Tenant Installed App means the app is installed on a particular site. Availability does not imply installation on every site.

### 14.12 Approved data migration order

The migration follows ownership dependencies. Do not change Tenant Site's Node relationship before Bench Environments exist and have been mapped.

| Order | Migration activity | Preconditions / validation |
|---:|---|---|
| 1 | Inventory existing Nodes, Tenant Sites and actual Host Bench directories. | Establish real site-to-Bench grouping. |
| 2 | Classify existing Node paths and service identities. | Separate Node-level paths from Bench-specific paths. |
| 3 | Create Bench Environment records for existing Host Benches. | Preserve Host runtime and existing operational identity. |
| 4 | Populate Bench runtime/configuration fields. | Framework, Bench paths, service identity, DB/Redis profile and storage references. |
| 5 | Migrate Tenant Site relationships. | Set `bench_environment`; verify parent Node equals legacy Node. |
| 6 | Reconcile site version and database metadata. | Check ERPNext version, database names and app records against actual runtime/site state. |
| 7 | Migrate app inventory. | Populate Bench Environment Application from actual Bench apps and product composition. |
| 8 | Reconcile Tenant Installed App rows. | Preserve site installation state and verify app availability in parent Bench. |
| 9 | Classify dependency records. | Separate Node prerequisites, NDF image dependencies and external service dependencies. |
| 10 | Extend Framework Version and Extension metadata. | Add compatibility/provenance without losing catalogue history. |
| 11 | Migrate secrets. | Move credentials to protected references; verify resolution and rotate where required. |
| 12 | Preserve and reconcile logs/execution history. | Maintain historical parentage and auditability. |
| 13 | Switch application reads to the new ownership model. | Only after relationship/runtime validation passes. |
| 14 | Retire old fields and actions. | Only after compatibility checks and successful observation period. |

#### Migration safeguards

1. Do not assume Node-to-Bench one-to-one. A Node can host multiple Bench Environments.
2. Do not automatically regroup sites. Actual filesystem/runtime evidence determines Bench membership.
3. Do not delete data during relationship migration. Preserve old Node values and credential recovery capability until validation succeeds.
4. Do not duplicate plaintext secrets. Use a protected provider and verify access before retiring old fields.
5. Do not perform destructive Bench cleanup. Retirement must not delete site databases, files or recovery material.
6. Preserve Host compatibility throughout migration.
7. A migrated Host Bench does not become Docker-backed merely because the target architecture supports Docker.
8. Do not overwrite deployment history. Each successful or failed deployment retains its immutable descriptor and digest.

### 14.13 Implementation sequence following matrix approval

1. **Schema foundation:** Create Bench Environment and four child tables. Extend Node and Tenant Site without retiring legacy fields.
2. **Host migration:** Create Host Bench Environment records, map existing sites and populate runtime/storage/application information.
3. **Relationship cutover:** Switch Tenant Site ownership to Bench Environment and validate existing site operations.
4. **Application governance:** Introduce Bench app availability validation and App Installation Request orchestration.
5. **Deployment records:** Implement Deployment Candidate, Deployment Build and Bench Deployment.
6. **Dependency and secret migration:** Introduce dependency ownership and protected secret references.
7. **Runtime adapter transition:** Move Bench operations behind Host and Docker adapters.
8. **Legacy retirement:** Remove obsolete Node Bench fields and direct Tenant Site Node ownership only after Host migration and compatibility checks.

**Ordering constraint:** Prove the new ownership model against existing Host deployments before Docker orchestration. This separates data-model migration from runtime migration and makes failures easier to isolate.

### 14.14 Outstanding decisions and schema observations

The matrix records ownership direction. The decisions below distinguish contracts now agreed from contracts that still require resolution.

#### Resolved in this architecture review

| Item | Recorded decision |
|---|---|
| Application identity | Introduce a stable, globally unique `app_id` shared by DT Master and NDF. It is assigned during application registration, remains immutable across releases and is independent of repository URL, display name and product composition. |
| Application registration ownership | DT Master owns developer registration, verified publisher identity, display/listing content, marketplace visibility, pricing and marketplace governance. |
| Technical application/release authority | NDF owns the Marketplace App Descriptor and technical release provenance, including repository, immutable commit, framework compatibility, Python/Node/OS dependencies, application dependencies and release identity. |
| Application descriptor registry | NDF maintains the canonical registry of published application descriptors/releases. The application repository contains the descriptor source; NDF validates and publishes an immutable descriptor artifact. |
| Existing NDF manifest schema | Retain `application-manifest-v1` for its current composition-manifest role and backward compatibility. It is not the per-application Marketplace App Descriptor. Do not replace the existing resolver or lock mechanism. Extend the NDF design so composition manifests can reference registered `app_id` and release versions, which the resolver expands into the existing immutable application lock. |
| Artifact separation | Keep three distinct artifact levels: (1) Marketplace App Descriptor for an application/release, (2) product composition manifest selecting application releases, and (3) Product Deployment Descriptor describing the exact built image/runtime artifact. |
| DT Master release synchronization | When NDF publishes a validated immutable application release, it notifies DT Master with the `app_id`, release version and immutable descriptor/artifact reference and integrity information. DT Master finds or creates the Extension, creates or updates the corresponding Extension Version, synchronizes technical metadata and places the release in Pending Review. Marketplace approval remains a DT Master decision. |
| Product inclusion governance | Product inclusion is separate from application registration and marketplace release approval. DT Master creates a Product Inclusion Request identifying target product, `app_id`, release version and requested immutable commit/artifact. DT Master submits an authenticated request to NDF; NDF validates compatibility and proposes the manifest change through a pull request. An authorized product maintainer approves and merges the change. NDF then builds, validates and publishes the immutable product image. DT Master does not directly edit product manifests. |
| Marketplace/product approval separation | Approval to list or publish an application release in the marketplace does not automatically authorize its inclusion in any product. Product composition requires its own product-owner approval. |
| Extension source metadata | NDF descriptor is authoritative for technical source and runtime metadata. DT Master may retain synchronized, read-only references for marketplace display and traceability; it must not independently govern repository refs or technical dependency declarations. |

#### Replacement Bench migration and formal rollback boundary

Replacement Bench migration must use isolated data copies as the mandatory
default. The active source Bench and its production databases/files must not
be modified while the replacement is being prepared.

1. Take consistent backups of each affected Tenant Site database, site files
   and relevant configuration/recovery material.
2. Prepare isolated database and site-file copies for the replacement Bench.
   Required Frappe and application migrations run against these copies only.
3. Validate the replacement runtime, application compatibility, database
   connectivity, site configuration and health checks before cutover.
4. At cutover, freeze writes to the source sites and perform the required final
   synchronization into the replacement copies. The exact synchronization
   mechanism is implementation-specific and must be validated for each
   supported database/storage profile.
5. Keep writes frozen while routing is switched and every affected Tenant Site
   is verified through the replacement runtime and actual Node ingress.
6. If verification fails before migration commit, restore routing to the
   original Bench. The original Bench and its source data remain authoritative;
   the failed replacement is isolated for investigation or safely discarded.
7. Migration commit occurs only after cutover verification succeeds and the
   platform authorizes production writes on the replacement Bench. Record the
   commit point, write-freeze state, synchronization result, routing activation
   and per-site verification in the deployment history.
8. After migration commit, returning to the old Bench is not an automatic
   rollback. It requires a separately validated recovery/reconciliation
   procedure that accounts for writes accepted by the replacement. Routing
   alone must never be switched back if doing so could lose or diverge
   production transactions.
9. If a consistent return to the old Bench cannot be proven, block automatic
   rollback and require an authorized recovery decision. Preserve the
   replacement's current data and recovery evidence.

A code/image rollback is not, by itself, a database rollback. Frappe schema
migrations may make an older application runtime incompatible with the
replacement database. The recovery plan must explicitly account for database
schema and data compatibility, site files, configuration and writes accepted
after cutover.

The rollback window must be explicitly configured and recorded for each
deployment. The previous Bench, source data and required recovery material
must remain available for that window. Capacity, backup retention, synchronization
duration and supported database/storage profile constraints are implementation
requirements assigned to the relevant M4 milestones.

#### Resolved infrastructure and schema decisions

The following decisions have been discussed and resolved at the architecture level. Their implementation must follow the contracts below; they are not open architecture questions.

| Item | Recorded decision |
|---|---|
| Deployment logging | Bench Deployment owns a dedicated **Bench Deployment Stage** child table. Each stage records its ordered lifecycle position, stage identity, state/status, start and finish timestamps, result/summary, and a link to the relevant Script Execution where applicable. Script Execution and Execution Log retain command-level execution details, exit codes, stdout/stderr and realtime output. Node Action Log remains for general Node/Site operational actions. Deployment history must not be collapsed into the generic Node Action Log. |
| Ingress technology and configuration ownership | Use **Nginx** as the standard Node-level ingress. DT Master owns the desired routing configuration and generates/manages its configuration; Nginx executes it. Infrastructure Node owns the Nginx instance, global configuration, public listeners and TLS/routing infrastructure. Individual Bench Environments must not independently modify Node ingress. The managed routing configuration maps each site's FQDN/site identity to the upstream endpoint of its parent Bench Environment. |
| Runtime upstream endpoint registration | A Bench Environment exposes a registered runtime endpoint. After provisioning and verification, DT Master's runtime adapter obtains and records the endpoint; ingress must not discover endpoints by scanning Docker containers or inspecting arbitrary host processes. An endpoint record includes protocol, address, port, runtime type, network/interface reference where applicable, endpoint purpose (for example web or Socket.IO), and availability state. The registered endpoint is the routing input for the Bench Environment. |
| Node-level port allocation | DT Master owns a Node-level port allocation registry. Allocations are unique within the Node and relevant network namespace, reserved before provisioning, checked against ports already in use, retained while an endpoint remains active (including old/new coexistence during replacement), and released only after the endpoint is retired. Prefer private Docker-network communication where possible; host-port publication is used where the Node ingress or another explicitly defined boundary requires it. |
| TLS ownership and lifecycle | TLS certificate lifecycle is owned by Infrastructure Node because TLS terminates at Node-level ingress. The platform supports automatic certificate issuance, renewal before expiry, certificate-to-FQDN association, renewal status/failure tracking, secure private-key storage and certificate validation before routing activation. Certificate identity follows the Tenant Site FQDN and is not tied to a particular Bench Environment, so moving a site between Benches does not inherently change its certificate identity. |
| Atomic routing update and rollback | Routing changes are part of the Bench Deployment lifecycle. DT Master generates the replacement routing configuration, validates it, stages it, applies it atomically through the ingress mechanism, verifies that ingress serves the intended upstream, and records successful cutover. For Nginx, activation uses configuration validation followed by a controlled reload. If validation or activation fails, the previous active configuration remains authoritative, cutover is not marked successful, and the old Bench remains the active destination throughout the rollback window. |
| Layered health checks and execution ownership | Health assessment covers five layers: **Infrastructure Node, Bench Runtime, Frappe Application, Tenant Site and Node Ingress**. DT Master orchestrates checks, evaluates policy and aggregates results. A **hybrid execution model** is used: a Node-side execution component performs checks requiring local Node/runtime access and reports structured results to DT Master; DT Master remains the control-plane authority and does not need unrestricted direct access to every runtime detail. NDF Deployment Descriptors define the required Bench service topology and applicable service health-check declarations; DT Master executes and evaluates those declarations. DT Master owns Node, runtime-adapter, site and ingress checks. |
| Health-check execution lifecycle | Run health checks in four modes: **(1) pre-deployment** checks before provisioning; **(2) mandatory deployment-stage** checks as Bench Deployment progresses and before traffic activation; **(3) continuous monitoring** at configurable intervals after activation; and **(4) immediate checks** after important lifecycle operations. Readiness checks determine whether a component/deployment may proceed or receive traffic. Liveness checks determine whether an active component remains operational. A process/container being present or running is not, by itself, proof of readiness. |
| Health-check scope and isolation | Infrastructure checks cover Node reachability, runtime availability, capacity and required infrastructure resources. Runtime checks cover the descriptor-required services for the selected Host/Docker adapter. Application checks verify Frappe application behavior, not merely proxy response. Site checks independently verify each Tenant Site's hostname/site resolution, configuration, database connectivity, migration state and safe application request. Ingress checks verify Nginx/configuration validity, upstream reachability, hostname routing and TLS validity. A failure affecting one Tenant Site must not automatically mark sibling sites unhealthy; shared dependency failures should be correlated and identified at their common dependency. |
| Health status and readiness outcome | Store operational health separately from deployment readiness. Operational health supports **Unknown, Healthy, Degraded, Unhealthy and Unreachable**. Deployment readiness supports **Not Assessed, Pending, Passed, Blocked and Waived**; any waiver requires an authorized, auditable exception. Health results are structured and include target/component, layer, check type (readiness/liveness), status, timestamp, duration, machine-readable failure code and a safe diagnostic summary. Parent Node/Bench/Site records may expose the latest aggregated status; individual check executions and results retain history. |
| Health-check failure and recovery policy | A failed readiness check blocks the relevant deployment progression or traffic activation. A liveness failure triggers assessment and the configured recovery policy; it does not automatically cause a restart, redeployment, rollback or destructive action. DT Master separates detection, assessment, decision, authorized recovery execution and post-recovery verification. A replacement Bench that fails readiness remains out of production traffic and the previous active Bench remains available for rollback. |
| Database profiles | Bench Environment declares a database profile: **Dedicated, Shared or External**. The profile determines provisioning and endpoint resolution. Database naming is explicit through the configured strategy (per Site or profile-defined). Shared database infrastructure is permitted only with an explicit isolation boundary and allocation for each Bench Environment; external services are first-class and need not be part of the Bench Compose project. |
| Redis profiles | Bench Environment independently declares a Redis profile: **Dedicated, Shared or External**. Cache and queue endpoints remain separate logical references even when backed by the same Redis service. Shared profiles require explicit per-Bench isolation, including key/namespace and access boundaries appropriate to the selected service. Cache persistence is not assumed; queue persistence and recovery follow the selected service profile. |
| Secret provider and rotation | Introduce a standalone **Runtime Secret** model and provider abstraction. The initial provider may use Frappe's encrypted Password field storage, behind the abstraction so an external secret manager can be introduced later without changing consumers. DocTypes store secret references, never credential values in ordinary Data fields. Secret resolution is restricted to authorized server-side execution paths; values must not enter descriptors, Compose definitions, logs, exports or command-line arguments. Rotation is an explicit lifecycle operation: provision/update the provider value, validate dependent service access, switch the reference/version safely, retain recovery capability for the transition, and record rotation outcome without recording the secret itself. |
| Dependency classification | Classify dependencies by where they are installed and consumed: **(1) Node infrastructure prerequisites** are owned/validated by DT Master against Infrastructure Node capabilities; **(2) image/runtime dependencies** are declared and resolved by NDF during image construction; **(3) external service dependencies** are represented by Bench Environment service/profile references. Bench Runtime Component records exact deployed/observed runtime components. Existing System Dependency records must be classified individually; package-manager type alone does not determine ownership. |
| Existing Host Bench grouping | The actual existing Host Bench directory/runtime is the migration boundary. Create one Host Bench Environment for each distinct existing Bench installation, then associate only the sites whose configuration and filesystem evidence show that they belong to that Bench. A Node may host multiple Bench Environments; do not infer one Bench per Node or group all sites on a Node together. Preserve Host runtime during this migration. |
| Tenant `deployment_type` semantics | Existing `Shared` / `Dedicated` values describe tenant deployment/allocation semantics, not Host versus Docker runtime and not the Node's hosting model. Runtime type is owned by Bench Environment. Preserve legacy values during migration, identify and migrate all consumers, then deprecate and retire the Tenant Site field when it no longer has an authoritative role. Do not silently reinterpret existing values. |
| Frappe schema mechanics | The logical schema is defined in Sections 4–5 and the migration sequence in Sections 14.12–14.13: standard parent DocTypes, child DocTypes with `istable = 1`, explicit field types, Link relationships, generated document names with separate immutable business codes, and server-side validation for uniqueness and relationship constraints. Naming, uniqueness, indexes and schema patches must be implemented through supported Frappe mechanisms and verified against the repository's pinned/supported Frappe version before coding. This is implementation compatibility verification, not an unresolved architecture decision. |

#### Remaining M4.1 implementation contracts

The health-check architecture and operating principles are resolved above. The following are implementation-level specifications, not open architecture decisions:

1. Define the exact health-check result and execution DocType fields, relationships, retention and indexing.
2. Define the Node-side execution component's transport, authentication, authorization, reporting and offline/retry behavior.
3. Define per-product/service health-check declarations in the NDF Product Deployment Descriptor, including supported probe types and safe execution constraints.
4. Define configurable monitoring intervals, timeouts, retry/backoff rules, thresholds and stale-result handling for each check class.
5. Define precise pass/fail criteria for each supported Host and Docker runtime profile and each supported Frappe product.
6. Define which lifecycle operations require immediate checks and the exact deployment-stage gates that consume their results.
7. Define alerting/escalation and authorized recovery policies, including how exceptions/waivers are approved and audited.
8. Verify that diagnostics are sanitized and never disclose credentials, tokens, private configuration or sensitive tenant data.

#### Implementation verification (not open architecture decisions)

The following checks remain necessary before implementation, but do not reopen the decisions above:

1. Verify supported Frappe field options and child-table behavior against the pinned Frappe release.
2. Confirm autoname/naming-series behavior and immutable business-code generation.
3. Implement database uniqueness constraints/indexes using supported Frappe migration mechanisms; validate behavior on the supported database engine.
4. Define ordered, idempotent patches with preflight checks, backups, resumability and post-migration validation.
5. Test migration against representative existing Host Nodes, Bench directories and Tenant Sites before enabling relationship cutover.
6. Verify secret encryption/decryption and permission behavior in the deployed Frappe configuration; do not assume a Password field alone completes the provider/access-control contract.


#### Application descriptor contract — additional constraints

1. `app_id` is allocated/registered through DT Master before the application can participate in the trusted NDF release flow. NDF validates that the descriptor uses the registered identity; it must not silently create a second identity from a repository or package name.
2. The publisher shown in marketplace listings is verified and governed by DT Master. A descriptor may identify the claimed publisher, but that claim is not proof of publisher ownership.
3. Developer registration information and technical release information remain separate. Display name and listing content originate from developer registration and are governed by DT Master; technical source, immutable commit, compatibility, dependencies and release provenance originate from the NDF descriptor.
4. Each release descriptor is immutable once published. A correction produces a new descriptor artifact/revision with explicit provenance; it must not rewrite the meaning of an already-consumed release.
5. Release notifications are authenticated, replay-safe and idempotent. DT Master verifies descriptor integrity and immutable source provenance before synchronizing records. Notification delivery is not itself marketplace approval.
6. DT Master retains the immutable descriptor reference/digest and synchronization outcome on the corresponding Extension Version for auditability.
7. Product Inclusion Requests identify both the requested semantic release and immutable commit/artifact. NDF validates that they correspond; it must not resolve a moving branch at product-build time.
8. The existing NDF resolver/lock format remains the resolved build input during migration. Descriptor-backed composition support must preserve deterministic locks, immutable commit pinning and reproducible product builds.

#### Additional existing-schema observations

- `Tenant Site.port` is currently a Data field. Preserve values during migration; do not silently coerce.
- `Extension.supports_upgrade` is currently Data with a default of 1, unlike Check field `supports_uninstall`; review its type during schema work.
- `Script Execution.triggere_source` is the existing fieldname. Correct only through a controlled migration.
- `Infrastructure Node.state` currently includes Bench-oriented states such as Project Installed, Initialized, Healthy, Running and Stopped. Separate these from Node readiness.
- `Tenant Site.region` is free text, while `Infrastructure Node.region` is a Link. Reconcile before making Site region derived.

**Approval boundary:** The application identity, descriptor ownership, release synchronization, product inclusion governance, deployment logging, database/Redis profiles, secret-provider model, dependency classification, Host Bench grouping, `deployment_type` semantics, logical Frappe schema mechanics, Node-level Nginx ingress ownership, runtime endpoint registration, Node-level port allocation, Node-owned TLS lifecycle, atomic routing/cutover behavior and layered health-check architecture recorded in this section are agreed architecture decisions. Health-check field-level schema, agent transport, exact probes, intervals, thresholds, stage gates and recovery policies remain implementation-level contracts to specify; they do not reopen the agreed ownership, execution, cadence or readiness/liveness principles. The implementation verification checks above also remain. This documentation update does not authorize implementation. Explicit implementation approval is still required before DocType/controller/API/migration/runtime changes begin.



## 15. Cross-repository security and authorization contract

**Status:** M4.1 contract proposal for architecture review. Source-level findings
are recorded in the API caller, UI/role and security verification audits.
This section defines the target authorization boundary; it does not create
Frappe Roles, service users, secrets, endpoints or implementation changes.

### 15.1 Governing rules

1. **Authenticate the principal, then derive the tenant.** A request-body FQDN,
   Tenant Site name, customer, subscription, invoice or Node identifier is not
   proof of identity or ownership.
2. **Separate human actors from service identities.** A tenant service user is
   not a Tenant Manager, System Manager or platform operator.
3. **Default deny.** Every whitelisted method must enforce an explicit
   actor/scope and resource-ownership decision at the server boundary.
4. **Authorize before bypassing Frappe permissions.** Any
   ignore_permissions=True operation requires a preceding explicit
   authorization decision, validated parent/tenant relationship and audit
   record. UI visibility is never authorization.
5. **Preserve the ownership chain.** Tenant Site → Bench Environment →
   Infrastructure Node is authoritative. Site operations derive Bench and
   Node from the parent relationship; they do not trust caller-supplied Node
   links.
6. **Use durable operation identity.** Async workers and callbacks carry the
   accepted actor, tenant, target, action and operation ID. Worker session user
   is not assumed to be the initiating user.
7. **Keep secrets out of ordinary data and diagnostics.** Secrets are resolved
   only inside authorized server-side execution and are never serialized into
   descriptors, command arguments, realtime payloads, logs or ordinary API
   responses.

### 15.2 Actor classes and responsibility boundaries

These are logical authorization classes, not a requirement to introduce a separate OAuth server or one Frappe Role per scope. Concrete role fixtures and endpoint enforcement are implementation work assigned to the relevant M4 milestones.

| Actor class | Permitted responsibility | Explicitly excluded |
|---|---|---|
| Platform Operator | Infrastructure Node, Bench Environment, Tenant Site provisioning/lifecycle, deployment, ingress, backup/restore and platform recovery | Tenant billing/customer actions unless separately granted |
| Platform Marketplace Operator | Publisher/listing governance, Extension/Extension Version approval and Product Inclusion Request governance | Node/Bench administration unless separately granted |
| Tenant Service Identity — tenant to Master | Authenticated API calls for one bound Tenant Site and explicitly granted scopes | Desk use, arbitrary tenant selection, System Manager and platform operations |
| Tenant Callback Identity — Master to tenant | Receive validated Master callbacks for one tenant site and one outstanding operation | General tenant administration, user management and arbitrary realtime publication |
| Tenant Manager | Tenant users, tenant role profiles, capability-constrained role/module selection, subscription and billing workflows, and authorized marketplace requests | Infrastructure Node/Bench management, secret retrieval and platform administration |
| Tenant User | Read-only access to permitted marketplace listings and available/installed application information | Application lifecycle mutations, tenant settings/secrets, user/role administration, billing and infrastructure operations |
| Read-only/Support | Explicitly scoped operational or tenant information required for support | Write/execute, unrestricted export, secret retrieval or cross-tenant access |
| System Manager | Platform administration and audited break-glass recovery | Routine tenant service authentication or a shared tenant integration identity |

System Manager must not be assigned to either tenant service identity. Tenant
roles and capability profiles must not be able to grant platform operator or
marketplace governance privileges.

### 15.3 Operation authorization matrix

| Operation | Human actor | Service principal / execution identity | Resource boundary |
|---|---|---|---|
| Node provisioning/maintenance | Platform Operator | Authorized Node execution component | One Infrastructure Node |
| Bench create/update/deploy/replace/rollback | Platform Operator | DT Master operation worker and selected runtime adapter | One Bench Environment and its Node |
| Tenant Site create/suspend/resume/destroy | Platform Operator or authorized provisioning operator | DT Master operation worker | One Tenant Site, resolved through its Bench |
| Marketplace browse/listing read | Tenant Manager and Tenant User | Tenant-to-Master identity with marketplace.read | Only the bound Tenant Site's eligible catalogue view |
| App install/upgrade/uninstall request | Tenant Manager; Tenant User read-only | Tenant-to-Master identity with the applicable application-operation scope; Master independently validates authority and Bench availability | One Tenant Site; app must be available in its parent Bench Environment |
| App downgrade/reinstall | Tenant Manager with explicit confirmation; Platform Operator | Tenant-to-Master identity with app.downgrade or app.reinstall; Master validates request and records confirmation/audit | One Tenant Site; operation cannot change Bench inventory or product image |
| Bench application inventory add/remove | Platform Operator only | Platform service/operation identity | One Bench Environment; removal requires dependency and tenant-usage checks and does not silently uninstall from sites |
| App installation execution | No direct human execution | Authorized DT Master worker after request validation | One accepted App Installation Request and target Site |
| Extension/listing/release approval | Platform Marketplace Operator | Authenticated NDF release-notification identity for technical metadata only | One registered app/release |
| Product inclusion approval | Authorized product maintainer / Platform Marketplace Operator | Authenticated DT Master-to-NDF request identity | One product composition and immutable app release |
| Subscription and billing read | Tenant Manager | Tenant-to-Master identity with subscription.read / billing.read | Customer/subscription derived from bound Tenant Site |
| Plan change, payment and payment-method mutation | Tenant Manager | Tenant-to-Master identity with subscription.change, billing.pay or billing.payment_method.manage scope | Invoice/customer/subscription ownership independently verified by Master |
| Capability fetch | No direct tenant-user execution | Tenant-to-Master identity with capability.read | One bound Tenant Site |
| Capability synchronization and role/module reconciliation | Authorized platform/bootstrap operator or dedicated local scheduler/service identity | Serialized DT Tenant capability-sync operation | One tenant site; only Master-approved roles/modules |
| Tenant user/role administration | Tenant Manager | Tenant app server-side controller | Users and role/module assignments within the tenant capability allowlist |
| Tenant Settings ordinary preferences | Tenant Manager, restricted to an explicit allowlist | Tenant application server | One tenant site; cannot alter Master trust endpoint or service credentials |
| Service credential bootstrap/rotation | Authorized provisioning/platform security operator | Dedicated bootstrap/rotation identity | One Tenant Site and one credential direction |
| Mail Server operations | Explicitly authorized platform mail operator | Mail operation worker | One Mail Server and its Node; remains separate from Bench Environment |
| Secret retrieval | No ordinary human or tenant actor | Authorized execution boundary only | One secret reference and one authorized operation |

**Accepted application policy:** Tenant User may view available and installed applications but cannot request or execute install, upgrade, downgrade, reinstall or uninstall operations. Tenant Manager may install, upgrade and uninstall applications on their own site; downgrade and reinstall require explicit confirmation and audit logging. Tenant Managers cannot change Bench Environment inventory, product images or runtime dependencies. Platform Operators exclusively control those Bench-level operations. Subscription and billing functions remain Tenant Manager-only pending any separate billing-policy decision. Enforce these rules at API boundaries as well as in page/workspace visibility.

### 15.4 Directional service identities

The two integration directions use separate principals and credentials.

**A. DT Tenant → DT Master**

- Each Tenant Site has one dedicated Tenant-to-Master service principal on
  DT Master.
- The principal maps server-side to exactly one Tenant Site.
- Its permissions are limited to the scopes granted in §15.5.
- It is not a shared System Manager user and has no Node/Bench permissions.
- DT Tenant retains the existing Frappe API key/secret mechanism for this direction. Credentials must be tenant-specific, least-privilege, protected in Frappe password fields or an approved secrets manager, rotatable and revocable; they must not be exposed through ordinary Tenant Settings Data fields or API responses.
- OAuth2 client credentials are not introduced for the initial integration; revisit only if a demonstrated requirement exceeds the existing mechanism.
- The Master URL is a trusted destination setting; changing it requires an
  authorized bootstrap/platform operation, not ordinary Tenant Manager access.

**B. DT Master → DT Tenant**

- Each Tenant Site has a separate Tenant Callback Identity on that tenant site.
- Its credential is stored on Master as a protected secret reference. Retain the existing Frappe API key/secret mechanism initially, with tenant-specific permissions, rotation and revocation; do not reuse the tenant-to-Master credential.
- It can invoke only the callback contract required for operation status and
  other explicitly approved Master-to-Tenant notifications.
- It cannot act as a tenant administrator or use a broad System Manager account.
- The callback validates Master identity, target site, operation ID, expected
  state transition, payload schema and replay/idempotency state before
  publishing a notification.

**C. Bootstrap and rotation**

- Initial provisioning uses a narrowly scoped bootstrap identity/credential.
- Bootstrap creates or binds the two directional principals, stores protected
  references, verifies both directions and rotates/revokes bootstrap material.
- Bootstrap credentials are not retained as permanent runtime credentials.
- Credential rotation is explicit, audited, reversible during the transition
  and does not expose secret values.

### 15.5 API scopes and tenant binding

Initial logical scope vocabulary (enforced through Frappe roles, endpoint checks and tenant-specific service identities; these names do not require a separate OAuth authorization server):

| Scope | Purpose |
|---|---|
| marketplace.read | Read eligible marketplace listings and approved release metadata |
| app.install | Install an app on the bound Tenant Site, if available in its Bench Environment |
| app.upgrade | Upgrade an app on the bound Tenant Site |
| app.uninstall | Uninstall an app from the bound Tenant Site without removing Bench availability |
| app.downgrade | Downgrade an app on the bound Tenant Site, subject to Tenant Manager confirmation |
| app.reinstall | Reinstall an app on the bound Tenant Site, subject to Tenant Manager confirmation |
| app.request | Submit an application lifecycle request for the bound Tenant Site |
| subscription.read | Read the bound tenant's subscription and plan information |
| subscription.change | Request a plan change for the bound tenant, subject to Master-side eligibility and approval policy |
| billing.read | Read billing history and invoices belonging to the bound tenant |
| billing.pay | Initiate an authorized payment for an owned invoice |
| billing.payment_method.manage | Create/list/manage payment methods for the bound tenant, subject to gateway policy |
| capability.read | Retrieve the capability snapshot for the bound Tenant Site |
| tenant.callback | Publish a validated callback for one outstanding Master operation on the bound tenant |
| tenant.bootstrap | Complete restricted initial registration and credential rotation |
| platform.node / platform.bench / platform.site | Platform-internal scopes; never granted to a tenant service identity |

Master resolves the authenticated API principal to its Tenant Site mapping
before executing a method. It then verifies that the Site is active/eligible,
derives Customer and Subscription from that Site, derives Bench Environment
and Node through the authoritative parent relationship, and checks the
requested scope and resource ownership.

A caller-supplied FQDN may be used as a consistency assertion during migration,
but it is not the tenant identity. A caller may not select another Tenant Site,
customer, subscription, invoice, Bench or Node by changing request arguments.

For child records, the API loads and authorizes the parent first, then resolves
the child row from that parent's child table. A child-row name paired with a
different parent is rejected.

### 15.6 Request, operation and callback envelope

Every mutating cross-repository request must carry or resolve to:

- request_id and idempotency_key;
- authenticated actor class and principal identity;
- bound Tenant Site identity where tenant-scoped;
- operation/action and target DocType/document;
- requested authorization scope;
- schema/contract version and request timestamp;
- safe result/error code and operation state.

Master creates the durable operation/request record before queueing work. Each mutating operation has a unique operation ID and records tenant ID, Bench Environment ID, application, operation, requesting user, initiating service identity, status (Pending/Running/Completed/Failed), start/completion timestamps and sanitized error information. Duplicate idempotency keys return the existing operation result or a defined conflict; they must not schedule duplicate mutations. Retry and reconciliation must be controlled and must not repeat a destructive action blindly.

The initiating human actor is captured at request acceptance and explicitly
propagated to the worker. Scheduled jobs use a distinct service/system actor.
A worker's current frappe.session.user is not treated as proof of the
initiating actor.

Callbacks are authenticated, bound to an outstanding operation, validated
against the expected state transition and replay-safe. They carry operation
identity and safe status only; raw tracebacks, shell output and secret values
are excluded.

### 15.7 Realtime authorization

- Realtime publication is performed only after server-side authorization.
- Execution events carry operation ID, tenant/site, target and safe status.
- Recipient selection is explicit and derived from the persisted authorized
  actor/tenant context, not from an unverified worker session.
- Tenant callbacks do not broadcast unrestricted site-wide status/error
  payloads.
- Browser-side filtering by site, extension or row is presentation only and
  never the access-control boundary.
- Client listeners unsubscribe only their own callback.
- Diagnostics are sanitized before persistence and publication.

### 15.8 Secret and diagnostic boundary

- Replace ordinary Tenant Site and Tenant Settings credential fields with
  protected Runtime Secret/provider references.
- Keep tenant-to-Master credentials, Master-to-Tenant callback credentials,
  bootstrap credentials, Frappe Administrator/bootstrap credentials, database
  administrator credentials and mailbox passwords as separate trust domains.
- Resolve secret material only inside the authorized server-side execution
  adapter. Do not put secrets in command-line arguments, serialized SSH
  command strings, Compose descriptors, deployment metadata, realtime events,
  ordinary API responses or logs.
- Replace the broad frappe NOPASSWD:ALL sudo grant with narrowly scoped
  privileged operations.
- Do not execute arbitrary dependency URLs through curl piped to bash; Node
  prerequisites require allowlisted, immutable and integrity-verified sources.
- Redact before persistence, not only before browser publication. Error logs,
  child logs, mail logs, exports and backups are treated as potentially
  sensitive.
- Backups containing site_config.json, database contents or private files
  require encryption, access control, retrieval audit and defined retention.

### 15.9 Enforcement and recovery rules

- The API authorization matrix is authoritative; UI/workspace visibility is
  supplementary.
- Tenant Manager role/profile assignments are intersected with the Master
  capability allowlist and must also honor the enabled state of Tenant Roles
  and Tenant Modules.
- Platform roles are never assignable through tenant role/profile controls.
- Subscription lifecycle transitions modify only accounts/roles that the
  lifecycle operation itself changed. Recovery must not re-enable users
  disabled for unrelated security or administrative reasons.
- Subscription request gating is not considered active until its hook and
  policy are explicitly enabled and verified.
- Failed authorization, invalid parent/child relationships, stale callbacks
  and replayed requests fail closed and create safe audit records.

### 15.10 Accepted decisions and remaining policy boundary

The following cross-repository authorization decisions are accepted for M4.1:

1. Platform, tenant and service identities are distinct. Every tenant service identity is bound to one registered Tenant Site and its current Bench Environment; FQDN alone is never proof of identity.
2. Retain Frappe API key/secret authentication for both communication directions using separate, tenant-specific service credentials. Strengthen least-privilege permissions, protected storage, rotation and revocation. OAuth2 client credentials are not required at this stage.
3. DT Tenant validates the local human actor and tenant role. DT Master independently validates the authenticated service identity, tenant binding/status, subscription eligibility, application availability and requested operation. Caller-supplied actor claims or FQDN do not establish authority.
4. Tenant Users are read-only for application inventory. Tenant Managers may install, upgrade and uninstall applications on their own sites. Downgrade and reinstall require explicit confirmation and audit logging. Platform Operators exclusively control Bench Environment application inventory, product images and runtime dependencies.
5. Site-level uninstall and Bench-level removal are distinct. Bench-level removal is platform-only and requires dependency/tenant-usage checks; it must not silently uninstall the application from existing sites.
6. Every mutation has a durable operation ID, audit record, idempotency behavior and controlled failure recovery. Realtime events are tenant-scoped notifications, not authorization grants.
7. Tenant activation requires successful site binding, service authentication, settings/capability setup and administrator-access verification.

These decisions resolve the policy questions previously listed for actor separation, Tenant User application permissions and service authentication. Billing/payment-method policy remains subject to the existing billing authorization rules and is not expanded by this approval. Concrete Frappe roles, endpoint checks, credential migration, runtime tests and secret-provider implementation remain implementation work in their assigned M4 milestones.

**Approval boundary:** The Section 15 architecture and authorization policy is accepted. This does not authorize application code, DocType, permission, credential, migration or runtime changes; those remain subject to the existing M4.2–M4.7 implementation gates and explicit implementation authorization.


## 16. NDF integration contract — verified source state and target acceptance

**Status:** Contract requirements are defined below; current NDF implementation
gaps are recorded from a read-only audit of NdfTechLabs/frappe_docker:main.
M4.1 remains in progress. This section does not authorize NDF code changes.

### 16.1 Artifact ownership and separation

The integration uses four distinct artifact/request types:

| Artifact | Owner | Meaning |
|---|---|---|
| Marketplace App Descriptor | NDF publishes; DT Master assigns/validates app identity and governs publisher/listing | Immutable technical description of one application release |
| Product Composition Manifest | NDF | Product-selected set of app releases and optional capabilities |
| Deployment Candidate | DT Master | Requested complete Bench composition and supported runtime/deployment parameters |
| Product Deployment Descriptor | NDF | Verified description of the exact image and runtime/service composition actually built |

The existing application-manifest-v1 remains the composition-manifest/lock input during
migration. It is not the per-app Marketplace App Descriptor and must not be
replaced by a second competing resolver. Extend the existing resolver and
lock model to consume registered app identities and immutable release
descriptors.

### 16.2 Marketplace App Descriptor

Each immutable application release descriptor must contain, at minimum:

- Stable app_id assigned by DT Master during application registration.
- Release version and descriptor schema version.
- Repository identity and immutable source commit SHA.
- Framework compatibility range, including Frappe and relevant ERPNext
  compatibility.
- Python, Node.js and package-manager compatibility ranges.
- Declared Bench app dependencies and dependency constraints.
- OS/system package requirements, classified as image-build dependencies.
- Build/install metadata required to include the app in a Bench image.
- Optional external service requirements and declared runtime capabilities.
- Validation evidence/status and descriptor integrity digest.

The descriptor does not own publisher verification, marketplace visibility,
pricing or commercial approval. Those remain DT Master responsibilities.
A moving branch/tag may be used during developer preparation, but a
published descriptor must identify an immutable commit.

### 16.3 Product Composition Manifest and resolution

A product composition selects app_id plus release version/descriptor digest,
not an unverified repository branch. It records required versus optional
applications, selected capabilities and product-level compatibility policy.

NDF resolves the entire composition as one compatible Bench environment:

1. Validate app identities and immutable descriptor references.
2. Intersect all required Frappe/ERPNext, Python, Node.js and package-manager
   compatibility ranges.
3. Resolve exact runtime versions and image/base-image identities.
4. Resolve Python, Node.js and OS dependencies using their native package
   managers and deterministic lockfiles.
5. Validate the application dependency graph and installation order.
6. Reject missing dependencies, incompatible required applications,
   unverified required releases or unsupported service requirements.
7. Produce a deterministic composition lock with source descriptor digests,
   immutable app commits, exact runtime versions and dependency lock hashes.

An empty compatibility intersection or a required dependency failure blocks
the candidate. Optional applications are included only when explicitly
selected and validated; optional does not mean silently install every
available app.

### 16.4 Deployment Candidate input

DT Master owns the Deployment Candidate. Its immutable build input identifies:

- candidate ID and request/schema version;
- target product and product composition;
- selected app_id/release/descriptor digests;
- requested optional capabilities;
- supported runtime constraints or explicitly selected runtime profile;
- approved build parameters and platform targets;
- composition hash and source/provenance references.

A candidate contains no tenant credentials, database passwords, API secrets,
customer data or arbitrary shell commands. Node-specific ports, site FQDNs,
TLS private keys and live tenant configuration are not part of the product
image build input.

### 16.5 Product Deployment Descriptor

NDF publishes a versioned descriptor bound to the exact OCI image digest.
It describes the artifact actually built, including:

- product ID/version, image repository, immutable image digest and platforms;
- base image identities/digests and build provenance;
- framework and exact Python/Node.js/package-manager versions;
- full app inventory: app_id, release, descriptor digest, repository and
  verified commit SHA;
- composition lock, dependency lock and manifest checksums;
- Bench service topology, dependencies, internal ports/protocols and
  supported deployment parameters;
- service readiness/liveness declarations and supported probe types;
- required persistent paths, volume semantics and storage requirements;
- database/Redis service requirements and supported external-service modes;
- internal endpoint purposes and resource/capacity requirements;
- initialization, migration, backup/restore and recovery requirements;
- source commit, workflow run/build ID, composition hash and provenance.

The descriptor contains no secret values. It may declare secret names or
required secret references, but values are supplied at deployment time by
DT Master through the approved secret provider.

### 16.6 Compose and runtime ownership boundary

NDF owns the Bench service definitions and the versioned service topology
required by the product image. It declares supported service roles, internal
ports, health probes, dependencies, resource requests, persistent paths and
supported configuration parameters.

DT Master owns deployment to a specific Infrastructure Node and Bench
Environment: Compose project identity, Node network/port allocation,
storage backend and volume lifecycle, database/Redis profile allocation,
external endpoint binding, secret injection, site membership, Node ingress,
TLS, FQDN routing, deployment sequencing, health aggregation and rollback.

DT Master may override only parameters explicitly marked configurable in the
NDF descriptor. It must not rewrite NDF service definitions or maintain a
competing product Compose model. NDF does not own Node-level Nginx ingress or
customer-specific site routing.

The existing upstream compose.yaml and overrides remain the upstream runtime
foundation. NDF-specific product/service contracts are layered and versioned
separately; do not fork or duplicate upstream Compose behavior unnecessarily.

### 16.7 Asynchronous build request/result contract

1. DT Master creates and validates a Deployment Candidate.
2. DT Master submits a candidate ID plus immutable candidate artifact reference
   to an authorized NDF GitHub Actions workflow.
3. NDF validates identity, composition, runtime compatibility, dependencies
   and source provenance.
4. NDF resolves the exact build lock, builds the image, validates installed
   commit identities and product smoke requirements, and publishes the image.
5. NDF generates and publishes the Product Deployment Descriptor associated
   with that image digest.
6. DT Master retrieves and verifies the descriptor, image digest, descriptor
   digest, lock hash and composition hash before marking the candidate ready.

The result contract returns candidate/request ID, GitHub workflow run ID,
status, image repository/digest, descriptor artifact reference/digest,
composition hash, lock hash, build provenance and a safe failure code/summary.
Build execution is asynchronous; DT Master does not hold an HTTP request open
while NDF builds.

Initially DT Master may poll GitHub workflow status/artifacts. A later
authenticated callback can notify completion, but polling and callback must
share the same idempotent result contract. NDF retains GHCR write credentials;
DT Master receives no unrestricted publishing credential. A GitHub App
installation token with narrowly scoped Actions permissions is preferred for
workflow invocation.

### 16.8 Product inclusion and release synchronization

Marketplace release approval and product inclusion remain separate decisions.

- NDF publishes an immutable Marketplace App Descriptor and notifies DT Master.
- DT Master verifies app_id, release, descriptor integrity and source provenance,
  synchronizes the technical metadata snapshot and places the release into
  Pending Review.
- DT Master marketplace governance approves or rejects listing/release.
- A separate Product Inclusion Request identifies product, app_id, release
  and immutable descriptor/commit.
- NDF validates the request and proposes a product-manifest change through a
  pull request. An authorized product maintainer approves/merges it.
- NDF builds and publishes the new immutable product image.
- Publishing an app release or approving marketplace listing does not
  automatically upgrade existing Bench Environments or authorize product
  inclusion.

### 16.9 Verified NDF implementation gaps requiring M4.2/M4.4 work

The current NDF main-branch audit found:

1. The resolver records immutable app commit SHAs in lock files, but
   prepare_build.py writes apps.json using target_ref as the Bench branch.
   build_image.py also passes framework.target_ref as FRAPPE_BRANCH.
   The Containerfile installs from those refs. Therefore the resolved SHA is
   not currently guaranteed to be the source actually built.
2. validate_image.py checks application directories, sites/apps.txt and
   metadata labels, but does not inspect each installed Git HEAD against the
   locked SHA. OCI labels can therefore claim a resolved SHA without proving
   that the image contains that commit.
3. The build and base stages use frappe/build and frappe/base tags derived
   from FRAPPE_BRANCH rather than immutable base-image digests.
4. Manifest fields such as allow_development_branches and
   fail_on_unverified_compatibility are schema-accepted, but the resolver does
   not enforce those policies. validation.required_checks are declarative
   entries; the current validator does not execute them as product acceptance
   tests. The dhana-base manifest includes telephony target_ref=develop while
   declaring allow_development_branches=false; compatibility_status values
   remain pending in multiple product manifests.
5. application-manifest-v1 does not define the per-app app_id/release
   descriptor, compatibility ranges or Python/Node/OS dependency contract.
6. The ai-automation manifest declares a services array, but the resolver
   composes only applications. The current image build pipeline does not
   build or publish those independent service images from that declaration.
7. No Product Deployment Descriptor schema/generator, candidate input
   workflow, descriptor publication, Product Inclusion Request workflow or
   authenticated DT Master build-result notification was found in the
   inspected NDF tree.
8. The current build workflow selects a fixed list of products. Publishing is
   manual and product-list based; the release workflow promotes only the
   DhanaERP Master image.
9. images/ndf/.build/dalali-v16 contains generated apps.json and
   build-metadata.json in the repository tree. Generated build output should
   be ephemeral and ignored; committed deterministic lock files remain the
   build inputs.

These are source-level findings, not evidence of a production compromise.
They are blockers to claiming that the current NDF pipeline already provides
the complete immutable deployment contract. They do not invalidate the
existing resolver/lock architecture or the published image digest itself;
they mean the relationship between the lock and actual image contents must
be fixed and verified before DT Master trusts it.

### 16.10 Milestone placement

| Work | Milestone |
|---|---|
| Approve descriptor schemas, candidate/result contract, immutability and ownership boundaries | M4.1 |
| Implement Marketplace App Descriptor schema/registry, runtime and dependency resolution, SHA-enforced build inputs, base-image pinning, service definitions and Product Deployment Descriptor generation | M4.2 |
| Implement DT Master Host/Docker adapters and Node/Bench orchestration consuming the versioned NDF runtime contract | M4.3 |
| Implement candidate submission, asynchronous build tracking, descriptor/digest verification and replacement Bench deployment | M4.4 |
| Implement marketplace release synchronization, app availability and Product Inclusion Request lifecycle | M4.5 |
| Integrate Tenant Site/DT Tenant lifecycle and service identities | M4.6 |
| Perform end-to-end compatibility, migration, permission, security, rollback and production-readiness testing | M4.7 |

**Approval boundary:** The artifact separation, ownership, SHA-enforcement
requirement, candidate/result exchange, Compose boundary, descriptor content,
release/product approval separation and milestone placement above are the
proposed M4.1 NDF integration contract. Explicit review is required before
marking it resolved. All implementation fixes remain in their assigned
milestones; no NDF or DT Master code changes are authorized by this record.


## 17. M4.1 architecture review and exit assessment

**Review status:** Prepared for owner review; M4.1 is not yet closed.
**Implementation authorization:** Not granted.

### 17.1 Resolved architecture decisions

The following architecture decisions are recorded as resolved in Sections
1–14 and must remain binding:

- Infrastructure Node → Bench Environment → Tenant Site ownership hierarchy.
- A Node may host multiple Bench Environments; a Bench Environment may host
  multiple Tenant Sites.
- Bench Environment owns runtime type, product identity, image/digest,
  application inventory, runtime components, storage/service references,
  site membership and deployment state.
- Bench Environment Application is the Bench-level availability inventory;
  Tenant Installed App remains site-level installation state.
- Host-native and Docker-backed Bench Environments coexist during migration.
- Existing Host migration follows actual Bench directory/runtime grouping;
  preserve the user's confirmed initial one-Bench-per-existing-Node baseline
  only where filesystem/site evidence confirms it.
- Shared Bench composition changes use immutable replacement Bench
  deployments; all member sites move together and the prior Bench remains
  available for rollback.
- NDF owns product composition, dependency resolution, image construction,
  validation, immutable publishing and deployment descriptors.
- DT Master owns Node/Bench/Site orchestration, runtime adapters, ingress,
  deployment state, migration and recovery coordination.
- DT Tenant is the tenant-facing authenticated service client; it does not
  orchestrate Node or Docker.
- Nginx is Node-level ingress; DT Master owns desired routing configuration,
  endpoint registration, port allocation, TLS lifecycle and atomic cutover.
- Health checks use five layers and hybrid Node-side/control-plane execution,
  with readiness separated from liveness and operational health separated
  from deployment readiness.
- Database and Redis profiles are independently Dedicated, Shared or External.
- Runtime Secret/provider abstraction replaces ordinary credential fields.
- Dependencies are classified as Node prerequisites, NDF image/runtime
  dependencies or Bench external-service dependencies.
- Marketplace app identity/release, product composition and product deployment
  are separate artifacts and approval processes.

### 17.2 Contract approval and remaining review

| Contract | Current record | Status / remaining closure requirement |
|---|---|---|
| Cross-repository security/authorization | Section 15 | **Policy accepted.** Reconcile documentation across both repositories. Concrete Frappe roles, endpoint enforcement, credential migration, operation implementation and runtime verification remain assigned implementation work. |
| NDF integration/artifact contract | Section 16 | **Review/approval pending.** Approve Marketplace App Descriptor, Product Composition Manifest, Deployment Candidate, Product Deployment Descriptor, build/result and Compose/runtime ownership. |
| Repository dispositions | M4_FILE_DISPOSITION_MATRIX.md | **Source dispositions reviewed and consolidated.** External/private/deployed callers cannot be ruled out from the two accessible repository trees; retain this verification limitation. |
| Migration and rollback | Sections 14.12–14.13 and the replacement migration contract in Section 14.14 | **Policy accepted.** Isolated database/file copies are mandatory for replacement preparation; write-freeze and final synchronization precede cutover; pre-commit rollback preserves the original Bench; post-commit return requires separately validated recovery/reconciliation. Implementation details remain assigned to M4.2–M4.7. |

### 17.3 Remaining implementation dependencies by milestone

| Milestone | Dependencies that must be implemented there |
|---|---|
| M4.2 — Runtime Infrastructure Enhancements (frappe_docker) | Marketplace App Descriptor and schema; app identity/release references; runtime and Python/Node/OS dependency resolution; deterministic lock extension; enforcement of compatibility/development-ref policy; SHA-enforced app and Frappe builds; immutable base image references; service topology; Product Deployment Descriptor; candidate-driven NDF build/publish/result workflow. |
| M4.3 — Docker Orchestration Engine (dt_master) | Node Docker readiness/capacity; least-privilege Docker execution; Host/Docker runtime adapters; Bench lifecycle ownership; durable operation coordinator, idempotency/leases/timeouts; endpoint registration; Node port allocation; Nginx ingress manager; protected secret resolution. |
| M4.4 — Product Image Deployment (dt_master) | Deployment Candidate and Deployment Build lifecycle; GitHub Actions request/poll/result; descriptor and digest verification; product capacity checks; replacement Bench provisioning; deployment stages/readiness gates; atomic traffic cutover and rollback. |
| M4.5 — Runtime Application Management (Both) | NDF descriptor synchronization; Extension/Extension Version provenance; Bench app availability; App Installation Request; Product Inclusion Request; no in-place shared Bench code mutation; site-level install only after Bench availability validation. |
| M4.6 — Tenant Site and DT Tenant Integration (Both + dt_tenant) | Tenant Site → Bench Environment migration; separate directional service identities; tenant-bound API authorization; Tenant Settings and Tenant Site secret migration; Tenant Manager/User policy; capability sync/lifecycle correction; site lifecycle and callback integration. |
| M4.7 — Integration Testing and Production Readiness | Migration rehearsal; Host/Docker and multi-Bench/multi-Site tests; authorization/negative tests; worker/realtime verification; actual installed-commit and descriptor verification; backup/restore; concurrency; observability; failure recovery; rollback and production acceptance. |

### 17.4 Migration sequence and safeguards

The approved logical sequence remains:

1. Inventory Nodes, actual Host Bench directories, Tenant Sites, dependencies,
   service identities and stored credentials.
2. Classify Node-owned versus Bench-owned paths, ports, services and logs.
3. Create one Host Bench Environment for each verified existing Bench.
4. Populate Bench runtime, configuration, service, storage and app inventory.
5. Link each Tenant Site to its verified Bench Environment and prove that the
   derived Node matches its legacy Node.
6. Reconcile framework/app versions and site database metadata.
7. Reconcile Bench Environment Application inventory against actual Bench
   source and site-level Tenant Installed App state.
8. Classify dependencies and external service ownership.
9. Extend framework and marketplace release metadata with immutable provenance.
10. Migrate credentials into protected secret references and verify/rotate
    without duplicating plaintext.
11. Preserve/reconcile execution, action, deployment and audit history.
12. Switch application reads and operations to the new ownership model only
    after validation.
13. Run representative Host migration and recovery rehearsals.
14. Retire legacy direct Node ownership and Bench actions only after all
    consumers are migrated and compatibility observation passes.

For replacement Bench deployment, prepare isolated database and site-file
copies and run migrations against the copies, not the active source. Freeze
source writes and perform validated final synchronization before cutover.
Verify every affected site through the replacement runtime and ingress while
writes remain frozen. If pre-commit verification fails, route back to the
original Bench, which remains authoritative.

The migration commit point is the authorization to enable production writes
on the replacement after successful cutover verification. Before that point,
the original Bench remains the rollback target. After that point, returning to
the old Bench requires a separately validated recovery/reconciliation procedure
that accounts for replacement-side writes; routing reversal alone is unsafe.
Block automatic rollback whenever data consistency cannot be demonstrated.

No site regrouping, Host-to-Docker conversion, data deletion, credential
plaintext duplication, broad Docker pruning or production cutover is implied.
Retain the original Bench, source data and recovery material for the configured
rollback window. Record backup, synchronization, write-freeze, cutover,
verification and migration-commit evidence in deployment history.

### 17.5 M4.1 acceptance criteria

M4.1 can be marked complete only when all criteria below are satisfied:

- [ ] The exact M4.0–M4.7 milestone structure is used consistently across the
      tracker and audit records.
- [ ] Node → Bench Environment → Tenant Site ownership and all child-table
      boundaries are approved.
- [ ] The schema and field migration matrix is reviewed and accepted.
- [ ] Host/Docker runtime ownership and adapter contracts are approved.
- [ ] Deployment Candidate, Build, Bench Deployment and deployment-stage
      lifecycle contracts are approved.
- [x] The Section 15 security/authorization policy decisions are approved; documentation reconciliation is required, while implementation and runtime verification remain assigned to later milestones.
- [ ] The Section 16 NDF artifact/build/deployment contract is approved.
- [ ] The NDF lock-to-actual-build SHA mismatch is explicitly assigned as an
      M4.2 blocker, not treated as an already-satisfied immutable build.
- [ ] The cross-repository caller/disposition audit is reviewed, with external
      caller limitations recorded.
- [x] The ordered data migration, backup, resumability and rollback policy is accepted; isolated replacement copies and the pre-/post-commit rollback boundary are recorded. Implementation and rehearsal remain assigned to M4.2–M4.7.
- [ ] Every remaining implementation dependency is assigned to M4.2–M4.7.
- [ ] Explicit authorization is obtained before implementation starts.

**Current assessment:** The architecture and source audits are substantially complete. The Section 15 cross-repository authorization policy decisions have been accepted and are being reconciled across repository documentation. Section 16 NDF integration/artifact approval, final migration/rollback review, remaining external caller verification and final owner architecture acceptance remain outstanding. Therefore M4.1 remains **In progress**. M4.2 has not started.

