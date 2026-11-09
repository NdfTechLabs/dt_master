# dt_master — Tenant and Infrastructure Management

The management-site application for a Frappe/ERPNext tenant platform, published by **NDF Technology Labs Solutions LTD**.

`dt_master` manages infrastructure, tenant sites, application extensions, subscription capabilities, and billing. The companion `dt_tenant` app runs on customer sites and consumes the master's APIs; it is not a replacement for this management app.

## Features

- **Infrastructure operations:** framework/version and system-dependency records; remote dependency installation, bench installation and initialization, production preparation/verification, and start/stop operations.
- **Bench maintenance:** remote backup, update, migration, restore, and removal actions.
- **Tenant lifecycle:** site creation, app installation, proxy configuration, verification, suspension, resumption, and destruction, with automation states and action logs.
- **Tenant configuration:** reachability checks, tenant-app checks, settings configuration, initial capability synchronization, and administrator checks.
- **Extension marketplace:** app/version metadata and tenant install, upgrade, downgrade, reinstall, uninstall, removal, and version-check executors.
- **Subscription capabilities:** allowed roles/modules linked to subscription plans and exposed to tenant sites.
- **Billing:** available plans, billing history, invoice PDFs, plan changes, and payment APIs with M-Pesa and Flutterwave integration paths.
- **Mail administration:** remote mail-stack installation/configuration, verification, and mail-user management.

## Requirements

- A compatible Frappe/ERPNext bench; both apps are declared in `required_apps`.
- `pyproject.toml` declares Python **3.10 or later**. This is a package minimum, not a guarantee of compatibility with every Frappe release; use the Python/runtime versions required by your deployment.
- `dt_tenant` installed on the customer sites that use capability synchronization, subscription self-service, and extension management.
- Working background workers and network access to tenant sites and managed infrastructure.
- An SSH client on the execution host, non-interactive SSH authentication, and the remote permissions/scripts required by the selected operations. Commands use `ssh` in batch mode and `sudo -n`.
- Configured payment apps/accounts for the payment flows you enable, including `flutterwave_gateway` for the Flutterwave linker. These integrations are not all declared in `required_apps`.

## Installation

Install Frappe and ERPNext first. From the bench directory, fetch a branch compatible with your deployment and install on the **management site**:

```bash
bench get-app https://github.com/Dhana-Developers/dt_master.git
bench --site <management-site> install-app dt_master
bench --site <management-site> migrate
bench build --app dt_master
bench --site <management-site> clear-cache
```

Installation provides the app's records and interfaces; it does not provision servers, configure SSH, or establish tenant API credentials automatically.

## Configuration and operation

1. Define **Frameworks**, **Framework Version**, **System Dependency**, and **Framework Version Dependency** records for the target runtime.
2. Configure **Infrastructure Provider**, **Infrastructure Region**, **Infrastructure Profile**, and **Infrastructure Node** records as needed. Node execution requires an IP address, SSH user/port, verified access, project/log paths, and an absolute `scripts_base_dir`.
3. Deploy/review the scripts under `dt_master/scripts/` and ensure the configured remote script tree contains `bin/node`, `bin/site`, `bin/app`, and `bin/mail` for the operations you use. The executor runs scripts on the remote host, not directly from the local repository.
4. Create **Tenant Site** records with the correct FQDN, node, customer, subscription, and app selections. Establish the required API credentials on both sides before tenant configuration and synchronization.
5. Map subscription plans to **Capability Profile** records containing allowed roles/modules. Configure the tenant's **Tenant Settings** to call this management site.
6. Populate **Extension** and **Extension Version** records before using marketplace installation flows. Configure payment gateways and mail servers separately before using their actions.

**Operational caution:** restore, removal, site destruction, app changes, and mail actions affect real remote infrastructure and data. Review scripts, privileges, target paths, and backups before execution. Use a sandbox before production; the presence of an executor is not evidence that its full deployment flow has been validated.

## Source map

| Path | Purpose |
| --- | --- |
| `dt_master/api/` | Infrastructure, site/app executors, marketplace, capabilities, subscriptions, billing, and mail APIs |
| `dt_master/controllers/provisioning.py` | Tenant checks, configuration, and initial synchronization |
| `dt_master/dt_master/doctype/` | Infrastructure, tenant, extension, capability, framework, mail, and log records |
| `dt_master/scripts/` | Infrastructure automation scripts and supporting files |
| `dt_master/testing/` | Integration and Docker sandbox helpers; review prerequisites before running |
| `docs/migrations/` | Current M4 NDF Frappe Docker integration status and historical migration records |
| `dt_master/hooks.py` | App metadata, required apps, branding, and management app-screen entry |

## Development

From this repository:

```bash
pre-commit install
pre-commit run --all-files
```

Configured checks include Ruff, ESLint, Prettier, and file-validation hooks. GitHub Actions definitions are under `.github/workflows/`; inspect their runtime and branch configuration before relying on them for your deployment.

## License

AGPL-3.0. See [`license.txt`](license.txt).


---

# M4 — NDF Frappe Docker integration

## M4.0 — Architecture and repository discovery

**Status: Completed (2026-11-09).**

The architecture baseline for integrating NDF Frappe Docker with DT Master and DT Tenant has been agreed. M4.0 completion records architectural agreement; it does not mean the integration has been implemented.

### Agreed ownership boundaries

- **Infrastructure Node** owns infrastructure identity and health, SSH access, Docker Engine availability, capacity and node-level operations. It does not own a single Frappe Bench.
- **Bench Environment** is the runtime and deployment boundary. It owns runtime type (Host or Docker), product/release identity, image reference and digest where applicable, runtime configuration, Bench-level application availability, storage/service configuration, site membership and deployment state.
- **Tenant Site** belongs to one Bench Environment and remains the owner of site-specific configuration, data and lifecycle.
- **Tenant Installed App** remains the site-level record of applications installed on an individual Tenant Site.
- **Bench Environment Application** is the Bench-level inventory of application code available to sites hosted by that Bench. Availability in a Bench does not install an app on every site.
- **App Installation Request** is owned by DT Master. DT Tenant submits customer requests; DT Master validates and orchestrates them.
- **Framework Version** remains the runtime compatibility foundation and will be extended with approved runtime component information. A separate Runtime Profile DocType is not part of the initial agreed design.
- **NDF Frappe Docker** owns product composition, application/runtime dependency resolution, image construction, validation, immutable publishing and deployment descriptors.
- **DT Master** owns when and where a runtime is deployed, Docker/Compose orchestration, site lifecycle, replacement Bench deployment, migration, verification and rollback.
- **DT Tenant** owns tenant-facing self-service, subscription interaction and authenticated communication with DT Master. It does not orchestrate Docker.

### Deployment principles

1. Host-native and Docker-backed Bench Environments must coexist during migration.
2. A Bench Environment can host multiple Tenant Sites.
3. A site can install an application only when that application's code is available in its parent Bench Environment.
4. Changes to a Bench's application composition or runtime are delivered through a validated replacement Bench, not by mutating a live runtime in place.
5. All sites belonging to a shared Bench move together when that Bench is replaced; site-level installed-app state remains independent.
6. Deployments retain immutable image digests and versioned deployment contracts. Publishing a new image does not automatically upgrade running Bench Environments.
7. NDF produces deployment artifacts; DT Master consumes and orchestrates them.

## M4.1 — Integration contract and architecture

**Status: In progress.**  
**Authorization policy: Accepted; documentation reconciliation and remaining M4.1 reviews are in progress.**  
**Implementation status: Not started.**

M4.1 defines and approves the cross-repository contracts before implementation begins. The Section 15 identity, authentication and application-authorization decisions are accepted. NDF artifact/build contract approval, final migration/rollback review and final architecture acceptance remain outstanding.

| Workstream | Required outcome |
|---|---|
| DocType ownership and relationships | Finalize Infrastructure Node → Bench Environment → Tenant Site, plus Bench and site application inventories |
| NDF deployment artifact contract | Versioned descriptor tied to the exact OCI image digest |
| Deployment Candidate contract | Complete desired Bench composition and runtime requirements |
| Build request/result contract | Authenticated asynchronous request, workflow identity, result, digest and descriptor reference |
| Runtime compatibility | Exact Bench runtime versions derived from framework and application compatibility constraints |
| Dependency resolution | NDF-owned Python, Node.js and OS dependency resolution and deterministic locks |
| Runtime adapters | Defined Host and Docker execution interfaces in DT Master |
| Deployment lifecycle | Provision, readiness checks, site migration, verification, rollback and retirement |
| Repository migration matrix | File-level ADD / EXTEND / RELOCATE / RETAIN / REMOVE decisions |

### DT Master schema and implementation specification

The Bench Environment ownership model, parent and child DocType fields, validation rules, Tenant Site migration, Deployment Candidate/Build/Deployment records, NDF Deployment Descriptor requirements and phased implementation plan are documented in:

- [M4.1 — Bench Environment Schema and Implementation](docs/migrations/M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md)

The approved Bench Environment child tables are:

- **Bench Environment Application** — complete app inventory available in the shared runtime, including image-baked and marketplace/custom apps.
- **Bench Runtime Component** — exact deployed runtime component inventory.
- **Bench Storage Reference** — persistent storage ownership and allocation references.
- **Bench Service Definition** — NDF descriptor-derived service inventory and observed state.

The deployment records are **Deployment Candidate**, **Deployment Candidate Application**, **Deployment Build** and **Bench Deployment**. **App Installation Request** remains a DT Master-owned orchestration record in the broader M4 design.

Existing DocTypes such as Infrastructure Node, Tenant Site, Tenant Installed App, Framework Version, Extension and Extension Version are to be extended or refactored according to the schema specification, not duplicated. The specification records the agreed target; implementation has not started.

### Cross-repository architecture records

- [DT Tenant M4 architecture](https://github.com/NdfTechLabs/dt_tenant#dt-master-and-ndf-integration)
- [NDF Frappe Docker M4 architecture](https://github.com/NdfTechLabs/frappe_docker/blob/main/images/ndf/README.md)

No production deployment should rely on an M4.1 contract until its schema/version, validation rules and compatibility behavior have been agreed and tested.
