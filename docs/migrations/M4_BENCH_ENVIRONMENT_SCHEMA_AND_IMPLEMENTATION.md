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
| Node Action Log | Child table containing action, actor, timestamps, exit code, stdout/stderr, state before/after and result. Reused by Node and Tenant Site. | Preserve existing records and realtime behavior during transition. Decide whether deployment-specific history needs dedicated stage records; do not force deployment lifecycle into this generic log. |
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
| `node_action_logs` | Retain | Preserve history. Bench-specific deployment logging ownership remains subject to the logging contract. |
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
| `deployment_type` | Retain with semantic restriction | Preserve Shared/Dedicated tenant hosting allocation meaning if still needed. Never reinterpret as Host/Docker. |
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

**Open decision:** whether Bench Deployment needs dedicated stage logs or can safely reuse this child table remains unresolved. Bench Deployment remains the authoritative deployment lifecycle record regardless of logging implementation. Review old stdout/stderr for secrets before deciding retention/access controls.

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

The matrix records ownership direction; it does not silently resolve these remaining contracts:

| Item | Current position | Required resolution |
|---|---|---|
| Site ingress and ports | Keep `protocol`, `port` and `fqdn` on Tenant Site pending contract. | Finalize Node ingress and Bench routing ownership before changing semantics. |
| Node and Bench logs | Preserve historical logs. | Decide deployment stage log model. |
| Database and Redis | Bench Environment owns profiles/references. | Define dedicated/shared/external provisioning and isolation. |
| Secrets | Replace plaintext with protected references. | Select provider and define migration/rotation. |
| Dependency classification | Existing records are migration inputs. | Classify each dependency by actual installation/consumption location. |
| Host Bench grouping | One Bench Environment per actual existing Bench. | Inspect paths/site configuration to establish membership. |
| `deployment_type` | Preserve Shared/Dedicated semantics. | Confirm whether still needed for tenant allocation. |
| Extension source metadata | NDF authoritative for build source/runtime composition. | Define synchronization with NDF Marketplace App Descriptor. |
| Frappe schema mechanics | Logical target model defined. | Verify field options, autoname, indexes and patch behavior against supported Frappe release. |

Additional existing-schema observations:
- `Tenant Site.port` is currently a Data field. Preserve values during migration; do not silently coerce.
- `Extension.supports_upgrade` is currently Data with a default of 1, unlike Check field `supports_uninstall`; review its type during schema work.
- `Script Execution.triggere_source` is the existing fieldname. Correct only through a controlled migration.
- `Infrastructure Node.state` currently includes Bench-oriented states such as Project Installed, Initialized, Healthy, Running and Stopped. Separate these from Node readiness.
- `Tenant Site.region` is free text, while `Infrastructure Node.region` is a Link. Reconcile before making Site region derived.

**Approval boundary:** This matrix and data migration order are approved for documentation. This approval does not authorize implementation. DocType/controller/API/migration/runtime changes remain gated by completion and explicit approval of the remaining M4.1 contracts.
