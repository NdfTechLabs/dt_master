# M4 — NDF Frappe Docker integration

**Status:** M4.0 architecture discovery completed; M4.1 integration contract and schema design in progress; NDF artifact contract, migration/rollback policy, candidate/build lifecycle and owner caller-inventory confirmation accepted. Final architecture review and M4.1 closure remain pending.  
**Source branch:** NdfTechLabs/dt_master:develop  
**Scope:** Integrate NDF Frappe Docker with DT Master and DT Tenant without
disrupting existing host-native deployments.

## 1. Purpose and ownership

M4 connects the image and product-composition work completed in NDF Frappe
Docker to the existing DT Master orchestration platform.

| Component | Responsibility |
|---|---|
| NDF Frappe Docker | Dockerfiles, product compositions, manifests, Compose/deployment definitions, build and validation workflows, image publishing and release metadata |
| GHCR | Distribution of published images and immutable image references |
| System Docker Engine | Container runtime on the deployment host |
| DT Master | Infrastructure nodes, Bench deployments, site lifecycle, deployment orchestration, application lifecycle, health, backup and recovery coordination |
| DT Tenant | Tenant-local configuration, capability synchronization, tenant management and authenticated communication with DT Master |

DT Master consumes the NDF Frappe Docker contract. It must not maintain a
competing Dockerfile, image-build, product-composition or Compose system.

## 1.1 Approved Bench Environment schema

The agreed parent/child DocType model, field-level schema, validation rules,
Tenant Site relationship migration, Deployment Candidate/Build/Deployment
records, NDF Deployment Descriptor requirements and phased implementation
plan are maintained in
[M4.1 — Bench Environment Schema and Implementation](M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md).

That specification records the approved target architecture. It is not evidence
that the proposed DocTypes, fields, validators, APIs or runtime adapters have
already been implemented. Code changes remain gated by completion and review
of the M4.1 contracts and migration plan.

## 2. Server baseline — 2026-11-05

The M4 pilot server is mail.ndftechlabs.com, running Ubuntu 24.04.4 LTS,
x86_64.

### Active system Docker environment

| Component | Verified state |
|---|---|
| Docker Engine | 29.9.3, operational |
| Docker API | 1.56 |
| Docker Compose | 5.7.0 |
| Docker Buildx | 0.38.1 |
| Storage driver | overlayfs |
| Docker data root | /var/lib/docker |
| CPU | 6 |
| Memory | 11.68 GiB |
| Filesystem | 193 GB total |
| Available disk after cleanup | 95 GB |
| Disk usage after cleanup | 98 GB (51%) |

The system Docker inventory at verification contained one stopped hello-world
container and two images:

- ghcr.io/ndftechlabs/dhanaerp-dalali:local — retained NDF development image.
- hello-world:latest — smoke-test image.

System BuildKit reported 16.23 GB of cache, all reported reclaimable by Buildx.
This cache was deliberately retained. The system Docker image, cache, volumes,
networks and daemon were not part of the rootless cleanup.

These observations are a dated baseline, not a guarantee that the inventory
remains unchanged. Re-run read-only Docker inspection before deployment or
cleanup operations.

### Retired rootless environment

A separate rootless Docker environment was identified as an unintended
implementation path and has been retired. It used:

- Dedicated account: dt-containers, UID/GID 5001.
- Docker binary: /opt/ndf-rootless/29.6.1/bin/docker.
- Socket: /run/user/5001/docker.sock.
- Data root: /home/dt-containers/.local/share/docker.

The rootless daemon was stopped and disabled; its Docker data directory and
runtime installation were removed; lingering was disabled; and the dedicated
account/home directory were deleted. Subsequent system Docker verification
confirmed that the intended system daemon remained operational and its NDF
image and BuildKit cache were preserved.

The rootless Dalali exact-runtime images, rehearsal volumes, custom network and
rootless BuildKit cache are not part of the M4 deployment target and must not be
reintroduced as dependencies.

Do not use the retired socket, binary, account or paths in scripts, runbooks,
DT Master configuration or future automation.

## 3. Bench deployment model

M4 adopts the following logical hierarchy:

~~~text
Infrastructure Node
|
+-- Bench A
|   Runtime: Host
|   Framework: Frappe v16
|   Sites:
|   +-- dalali.example.com
|
+-- Bench B
|   Runtime: Docker
|   Product composition: dhanaerp-master
|   Image: GHCR reference
|   Digest: sha256:...
|   Sites:
|   +-- master.example.com
|
+-- Bench C
    Runtime: Docker
    Product composition: dhana-tenant
    Image: GHCR reference
    Digest: sha256:...
    Sites:
    +-- customer-a.example.com
    +-- customer-b.example.com
~~~

A Bench is a first-class logical deployment boundary regardless of runtime.
One Infrastructure Node may host multiple Bench deployments. One Docker Engine
may host multiple Docker-backed benches. Each bench owns its deployment
configuration and site inventory; removing one bench must not remove shared
runtime resources or resources owned by another bench.

The existing implementation derives a single bench path from the node
(project_base_dir/bench) and currently has no dedicated Bench DocType. M4
discovery must verify the complete DocType relationships and execution paths
before deciding whether a new Bench DocType or a compatible extension is
required.

Each Bench deployment must be able to record or resolve, as applicable:

- Runtime type and runtime identity.
- Product composition and manifest identity.
- Image reference and immutable digest.
- Framework and application inventory.
- Site membership.
- Runtime configuration, network and port allocation.
- Database/Redis isolation and ownership.
- Backup/recovery policy and deployment history.
- Health and operational state.

Host-native and Docker-backed benches must operate side by side during gradual
migration. Existing host-native sites are not implicitly migrated or modified
when Docker support is introduced.

## 4. Runtime integration principles

The deployment host uses the existing system Docker Engine through the normal
Docker CLI/API and Compose plugin. The verified command surface is:

~~~bash
sudo docker info
sudo docker compose version
sudo docker buildx version
~~~

The final DT Master execution identity and privilege model remain to be
designed. Do not assume production orchestration should run every Docker
command through unrestricted root privileges. Define least-privilege access,
socket/API access, remote execution, audit logging and authorization as explicit
implementation requirements.

DT Master extends its existing shared execution architecture:
dt_master/api/script_contract.py and the scripts/bin / scripts/lib contract.
The common versioned runtime adapter contract is approved in M4.1. Both the
Host adapter and system-Docker adapter implement runtime inspection, preflight,
provisioning, lifecycle, deployment, storage, recovery, endpoint registration
and structured health reporting. DT Master remains the orchestration and
policy authority; adapters execute runtime-specific mechanics only. Every
mutation is invoked through the durable operation coordinator and returns a
structured, sanitized result correlated to its operation. The Host adapter
continues to manage existing Host-native Benches; the Docker adapter consumes
verified OCI artifacts and NDF service contracts. Exact method signatures,
transport schemas, timeout/retry behavior and Node-side execution details are
implementation work for M4.3/M4.7 and must conform to the approved contract.

The system Docker daemon is a shared node-level service. A Bench deployment
must own only its explicitly labelled containers, networks, volumes and
configuration. Shared databases, Redis, networks, reverse proxies or runtime
resources must never be deleted as a side effect of removing a bench.

## 5. NDF image and release contract

NDF Frappe Docker is the authoritative source for product image construction
and deployment composition. M4 consumes its manifests and deployment
definitions rather than duplicating them in DT Master.

The NDF repository has completed its initial repository/build/publishing and
release-management milestones. A published master image was recorded during
M3:

- Image: ghcr.io/ndftechlabs/dhanaerp-master
- Version tag: v16-1.0.0
- Moving convenience tag: v16-latest
- Commit tag: v16-b139ceb22bf75a6ed5e08b3b3ac327a2d9000c31
- Recorded digest: sha256:33f549f9488f20a857e34c18506881f524065034418d6e873b9f492182af4d15

This is evidence of image publication and inspection, not proof that the image
has passed live runtime compatibility, Bench provisioning, site provisioning,
upgrade or recovery acceptance on the target server.

Production deployments must resolve and retain immutable digests. Tags may
be used for discovery/channel selection, but a deployment record must identify
the exact digest actually deployed. Publishing a new image must not
automatically upgrade running production benches.

Master and tenant compositions remain distinct. A master composition must not
be installed as a tenant composition, or vice versa. Compatibility certification,
manifest lock enforcement, secrets handling, release channels, approval,
rollback and deployed inventory reconciliation remain M4 design/implementation
work.

## 6. Existing capabilities to preserve

The develop branch inspection identified existing orchestration capabilities
that M4 should build upon rather than replace:

- Infrastructure node lifecycle and remote execution.
- Shared script contract with validation, command construction, streamed
  execution and log redaction.
- Bench backup, update, migration, restore and removal operations.
- Tenant site creation, app installation, verification, suspension, resumption
  and destruction.
- Application install, upgrade, downgrade, reinstall, uninstall and version
  checks using Extension, Extension Version and Tenant Installed App.
- Authenticated DT Master-to-DT Tenant provisioning and capability
  synchronization.
- Existing integration tests for current SSH/script orchestration paths.

These capabilities currently reflect host-native and Git/source-reference
execution in important areas. Their presence does not establish NDF image
consumption or container deployment support. M4 must map and adapt them
explicitly.

## 7. Historical Dalali same-VM migration work

The same-vm-docker directory documents a separate Dalali migration experiment,
including rootless Docker setup, exact-runtime image experiments, isolated
restore rehearsal, staging and recovery procedures.

That workstream is closed as the selected M4 runtime direction. Its historical
results may be useful as technical reference, but its rootless installation
instructions, account/socket paths, images, volume names and deployment
scripts are superseded for M4.

The old records are retained for audit/history. They are not the current
deployment runbook. The rootless runtime and its resources have been removed
from the server. The previous rehearsal result must not be interpreted as
acceptance of the NDF Frappe Docker product image or DT Master integration.

## 8. M4 milestone status

The following is the authoritative M4 milestone structure. M4.0 is completed;
M4.1 is the active architecture and contract milestone. M4.2–M4.7 have not
started. Earlier milestone labels or stage breakdowns in historical notes do
not supersede this table.

| Milestone | Name | Repository | Scope | Status |
|---|---|---|---|---|
| **M4.0** | Architecture and Repository Discovery | Both | Audit existing implementations, ownership, boundaries and target architecture. | **Completed** |
| **M4.1** | Integration Contract and Architecture | Both | Finalize schema ownership, relationships, deployment contracts, runtime interfaces, migration and NDF integration contracts. | **In progress** |
| **M4.2** | Runtime Infrastructure Enhancements | `frappe_docker` | NDF descriptors, runtime definitions, service topology and deployment artifacts. | Not started |
| **M4.3** | Docker Orchestration Engine | `dt_master` | Docker/Compose orchestration, Node integration, Bench lifecycle and runtime adapters. | Not started |
| **M4.4** | Product Image Deployment | `dt_master` | Immutable NDF images, deployment candidates, build tracking, provisioning and replacement deployments. | Not started |
| **M4.5** | Runtime Application Management | Both | Bench-level app availability, marketplace compatibility, site app installation governance and composition changes. | Not started |
| **M4.6** | Tenant Site and DT Tenant Integration | Both + `dt_tenant` | Site lifecycle with Bench Environments, tenant requests, site-level app installation and lifecycle reporting. | Not started |
| **M4.7** | Integration Testing and Production Readiness | Both | End-to-end tests, migration validation, security, monitoring, recovery, rollback and production readiness. | Not started |

### M4.1 current position

M4.1 — Integration Contract and Architecture was formally closed/accepted on 2026-11-09 following the final 13-point architecture acceptance review and the platform owner's explicit approval. The target Bench Environment ownership/schema, formal migration dispositions, runtime/ingress/health architecture, NDF artifact ownership principles, source-level API/UI/security audits, cross-repository authorization policy, NDF artifact/build/deployment contract, migration/rollback policy, common runtime adapter contract, durable operation contract and secret transport contract are accepted and recorded in the canonical specification. DT Master and DT Tenant retain authoritative operation and secret ownership for their respective responsibilities; cross-repository workflows use shared correlation IDs and protected secret references. The platform owner confirms no additional DT Master API consumers are known outside DT Tenant; independent runtime, deployment and credential-inventory verification remains an implementation prerequisite. **Implementation authorization has not been granted.** M4.2–M4.7 remain pending.

Live Frappe behavior checks that do not change the architecture are recorded
as implementation verification requirements for the relevant implementation
or production-readiness stage. They are not independent M4.1 workstreams and
do not justify building a separate CI test platform during this architecture
review.

M1–M3 of NDF Frappe Docker are completed project milestones preceding M4.
They are not sub-milestones of M4. M4 is the integration milestone; publishing
an image alone does not complete it.

## 9. M4.1 completion position and later implementation gates

The source-level discovery and repository audit, architecture contracts and documentation reconciliation were accepted as the M4.1 baseline. The platform owner explicitly approved M4.1 closure on 2026-11-09. The following table distinguishes accepted architecture decisions from external verification and later implementation work.

| Workstream | Current position | Milestone |
|---|---|---|
| DT Master and DT Tenant repository tree inventory | Full develop-branch trees retrieved; grouped dispositions recorded in M4_FILE_DISPOSITION_MATRIX.md | M4.1 — source audit completed |
| Existing DocType/schema/ownership audit | Node → Bench Environment → Tenant Site hierarchy and migration dispositions documented | M4.1 — architecture resolved |
| Existing API, UI, execution and security audit | Principal source paths reviewed; findings and proposed dispositions recorded | M4.1 — source audit completed |
| Cross-repository service identity and authorization | Section 15 of the canonical schema specification; platform/tenant/service identity separation, tenant binding, Frappe API key/secret authentication, least privilege, Tenant User read-only policy and Tenant Manager application operations accepted | M4.1 — policy accepted and documentation reconciled |
| NDF manifest, lock, build and workflow audit | NDF main tree reviewed; SHA-to-build mismatch and missing descriptor/candidate/service contracts recorded in Section 12 and Section 16 of the canonical specification | M4.1 — source audit completed |
| NDF application, composition and deployment descriptor contracts | Accepted contract recorded in Section 16 of the canonical schema specification, including immutable artifacts and mandatory signature/attestation verification | M4.1 — contract accepted; implementation assigned to M4.2–M4.7 |
| Runtime adapter, durable operation and secret transport contracts | Common Host/Docker adapter contract, split operation ownership, shared cross-repository correlation, resource-level mutation locks, separate business/execution states, explicit retry/recovery policy, split secret ownership, reference-only transport, protected short-lived runtime delivery, rotation/revocation and backup-restore reconciliation accepted and recorded in the canonical specification | M4.1 — contracts accepted; exact schemas/providers/delivery mechanics remain implementation work |
| NDF descriptor/runtime/service implementation | Implement immutable build inputs, policy enforcement, runtime/dependency resolution, service definitions and deployment descriptors | M4.2 |
| DT Master Docker orchestration | Implement Node/Bench runtime adapters and Docker/Compose lifecycle | M4.3 |
| Candidate image deployment | Implement candidate submission, build tracking, descriptor/digest verification and replacement Bench deployment | M4.4 |
| Marketplace release and app availability | Implement app descriptor synchronization, Bench app availability and Product Inclusion Request lifecycle | M4.5 |
| Tenant Site and DT Tenant integration | Implement tenant service identities, Site → Bench migration and tenant lifecycle integration | M4.6 |
| End-to-end testing, staging, recovery and production acceptance | Validate migration, compatibility, security, rollback and production operations | M4.7 |

### M4.1 exit criteria and closure record

The following exit criteria were satisfied and M4.1 was formally closed on 2026-11-09 after explicit owner approval:

1. The repository disposition matrix and principal caller/security audit are
   reviewed. The platform owner confirms no additional DT Master API consumers
   are known outside DT Tenant; independent runtime/deployment and credential
   inventory verification remains an implementation prerequisite.
2. The accepted Section 15 authorization decisions are reconciled across both repositories, with billing policy boundaries and implementation-only verification clearly distinguished.
3. The Section 16 NDF artifact, candidate, build/result, descriptor,
   immutable-source and mandatory artifact-trust contracts are reviewed and
   explicitly approved.
4. Runtime adapter, durable operation, secret transport and migration
   contracts are reviewed and accepted against the agreed Node → Bench
   Environment → Site ownership model.
5. Migration sequencing, acceptance criteria, implementation ownership and
   rollback boundaries are agreed.
6. Implementation authorization remains a separate mandatory gate before any
   application code, DocType, migration, script, workflow or runtime change.
   It was not granted by M4.1 closure and remains pending.

**Closure outcome:** M4.1 is Closed / Accepted. Runtime-only Frappe permission,
worker identity, realtime delivery, export, report, print and backup checks are
deferred to the relevant implementation and production-readiness stages.
They do not reopen M4.1 or authorize implementation.

No production cutover, host-native Bench migration, site deletion, Docker
resource pruning or automatic upgrade is authorized by these documents.

## 10. Security and evidence handling

Never commit production database dumps, customer files, encryption keys,
passwords, SSH keys, API tokens, private configuration, .env files or raw
server audit output. Commit only sanitized summaries and non-secret deployment
metadata.

Before any cleanup, inspect resource labels and ownership. Do not use broad
prune commands against the shared system Docker daemon. Do not remove
unlabelled volumes or shared networks without proving ownership and retention
requirements.

## 11. Source references

- NDF Frappe Docker: https://github.com/NdfTechLabs/frappe_docker
- DT Master — develop: https://github.com/NdfTechLabs/dt_master/tree/develop
- DT Tenant — develop: https://github.com/NdfTechLabs/dt_tenant/tree/develop
- Historical same-VM Dalali migration: same-vm-docker/README.md


## 12. NDF source audit and integration-contract findings

**Audit repository:** NdfTechLabs/frappe_docker  \
**Branch:** main (repository default branch)  \
**Tree SHA:** 7c16ac4cf56226e417b941144da272267937dd84  \
**Files in tree:** 198  \
**Audit mode:** Read-only source/schema/workflow review.

The DT Master and DT Tenant applications were audited on their requested
develop branches. NDF Frappe Docker uses main as its default/source branch;
the source review therefore uses main rather than assuming a develop branch.

### 12.1 Existing capabilities to preserve

- application-manifest-v1 schema and recursive manifest loader;
- deterministic dependency graph ordering and lock generation;
- manifest checksums and immutable Git SHA resolution in lock files;
- image definitions mapping product IDs to GHCR repositories;
- build preparation and metadata generation;
- Buildx image construction, OCI labels, digest output and image-content
  validation;
- existing NDF build, publish and release workflows;
- upstream Compose files and override model.

These are the foundation to extend, not replace with a second builder/resolver.

### 12.2 Source-level contract gaps

1. **Resolved SHA is not the build input.** The resolver stores app commit SHAs
   in lock files, but prepare_build.py writes target_ref branches into
   apps.json. build_image.py passes framework.target_ref to FRAPPE_BRANCH.
   Containerfile uses those refs for Bench initialization. The image validator
   does not inspect installed Git HEADs. The lock can therefore claim a commit
   that the built image has not proven to contain.
2. **Base images are tag-based.** The build and runtime stages reference
   frappe/build and frappe/base using the Frappe branch tag, not a verified
   immutable base-image digest.
3. **Manifest policy is not fully enforced.** allow_development_branches and
   fail_on_unverified_compatibility are accepted by the schema but are not
   enforced by the resolver. validation.required_checks are declarations,
   not executable product acceptance checks. The current base manifest
   includes telephony on develop while its resolution policy disallows
   development branches.
4. **No per-app Marketplace App Descriptor.** application-manifest-v1 is a
   composition manifest; app entries use name/repository/target_ref and do
   not provide registered app_id, release descriptor digest, compatibility
   ranges or Python/Node/OS dependency declarations.
5. **Service declarations are not part of the build contract.** The
   ai-automation manifest contains services, but the resolver composes only
   applications. The current image pipeline does not build/publish those
   services from the declaration.
6. **No Product Deployment Descriptor implementation.** No descriptor
   schema/generator or digest-bound artifact was found in the audited NDF
   tree.
7. **No dynamic candidate workflow.** The existing workflow selects predefined
   product IDs. No workflow input/result contract for DT Master-generated
   Deployment Candidates was found.
8. **No Master notification or Product Inclusion Request flow.** No
   authenticated release callback, build-result callback or product inclusion
   workflow was found.
9. **Release workflow coverage is incomplete.** The current release workflow
   promotes the DhanaERP Master image only.
10. **Generated build files are tracked.** images/ndf/.build/dalali-v16 contains
    apps.json and build-metadata.json. These should be ephemeral generated
    outputs; deterministic lock files remain tracked.

### 12.3 NDF integration contract

The target contract is recorded in Section 16 of
M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md. It defines:

- Marketplace App Descriptor;
- Product Composition Manifest;
- DT Master-owned Deployment Candidate;
- NDF Product Deployment Descriptor bound to an immutable image digest;
- runtime/dependency resolution and deterministic locks;
- NDF Bench service topology versus DT Master Node/Bench deployment ownership;
- asynchronous GitHub Actions build request/result;
- separate marketplace release approval and product inclusion governance;
- SHA-enforced image builds and verification of actual installed commits.

### 12.4 Milestone placement

The contract is an M4.1 architecture deliverable. Its implementation belongs
to the existing milestone structure:

- M4.2: NDF descriptors, runtime/dependency resolution, service topology,
  SHA-enforced builds and deployment artifacts.
- M4.3: DT Master runtime adapters and Docker orchestration.
- M4.4: candidate build tracking, digest/descriptor verification and product
  image deployment.
- M4.5: app release synchronization and marketplace/product inclusion.
- M4.6: tenant integration.
- M4.7: end-to-end and production-readiness verification.

The SHA/build mismatch is a blocker to trusting the current lock as proof of
the exact source inside a published image. It must be corrected and validated
in M4.2 before DT Master treats the deployment descriptor as authoritative.

No NDF, DT Master or DT Tenant application code, workflows, manifests,
schemas, image definitions or runtime configuration were changed by this
source audit.
