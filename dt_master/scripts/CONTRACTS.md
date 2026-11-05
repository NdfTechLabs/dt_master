# Automation contracts

## Layers

`DocType form → whitelisted API → api/script_contract.py → bin/* → lib/*`

Existing script paths and environment names remain valid. Entry scripts are thin
wrappers. `script_contract.py` owns Python environment construction, quoting,
output parsing and SSH streaming; `lib/` owns shell operations. Deploy the whole
scripts directory to the node's configured `scripts_base_dir`, not only `bin/`.

## Environment and transport

- Paths come from Infrastructure Node (`scripts_base_dir`, `project_base_dir`,
  `project_logs_dir`) and must be absolute.
- Framework Version supplies `FRAMEWORK_NAME`, `FRAPPE_VERSION`, `PYTHON_VERSION`,
  `NODE_VERSION`, `BENCH_PACKAGE`. JSON and base64 framework contexts contain the
  same data. Dependencies are an ordered JSON array with `name`, `install_method`,
  `group`, `version`, `repository`; installer failures must return nonzero.
- Tenant Site supplies `SITE_NAME`, `DB_NAME`, admin/MySQL credentials, protocol
  and `FRAPPE_UPSTREAM_PORT`. The latter is private Gunicorn's port; public HTTP
  and HTTPS use the tenant proxy on ports 80 and 443.
- Tenant Installed App plus Extension/Extension Version supply app name,
  repository, install mode and ref. Numeric release targets receive one `v`
  prefix; existing tags are not double-prefixed. Rolling upgrades fast-forward
  branches; fixed upgrades and tag downgrades check out tags without discarding
  dirty working trees.
- Mail Server supplies domain, hostname, DKIM selector and client ports. Mailbox
  addresses must belong to that server's domain. MySQL-backed mail actions require
  a linked Tenant Site containing database credentials; mailbox variables are
  constructed independently of that link. Mailbox passwords use `get_password()`.
- Each export is individually shell-quoted. SSH uses an argv list, not a local
  `shell=True`. stdout and stderr are drained concurrently and credential values
  are redacted before publication/persistence.
- APIs check document permissions. Bench maintenance endpoints additionally
  require System Manager and verify tenant/node ownership for scoped operations.

## Results and events

Only bare `key=value` lines are parsed as machine results; logs are not results.
Nonzero exits mean failure. Missing/false required results are not success.

App install/upgrade/downgrade report `installed` and their actual Git/version
metadata. Uninstall reports `uninstalled`; a false value is a no-op, not proof of
absence. If absence was verified, `installed=false` is included. Removal reports
`removed`. Partial uninstall/removal outputs must not erase unrelated Git metadata.
Inspection reports site installation state, not merely presence of shared app code.
`check_app.sh` keeps `head_state` and adds the `git_head_state` alias; Python accepts
both. Version inspection without `SITE_NAME` remains bench-scoped.

| Event | Identity fields | Incremental / final fields |
|---|---|---|
| `script_execution_log` | `node`, `execution` | `line`, `stream` / `status` |
| `tenant_script_log` | `tenant` | `line`, `stream` / `status` |
| `tenant_app_log` | `tenant`, `row` | `line`, `stream` / `status` |
| `mail_executor_log` | `server` | `line`, `stream` / `status` |

`stream` is `stdout` or `stderr`; final status is `Completed` or `Failed`.
These describe process completion. Site/node verification retains its diagnostic,
non-blocking legacy behavior: exit zero is not a guarantee of healthy services.

## DocType lifecycle conventions

- Tenant Installed App rows have unique hash names, independent of whether the
  parent has been named yet. Existing row IDs are preserved. Tenant validation
  permits the same app on different sites but rejects duplicate app rows on one
  site. Apply the DocType change with a normal site migration before deployment.

- Site creation ends at `installing_apps / Provisioning`; successful app setup
  ends at `ready / Active`.
- A suspended site uses `idle / Suspended`, as the existing form and Select field
  expect. Resume returns to `ready / Active`.
- Successful site destruction uses `idle / Archived`, not nonexistent `archived`
  or `destroyed` automation-state values. Failure preserves the prior site status.
- Mail configuration actions accept the state produced by the preceding action;
  verification ends at `ready / Active`. Mailbox results update actual parent
  child rows, not detached child-document copies.
- Node and tenant audit records use `state_before` and `state_after`, the fields
  defined on Node Action Log. Mail audit records use Mail Server Log's fields.

## Bench maintenance API

Endpoints in `dt_master.api.infrastructure_node`:

| Endpoint | Parameters | Wrapper |
|---|---|---|
| `backup_bench` | `node_name`, optional `tenant_name` | `backup_bench.sh` |
| `update_bench` | `node_name` | `update_bench.sh` |
| `migrate_bench` | `node_name`, optional `tenant_name` | `migrate_bench.sh` |
| `restore_backup` | `node_name`, `tenant_name`, absolute `restore_dir` | `restore_backup.sh` |
| `remove_bench` | `node_name`, optional `stop_only` | `remove_bench.sh` |

These are available under the Infrastructure Node form's **Bench** menu.
Successful removal ends at `Decommissioned / Retired`; stop-only uses
`Stopped / Maintenance`. Missing confirmation or failed operations are audited
as failures and place the node in `Error / Maintenance`.

## Backup and removal safety

- Backups default to `$PROJECT_BASE_DIR/backups`; `PROJECT_BACKUP_DIR` overrides
  this for shell callers. Each backup is isolated under `<site>/<unique-stamp>/`.
  Standard unencrypted Frappe database and public/private file artifacts are
  required. Site configuration (including the original encryption key) and a
  `site_name` manifest are saved privately alongside them. Encrypted backup-file
  variants require separate support; an incomplete/unsupported artifact set fails.
- All configured sites are included, even if they have no site-level `apps.txt`.
  Retention is per site, requires keeping at least one snapshot, and runs after
  successful updates. Backups must not be stored inside the bench or virtualenv.
- Update and migration take backups first. Failed backups block changes. Restore
  validates the manifest/artifacts, backs up the current site, restores both file
  archives and the database, reapplies the original encryption key, then migrates.
  Files-only restore is explicitly rejected rather than invoking a nonexistent CLI.
- Bench maintenance and standalone backup entrypoints share a per-project flock.
- Bench removal never calls `drop-site` or SQL deletion commands. It keeps
  databases and saves site connection/encryption settings under
  `backups/removed-bench/`. Only matching Nginx configuration and an owned
  Supervisor symlink are removed. Unsafe roots/symlinked bench paths are rejected.
- Site destruction is different: it intentionally removes the database/user and
  residual site directory. App removal uninstalls data from referencing sites
  before deleting shared code; inspection errors stop removal. With missing app
  code, DB-installed-app registry cleanup is attempted rather than claiming that
  editing an `apps.txt` file cleaned the database.

Run `python3 -m unittest discover -s tests -v` from the app repository. See
`tests/README.md` for coverage and the required disposable-bench integration pass.
No production migration, deployment, service restart or database operation is
performed by this test suite.
