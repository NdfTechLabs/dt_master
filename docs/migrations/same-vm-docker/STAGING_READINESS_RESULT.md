# d-stage readiness gateway — private only, clone not connected

**Subsequent status:** operator saved the protected route and the separate
connector is now healthy. Actual received config was verified; public anonymous
requests are denied. **Only static readiness is connected, not clone pages.**
See STAGING_CONNECTOR_RESULT.md; operator permitted-browser login remains pending.

Operator selected **Approve guarded staging**, including the separate service/
security/resource scope and normal-reviewer-browser navigation boundary in
STAGING_DEPLOYMENT_PROPOSAL.md. This does not authorize production cutover,
write-open, unsafe business changes or removal of existing services/deployments.

## Executed

- New rootless `ndf-dalali-stage-gateway-v1` runs as UID/GID 1000, readonly root,
  no capabilities, no-new-privileges, default seccomp, 96-MiB RAM/no swap,
  0.25 CPU/64 tasks, existing internal-only network, 16-MiB tmpfs. No public
  Docker port, app/site/DB credential mount or socket mount.
- Owned RootlessKit mapping added only at VM **127.0.0.1:18089**. Existing 18088
  frontend mapping preserved; new gateway uses a distinct child port 18089.
- Code-bound VM no-data proof passed in a separate network-none constrained
  container using synthetic RSA keys/identities and HTTP requests only. Real
  Access config, clone data and credentials were not mounted into that proof.
- Actual gateway denies anonymous loopback requests with **403**. It validates
  signed Access identity using the configured team/AUD/exact reviewer and a fresh
  public-key snapshot before serving even readiness content.
- **Readiness-only implementation has no upstream forwarding code**. Valid
  identity can receive only static readiness text or robots.txt; `/login`, assets,
  APIs, legacy RPC, writes and all WebSocket/CONNECT requests are denied. Enabling
  `origin_enabled` by config causes startup rejection, not clone exposure.
- New bounded `ndf-dalali-stage-keys.service` completed successfully and its timer
  is active (15-minute refresh, one-hour maximum accepted snapshot age). Fixed
  HTTPS endpoint, no redirects/arbitrary URLs, 64-KiB response cap, atomic public
  snapshot, disk/free-floor checks; no app credentials/data access.
- Separate `ndf-dalali-stage-tunnel.service` definition installed but **inactive
  and not enabled/started**. Existing `cloudflared.service` and its config remain
  unchanged. New service requires a new private token and a separate approval
  marker; neither was created by readiness setup.
- Local validation: **165 Python suite tests passed**, including the synthetic
  Node identity/HTTP tests; Node syntax and `git diff --check` passed.

Code/private config under `/opt/ndf-rootless/dalali-stage-v1/`. Public-key output
only under `/var/lib/ndf-dalali-stage-keys/`. Empty root-private credential directory
prepared at `/etc/ndf-dalali-stage/`; no token was read, printed or created.
Root-owned runtime config parent permits only the dedicated group to traverse;
source tests/docs contain no real reviewer email, token or private keys.

## Latest receipt (2026-10-26 02:11:53 UTC)

- Signing-key refresh: success; snapshot age 41 seconds at receipt.
- Key refresher: non-root `dt-containers`, `user-5001.slice`, 32 MiB/16 tasks.
- Inactive connector: same account/slice, 128 MiB/128 tasks. Both service units
  have no capabilities, hardened filesystem/home access, zero swap and no Docker
  socket access; their slice retains the original aggregate limits.
- Stage-added disk: **1,134,592 bytes (~1.08 MiB)**, below 64-MiB subcap.
- UI-added disk: **431,603,712 bytes (~0.40 GiB)**, below 1-GiB cap.
- Rehearsal-added disk: **4,292,538,368 bytes (~4.00 GiB)**, below 8-GiB cap.
- Host free: **85,492,715,520 bytes (~79.62 GiB)**, above 60-GiB floor.
- Native HTTP unchanged: Dalali 503, Demo 200, Dulaman 503, ERP 200. Protected
  native config/service/PID/maintenance, aggregate resources and app egress guards
  passed after setup. No native/source migration, pause, Nginx/TLS change or cleanup.

No binary/image/package downloads. Public signing-key metadata only was fetched;
ongoing tunnel traffic remains zero because the new connector has not started.

## Remaining account-side action: new tunnel credential

1. Cloudflare dashboard **Networking → Tunnels → Create a tunnel** (older menus
   may show Zero Trust Networks/Connectors). Name a **new** tunnel `dalali-stage`.
2. Do **not** run the suggested generic service-install command: it may replace
   the existing SSH connector. Do not select/reuse an existing tunnel or token.
3. Copy the new tunnel UUID (safe identifier). From the shown connector command,
   copy only the secret after `--token`, not the whole command. Do not post it here.
4. On an interactive VM terminal, use the installed hidden-entry helper:

   ```sh
   ssh -t ssh.ndftechlabs.com 'sudo -n python3 /opt/ndf-rootless/dalali-stage-v1/stage_store_token.py --tunnel-id YOUR_NEW_TUNNEL_UUID'
   ```

   Replace the placeholder with the new UUID. Paste only the token at the hidden
   prompt. The helper verifies token UUID/account/secret structure, rejects reuse
   of a UUID found in shared connector config, and refuses to overwrite existing
   files. It writes root-only mode-600 credential/receipt files, prints only safe
   status/UUID, and **does not start a service**. Do not pipe a token through a
   command line, shell history or chat; no native/private recovery keys are needed.
5. Report the new UUID and that the credential was stored. Leave public/DNS routes
   unconfigured until the isolated connector/account Access checks are ready.

## Before any clone publication

The connector must use only the new tunnel and whole-hostname Protect with Access,
then prove public anonymous/wrong-identity denial and the operator's permitted
login against **static readiness content first**. Access policy cannot be verified
from public keys alone. No signed real token/cookie should be exported for tests.

A clone-capable reverse action gateway and clone-only reviewer-login/role scope
still need implementation/review and fresh exact-code no-data proofs before clone
credentials/data are connected. Existing app bugs can remain staging findings;
do not hide them by allowing implicit Customer/Contact/cart writes. Automated UI
acceptance is still blocked, not superseded by readiness tests.

VM evidence: `rehearsal-v1/stage-v1/readiness-result.json`,
`readiness-health-receipt.json`, private journal `state.json.staging` and synthetic
test output in private `commands.log` (do not dump raw unrelated logs).
Local sanitized logs: `stage-readiness-token-helper-20261026.log` and
`stage-readiness-receipt-20261026.json` under the private healthchecks directory.
Latest proof source SHA-256:
`1d319336d73556c57fe770cfb848eeca6856ba115b98546aeb306e5f0bdcee50`.

**Public URL is not live. Clone upstream remains disabled.**

### Subsequent credential-entry troubleshooting (02:31 UTC)

Operator reports hidden-entry errors and running `cloudflared tunnel run --token`
instead. No raw token was supplied in chat. Read-only VM audit found only the
original shared cloudflared process/service, no unmanaged or staging connector,
and no staging credential file/receipt. The command may have run elsewhere or
exited; do not infer its location/success or kill the shared connector.

DNS for d-stage now resolves (account-side change not performed by this agent).
Unauthenticated public HEAD returned 403, not a redirect to the expected team;
no body, cookie or redirect query was read/exported. This is **not** proof of the
Access application/policy or a working staging tunnel. Managed connector remains
inactive and this gateway still cannot forward clone content. Need the command's
location and exact sanitized hidden-entry error before changing validation or
attempting credential storage again. No secret/process arguments were inspected.

### Safe helper diagnostics added (02:38 UTC)

Operator confirmed the foreground connector ran on the workstation, not VM, and
provided the helper's generic rejection for requested staging UUID
`9388ab42-7fa1-429c-a21b-bca54d301486`. This differs from the earlier screenshot's
UUID; a token/UUID mismatch is possible but **has not been diagnosed**.

Reviewed public cloudflared 2026.3.0 source: outer standard-base64 JSON, `a` account,
`t` UUID, `s` encoded secret, optional `e` endpoint. Updated only the owned helper
to distinguish clipboard whitespace/quotes/whole-command/terminal escapes,
encoding/JSON, UUID mismatch, account format and secret format/length failures.
No existing format/length, protected-shared-UUID, exact requested UUID or
no-overwrite check was relaxed. It may print only parsed canonical tunnel UUIDs
on mismatch, never token/account/secret contents or decoder exceptions.

Local suite **167 passed**. VM update required the exact previous helper SHA-256,
retained its original root-private bytes, and verified unchanged identity/HTTP
gateway sources plus native health/egress guards. New helper SHA-256:
`946967862b444a6100e42ab9efb78b08a7a53d5330f75726f82f46a15a89839d`.
The prior readiness bundle proof above is historical; gateway sources did not
change, and helper diagnostic tests are recorded separately. Requested UUID is
not present in shared connector config. No real token/argv read, credential
stored, service/route changed or connector started. The operator must retry the
hidden input once and share only the safe diagnostic category if it still fails.

### Connector-secret minimum corrected (02:43 UTC)

Operator's next attempt returned `[secret-length]`, identifying the rejecting
check without exposing any credential. Found a helper compatibility bug: it
required **exactly 32 decoded bytes**, whereas Cloudflare documents a minimum of
32 and the installed-version cloudflared parser accepts a byte slice without an
exact-32 requirement. No real token/decoded length was inspected; this diagnostic
alone does not distinguish a too-short secret from a valid longer one.

Changed only exact-32 to **at least 32**, retaining the existing 16-KiB outer-token
cap, exact UUID/shared-tunnel protection, account/encoding checks, hidden TTY,
root-only storage and refusal to overwrite or start services. Synthetic cases
32/48/64/128/512/8192 bytes pass; 0/1/16/31 and over-cap tokens remain denied, and
wrong/shared UUID checks still apply to all longer cases. Local suite **168 passed**.

Applied only after exact previous-helper hash/ownership checks, retaining that
version root-private. Verified unchanged gateway sources and protected native
health/egress. New helper SHA-256:
`2b54350bc00b5fbeb5d538ebeda2e8f013a7a655c2e1a4b9a4c75055d82fc71c`.
No real credential read/stored, service/route changed or connector started. Needs
operator retry at the hidden prompt, never a token pasted into chat.

Reference: https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/methods/create/

### Credential receipt verified; connector still gated (02:47 UTC)

Operator's corrected hidden-input attempt succeeded for new staging UUID
`9388ab42-7fa1-429c-a21b-bca54d301486`. VM preflight verified only credential
file metadata and the safe UUID/status receipt: regular root-owned mode-600 file,
single link, bounded size, exact requested new UUID, distinct from protected
shared configuration. **No token content or process arguments read/exported.**

Readiness source/unit hashes verified, anonymous loopback still 403, public keys
fresh (356 seconds), gateway still cannot forward clone data. New connector is
inactive, approval marker absent, metrics port unused; aggregate memory headroom
~930 MiB and host free ~79.63 GiB. Native health/maintenance/config/PID/resource
and application egress guards passed. No service/route action performed.

Remaining pre-start gate: confirm the new tunnel's exact published route is only
`d-stage.ndftechlabs.com` → **http://127.0.0.1:18089**, all paths, Protect with
Access enabled using the supplied team/AUD. Remote-managed connector config can
target other host services, so the token receipt alone is not sufficient to start
it. Never use raw frontend 18088 or change the existing SSH tunnel. If a conflicting
DNS/route already exists, inspect/ask instead of deleting or overwriting it.
Then start only the new managed connector for static readiness authentication;
clone action gateway/login/role gates still remain afterward.

VM receipt: `stage-v1/credential-preflight.json`; local sanitized receipt:
`healthchecks/stage-credential-preflight-20261026.json`.
