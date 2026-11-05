# Verified Docker integration results

Run ID: `036fd2c1a0` — **21 passed, 0 failed**.

These checks used real Frappe API executors, SSH, shell wrappers, MariaDB and
Redis, not mocked commands.

| Area | Verified |
|---|---|
| Existing sites | Master/tenant HTTP before and after tests; tenant backup and inspection |
| DocTypes | Second tenant with an independent child row; persisted action/status updates |
| Apps | Pending inspection, installation, version discovery, uninstall, reinstall, final uninstall |
| Sites | Suspension/maintenance mode, resume and HTTP recovery |
| Migration | Scoped migration with a pre-migration backup |
| Backup | Database/public/private archives; exact restore-directory output |
| Restore safety | Invalid/missing manifest rejected |
| Restore data | Deleted ToDo restored; later ToDo removed; public/private file contents recovered; encrypted secret readable after recovery of the original encryption key |
| Teardown | Only the uniquely marked fixture removed; final state idle/Archived |
| Events | Real app/tenant/node payload identities and completion statuses |

Independent checks confirmed removal of the fixture directory, database **and
database user**. Separate transactional Frappe checks verified unique child names
across tenants and rejection of duplicate apps on one tenant, leaving no records.

Persistent JSON report (inside the sandbox automation-log volume):

```text
/home/frappe/automation-logs/integration/036fd2c1a0-1792880311.json
```

The shell launcher errored *after* the successful JSON report because its source
was edited during execution. Long-running launches now use `exec` and do not
resume reading an edited script on completion. Results and cleanup were verified
independently rather than treating the launcher exit as a function failure.

The main live-discovered bug was `format:{parent}-{app_name}` naming children
before their parent had a name, producing duplicate `-dt_master` keys. New rows
use hash IDs; existing IDs are preserved. Per-tenant app uniqueness is validated.

Not covered: OS provisioning, systemd/Supervisor/Nginx configuration, mail,
shared app-code removal, Git upgrades or removal of the running bench. Those need
separate disposable apps/benches or a VM; these results are not production certification.
