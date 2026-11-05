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
