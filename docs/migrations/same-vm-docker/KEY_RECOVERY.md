# Generate and verify an operator-controlled backup encryption key

Run these commands in your **own interactive terminal on the local workstation**,
not on the production VM and not through chat. GPG is already available here.
No key generation, private-key export or production backup has been performed
by the migration agent at this stage.

## 1. Create private local directories

```sh
umask 077
KEY_HOME="$HOME/.gnupg-ndf-migration"
EXPORT_DIR="$HOME/ndf-migration-key-exports"
mkdir -p "$KEY_HOME" "$EXPORT_DIR"
chmod 700 "$KEY_HOME" "$EXPORT_DIR"
```

These paths are outside the code repository. If the keyring already contains
keys, inspect it before creating another; do not delete/overwrite existing keys.

## 2. Generate the key interactively

```sh
gpg --homedir "$KEY_HOME" --full-generate-key
```

Select:

- **RSA and RSA**, 4096 bits (both signing/certification and encryption subkey).
- Expiration: **1 year**, unless your retention policy requires another period.
- A recognizable identity, e.g. `NDF Production Migration Recovery 2026`.
  Email is optional; use only an address you control if supplied.
- A strong, unique passphrase entered through GPG's pinentry prompt. Save it in
  your password manager, separately from exported key files. Do not use a server,
  SSH, email or database password.

Never put the passphrase in a shell command, environment variable, script,
command history, Git file or chat message. Do not use a blank passphrase.

## 3. Identify and export the public recipient

```sh
gpg --homedir "$KEY_HOME" --list-keys --keyid-format long --fingerprint
FPR="PASTE_THE_FULL_NEW_KEY_FINGERPRINT_HERE"
gpg --homedir "$KEY_HOME" --armor --export "$FPR" > "$EXPORT_DIR/recipient-public.asc"
test -s "$EXPORT_DIR/recipient-public.asc"
gpg --homedir "$KEY_HOME" --show-keys --fingerprint "$EXPORT_DIR/recipient-public.asc"
```

Use the primary key's full fingerprint, without spaces. Compare it with the one
you just generated. Do not rely on
a short key ID. Ensure an encryption-capable `[E]` subkey is listed.

The public `.asc` file and full fingerprint may be shared with the migration
agent. This public key, not the private key, will be used to encrypt on the VM.

## 4. Export recovery material privately

```sh
gpg --homedir "$KEY_HOME" --armor --export-secret-keys "$FPR" > "$EXPORT_DIR/recovery-private.asc"
test -s "$EXPORT_DIR/recovery-private.asc"
chmod 600 "$EXPORT_DIR/recovery-private.asc"
```

This is a passphrase-protected **private key**. Do not upload, paste or commit it,
and never copy it to the production VM. Copy it to two trusted encrypted/offline
recovery locations under your control. Keep the passphrase separately. Also keep
the automatically generated revocation certificate:

```text
$KEY_HOME/openpgp-revocs.d/<FULL_FINGERPRINT>.rev
```

Do not import/publish the revocation certificate unless intentionally revoking the
key. It is recovery/security material, not the public recipient file.

The export directory is temporary local staging, not a substitute for independent
recovery copies. Avoid cloud-synced folders unless explicitly approved. Retain the
working private keyring with its passphrase protection; don't discard it until
offline copies are verified. Never keep the only recovery material solely beside
the encrypted production backups.

## 5. Prove that the exported key can recover a file

Use harmless test text, not production data:

```sh
printf 'NDF migration recovery test\n' > "$EXPORT_DIR/test.txt"
gpg --homedir "$KEY_HOME" --output "$EXPORT_DIR/test.txt.gpg" \
    --recipient "$FPR" --encrypt "$EXPORT_DIR/test.txt"

VERIFY_HOME=$(mktemp -d "$HOME/.ndf-keycheck.XXXXXX")
chmod 700 "$VERIFY_HOME"
gpg --homedir "$VERIFY_HOME" --import "$EXPORT_DIR/recovery-private.asc"
gpg --homedir "$VERIFY_HOME" --output "$EXPORT_DIR/test.restored.txt" \
    --decrypt "$EXPORT_DIR/test.txt.gpg"
cmp "$EXPORT_DIR/test.txt" "$EXPORT_DIR/test.restored.txt" \
    && echo 'RECOVERY TEST PASSED'
```

The fresh keyring makes the test use the exported private key, not just the
original keyring or its cached passphrase. Enter the passphrase only in pinentry.
For stronger recovery assurance, repeat import/decrypt on another trusted offline
device using one of the actual offline copies.

After successful verification, remove only the temporary verification keyring:

```sh
case "$VERIFY_HOME" in
    "$HOME"/.ndf-keycheck.*)
        gpgconf --homedir "$VERIFY_HOME" --kill gpg-agent
        rm -r -- "$VERIFY_HOME"
        ;;
    *) echo 'Unexpected verification path; not removing anything' ;;
esac
```

Do not delete the working keyring or the offline recovery copies. Ordinary file
deletion is not guaranteed secure erasure on SSDs/snapshots; use full-disk/encrypted
storage for key material. Keep old private keys for as long as any backups need
them. Key expiration does not prevent decrypting old backups, but renew the public
key before using it for new backups after expiry.

## What to send back

- Full public fingerprint.
- Local path to `recipient-public.asc`.
- Confirmation that the exported-key recovery test passed and offline recovery
  copies/passphrase are safely stored.

Do **not** send the private export, keyring, revocation certificate or passphrase.
Production collection/transfer still requires approval of the exact backup step.
