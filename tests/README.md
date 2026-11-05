# Automation contract tests

From `apps/dt_master`:

```sh
python3 -m unittest discover -s tests -v
```

Requirements: Python 3.10+, Bash, Linux command-line utilities, and `jq`.
Tests that need jq are skipped with an explicit reason if it is unavailable.
No Frappe site or database is required. The suite substitutes an in-memory
Frappe module and uses `fixtures/automation_demo.json` for example DocTypes.
The IP address and domains are documentation/test-only addresses, not servers.

For real site/API integration tests, see [`sandbox/README.md`](sandbox/README.md).
That Docker project has its own databases, Redis, SSH runner, sites and backups;
it never mounts this workspace's live Bench data.

The tests cover:

- DocType Select values, action mappings, and real child-log field names;
- environment requirements for every app/site/node/mail wrapper;
- shell-safe passwords, JSON and paths with spaces; version/tag normalization;
- all four realtime event names and their identity/stream/status fields;
- concurrent stdout/stderr draining (including output exceeding a pipe buffer);
- site provisioning, suspension, resume and teardown transitions;
- mail configuration transitions and mailbox child-row updates;
- app inspection, installation, uninstall no-ops and failed actions;
- SQL argument forwarding and non-destructive admin credential verification;
- dependency failures, version arguments, validation and skip counts;
- fresh complete backups, original site encryption keys and site-scoped retention;
- update abort on failed backup, correct restore CLI flags, and database-safe removal.

Remote execution is mocked. Shell tests replace service/package/database/Bench
commands; filesystem fixtures live under a temporary directory (preferably
`/tmp/opencode`). They do **not** prove that Supervisor, Nginx, Postfix, Dovecot,
MariaDB or a real Frappe site will work with a particular server configuration.

Before production, use a disposable VM/bench to create two sites sharing an app,
install/uninstall it, back up both sites, migrate, restore records and public/private
attachments, then remove the bench. Confirm that the databases and the recovery
configuration files still exist after removal. Do not test teardown on production.
