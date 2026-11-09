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
