# Approved preparation recovery checkpoint

This checkpoint is for `dalali.ndftechlabs.com`, not a traffic cutover and not a
full VM/mail backup. It does not authorize Docker installation or service changes.

## Why a scoped database pause is necessary

Preflight found 1,007 InnoDB and three MyISAM tables. A transaction-only dump is
not a consistent snapshot of MyISAM. The operator explicitly approved bounded
locks on **only Dalali's existing objects**, never a global server lock. A later
review identified 10 MariaDB sequences among those objects; ordinary READ locks
do not stop cached ID allocation. The operator separately approved exclusive
sequence locks after the post-pause health review.

`ScopedSchemaLock` uses its own site-user connection, strict identifier quoting,
5-second acquisition watchdog, 55-second hold watchdog and a 60-second idle-session
fallback. Timeout closes the owned transport and stops only owned dump processes.
Session lock/IO timeouts are local, not global MariaDB configuration changes.
Other schemas are not selected/locked. No table engines, rows or configuration
are changed by the locking procedure. Writes to Dalali and direct sequence
reads/allocations may wait during the dump. Base-table reads are not exclusively
locked. Sequence definitions/cache boundaries are read by the lock-owning session
and prepended to the dump; both ordinary tables and sequences must validate.

Local Docker rehearsal uses disposable InnoDB/MyISAM schemas and a scoped account:

```sh
docker compose --env-file tests/sandbox/.env -f tests/sandbox/compose.yaml \
  cp docs/migrations/same-vm-docker/checkpoint.py backend:/tmp/ndf-checkpoint.py
docker compose --env-file tests/sandbox/.env -f tests/sandbox/compose.yaml \
  cp tests/sandbox/rehearse_checkpoint_lock.py backend:/tmp/rehearse_checkpoint_lock.py
docker compose --env-file tests/sandbox/.env -f tests/sandbox/compose.yaml \
  exec -T backend env/bin/python /tmp/rehearse_checkpoint_lock.py
```

Verified: pilot MyISAM and InnoDB writes blocked; unrelated schema writable;
normal, watchdog and failed-dump release; actual scoped-account dump; matching
table-definition count and gzip CRC. Synthetic schemas/account are removed.

## Contents and protections

- Pilot SQL dump, exact site config/encryption key, common config, public/private
  files and Bench app-list metadata.
- All 21 app trees including existing changes and installed dependencies; native
  Bench/project environments, Bench configuration, deployed script directory,
  Nginx/Supervisor configuration and local custom fonts if present.
- Source revision/status fingerprints, collection-code checksums and a manifest.
- Disposable Python bytecode caches excluded. No host mailboxes, home SSH folders
  or DKIM key directories are selected. Selected source/config trees can contain
  sensitive application material: the archive must remain private/encrypted.

The server writes a newly allocated root-owned mode-700 checkpoint directory under
`/var/backups/ndf-migration`. It imports **only the approved public recipient** into
a fresh private public-keyring directory; private recovery keys stay off-server.
Root SQL credentials are not used: the pilot account/password are read locally,
never put in command arguments or exported in receipts. Dump diagnostics stay in
the private staging directory.

The lock is released **before** compression checking, source/file collection,
archive encryption and network transfer. Temporary SQL staging is removed only
after an encrypted archive and receipt have been finalized. Failed checkpoints
remain root-private for diagnosis and are never presented as successful backups.

The capture runs at low CPU/IO priority. Server free-space safety margin is 40 GiB;
gzip dump output is capped at 1 GiB and encrypted artifact at 8 GiB. No core dumps
are allowed for capped writer processes. Local destination needs 20 GiB free and
must be outside this repository, owned by the operator and mode 700.

This is deliberately marked **preparation only**. Source/files are captured after
the consistent DB dump, not under a single filesystem+DB snapshot. A fresh final
quiescent sync and restore rehearsal are still required before cutover.

## Dry run / explicit collection

```sh
python3 docs/migrations/same-vm-docker/collect_checkpoint.py \
  --audit /tmp/opencode/ndf-production-migration-audit.json \
  --public-key "$HOME/ndf-migration-key-exports/recipient-public.asc" \
  --destination "$HOME/backups/ndf-migration"
```

The default checks the public recipient and prints the plan; it takes no remote
lock or backup. Only an approved invocation with `--execute` performs collection.

After capture the collector saves a non-secret receipt locally before downloading
ciphertext. Transfer is finalized only after independent size/SHA-256 matching.
Interrupted transfers leave a `.partial`; inspect it before removing/retrying.
To fetch an existing completed checkpoint without repeating the DB pause, use:

```text
collect_checkpoint.py --fetch-receipt <actual-receipt-file> \
  --destination <approved-private-directory> --execute
```

Never paste literal placeholders. Use the exact receipt/path reported by the
collector. Do not recollect just because transfer failed.

## Operator decryption check (after transfer)

The agent does not open your private export or request its passphrase. Once the
exact archive path is supplied, run this in your own local interactive terminal,
substituting that verified file path:

```text
(
  set -e
  set -o pipefail
  gpg --homedir "$HOME/.gnupg-ndf-migration" --decrypt <actual-archive-path> | gzip -t
  echo 'CHECKPOINT DECRYPTION AND GZIP VERIFIED'
)
```

This consumes the entire decrypted stream for integrity verification without
writing plaintext files or printing customer data. Enter the passphrase only in
pinentry. Do not send private keys, passphrases or plaintext listings to chat.

Do not blindly extract this recovery tar into `/` or onto the workstation's live
paths: it preserves original paths, ownership metadata and symlinks. A future
restore uses a private reviewed staging area/container and selected members.
Cryptographic/gzip success proves integrity/decryptability, not application-level
restore correctness; the v16 rehearsal remains a separate gate.
