# M4 — NDF Frappe Docker integration

**Status:** Architecture discovery in progress  
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

DT Master should extend its existing shared execution architecture:
dt_master/api/script_contract.py and the scripts/bin / scripts/lib contract.
A host runtime adapter and a system-Docker runtime adapter are candidates for
the design; their interfaces and ownership boundaries must be established
during discovery.

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

| Stage | Scope | Status |
|---|---|---|
| M4.0 | Architecture and repository discovery | In progress |
| M4.1 | Audit and retire competing Docker implementation in DT Master | Pending code audit |
| M4.2 | Bench deployment model and hybrid host/Docker runtime boundary | Target model agreed; schema/design discovery pending |
| M4.3 | Consume NDF manifests, Compose definitions and GHCR digests | Pending |
| M4.4 | Provision master and tenant benches/sites through DT Master | Pending |
| M4.5 | Controlled app/image release, upgrade, approval and rollback lifecycle | Pending |
| M4.6 | Isolated live-server pilot alongside existing host-native deployments | Pending |
| M4.7 | Migration readiness, backup/restore, cutover and operational acceptance | Pending |

M1–M3 of NDF Frappe Docker are recorded as completed in the project milestone
history. M4 is the integration milestone; publishing an image alone does not
complete it.

## 9. Discovery checklist and implementation gates

Before code changes or destructive repository cleanup:

1. Complete a file-level audit of Docker-related code, DocTypes, scripts,
   configuration and tests in dt_master:develop. Classify each item as
   remove, replace, preserve or adapt.
2. Inspect the full schemas and relationships for Infrastructure Node,
   Tenant Site, Tenant Installed App, Framework Version, Extension,
   Extension Version and related execution records.
3. Map current node, bench and site call paths and determine how a Bench
   deployment is represented and addressed.
4. Inspect NDF manifests, schema, resolver, lock output, Compose definitions,
   image metadata and release workflow. Establish the exact deployment
   contract and compatibility evidence.
5. Define the system Docker execution identity, least-privilege access,
   resource labels/ownership, network/volume naming, port allocation and
   safe deletion rules.
6. Define separate master and tenant bench provisioning flows.
7. Define controlled image and app upgrades, approval, rollback and recovery.
8. Adapt integration tests to validate the actual NDF image contract and
   DT Master orchestration.
9. Deploy isolated staging master and tenant benches on the live server,
   without changing existing host-native production sites.
10. Require explicit acceptance and rollback evidence before any production
    migration or traffic cutover.

No production cutover, host-native bench migration, site deletion, Docker
resource pruning or automatic upgrade is authorized by this document.

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
