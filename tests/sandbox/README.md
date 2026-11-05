# Local Docker integration sandbox

This is a **separate Docker Compose project**, not a site inside your existing
Bench. No host `sites/`, Python environment, database, SSH directory or Docker
socket is mounted. Only this app's shell scripts are mounted, **read-only**.
App Python/JS code is copied into a custom image at build time.

## Start

From `apps/dt_master`:

```sh
bash tests/sandbox/sandbox.sh up
bash tests/sandbox/sandbox.sh credentials
```

First startup downloads/builds images and installs ERPNext on two new sites;
allow several minutes and several GB of disk space. Images are version-pinned;
the Frappe/ERPNext base is also digest-pinned. Random test-only passwords are
saved in `tests/sandbox/.env` with mode 600. Do not commit or reuse that file.

- Master UI: **http://master.localhost:8088/app**
- Tenant UI: **http://tenant.localhost:8088/app**
- User: **Administrator**
- Password: printed by the `credentials` command above.

Modern browsers resolve `*.localhost` to the loopback address. If your system
doesn't, add those two names to your local hosts file or use curl's `--resolve`.
Port 8088 is bound only to **127.0.0.1**, not your LAN. Change `SANDBOX_PORT` in
the private `.env` before startup if 8088 is occupied.

## What is created

- A pinned Frappe/ERPNext v15 image containing this working copy of `dt_master`.
- Separate MariaDB and Redis services with **no published database/Redis ports**.
- `master.localhost`: ERPNext and `dt_master`, including their actual DocTypes.
- `tenant.localhost`: ERPNext, ready for testing `dt_master` app installation.
- `Docker Sandbox` Infrastructure Node and `Sandbox Tenant` Tenant Site records,
  plus a framework/extension/version and pending installed-app row.
- Backend, frontend, websocket, worker and scheduler services.
- An internal SSH runner using sandbox-only keys. The master can therefore run
  the real API → SSH → environment → shell chain without contacting your host.
- Project-scoped named volumes for sites, databases, backups, logs and SSH keys.

The application/database network is Docker-internal: backend, workers, runner,
database and Redis cannot call real payment APIs or send external mail. Only the
Nginx frontend also joins a bridge network so Docker can publish its loopback
browser port. Build-time package/image downloads still use the network.

## Test and inspect

```sh
# Real API → SSH → shell backup of both test sites:
bash tests/sandbox/sandbox.sh smoke

# Full site-function integration suite (creates its own disposable ERPNext site):
bash tests/sandbox/sandbox.sh integration

bash tests/sandbox/sandbox.sh status
bash tests/sandbox/sandbox.sh logs setup
bash tests/sandbox/sandbox.sh logs backend
bash tests/sandbox/sandbox.sh logs runner
bash tests/sandbox/sandbox.sh shell
```

Inside the backend shell:

```sh
bench --site master.localhost list-apps
bench --site tenant.localhost list-apps
bench --site master.localhost execute dt_master.testing.docker_sandbox.seed
```

Seeding is idempotent: it does not reset demo records you've changed during tests.
Sandbox seed/smoke helpers are **not whitelisted**, require the explicit Docker
flag, the master site name and the internal database hostname.

### Integration suite

`integration` copies the current API Python files and guarded integration helper
into the sandbox backend, then invokes the real executors in a fresh Bench CLI
process. This allows testing contract fixes without rebuilding an image each time.
It does not copy files into the host Bench or change production databases.

The master and existing tenant are inspected/backed up only. Mutations target a
uniquely named `integration-<run-id>.localhost` site with a matching configuration
marker. The suite tests app inspection/install/uninstall/reinstall, suspend/resume,
scoped migration, backups, restore rejection, and restoration of database records,
public/private file contents and an encrypted secret after deliberately changing
the site's encryption key. It also verifies real realtime event payloads.

Successful teardown only removes that marked disposable site. If restoration is
not verified, the fixture is retained for investigation. JSON reports are written
under `/home/frappe/automation-logs/integration/` in the sandbox log volume. Backups
are persistent; Frappe's extra archives remain only in the runner's writable layer
and are lost when that container is recreated. Use the backup volume for durable
recovery. Initial fixture provisioning can take
several minutes because it installs ERPNext.

See [`INTEGRATION_RESULTS.md`](INTEGRATION_RESULTS.md) for the verified live run.

To retry against a retained fixture without provisioning ERPNext again, use the
run ID printed in the JSON report:

```sh
bash tests/sandbox/sandbox.sh integration <10-character-run-id>
```

Resume requires the existing site's integration marker to match exactly. Each
attempt writes a separate report. The integration command reloads the installed-app
child DocType on the sandbox master so new tenants receive unique row names;
it does not rename existing rows.

The suite intentionally does not remove shared app code, remove this running
bench, upgrade Git checkouts or configure OS/mail services. Those require separate
disposable fixtures/a VM. After API development changes, rebuild/restart the
backend for browser worker processes to load the same code; the integration CLI
always runs in a fresh process.

The Infrastructure Node includes the real SSH/config paths. You can use the
Tenant Site form to install/check/uninstall the pending `dt_master` row on the
tenant, and use the node's Bench menu for backups/scoped migrations/restores.
Backups stay in the sandbox's own named volume, not your host backup directory.

## Boundaries

This is **not a VM**. OS provisioning, swap management, systemd, host Nginx/
Supervisor setup, mail installation, Git-based bench upgrades and full bench
removal are not supported integration tests here. The image owns app code;
rebuild/recreate containers rather than using `bench update` on it. A disposable
Ubuntu VM remains the correct environment for those full-machine tests.

There is no separate per-site container network name for `tenant.localhost`;
it is a browser hostname, not an internal service name. The smoke test exercises
backup execution, not downstream tenant-to-master HTTP/payment callbacks.

To test changed Python/JS code, run `up` again to rebuild the image. It keeps
existing data and migrates the sandbox master during setup. To run an additional
migration explicitly:

```sh
docker compose --env-file tests/sandbox/.env -f tests/sandbox/compose.yaml \
  exec backend bench --site master.localhost migrate
```

Shell changes are available immediately through the read-only mount.

## Stop without deleting test data

```sh
bash tests/sandbox/sandbox.sh stop  # Stop services; keep containers and volumes.
bash tests/sandbox/sandbox.sh down  # Remove containers/network; keep volumes.
```

Neither command removes volumes. No cleanup command in this helper deletes
databases or backups. Only explicitly delete this project's named volumes when
you really want to discard the sandbox data; never use a global Docker prune.
