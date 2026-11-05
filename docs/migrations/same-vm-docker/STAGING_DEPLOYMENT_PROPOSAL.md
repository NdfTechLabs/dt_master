# d-stage guarded deployment proposal — approved; readiness portion deployed

**Subsequent decision:** operator selected **Approve guarded staging**. Private
readiness gateway and key refresh were deployed/tested within this scope; separate
connector remains inactive and no public/clone route is enabled. Exact result and
remaining credential/action-gateway gates: STAGING_READINESS_RESULT.md.

## Supplied Access identifiers / preparation evidence

- Host: `d-stage.ndftechlabs.com` (confirmed; the other domain shown during
  dashboard setup was a mistake, not a requested route).
- Issuer: `https://ndftech.cloudflareaccess.com`.
- Application AUD: `d3ed2dd29ed2ac1e2f0f4d9b49f1e05da29d76bfaafad1c6740f87aadd61ddaa`.
- Sole reviewer: operator-supplied exact email, retained only in VM private config.
- Operator-provided dashboard JSON has HttpOnly enabled and a 24-hour session.
  One hour was recommended, but no reduction has been silently imposed. The token
  validator supports the configured 24 hours and rejects longer token lifetimes.

VM public-JWKS discovery at 2026-10-26 01:54:14 UTC fetched **5,116 bytes**, exactly
the configured HTTPS URL with no redirect, two public RSA signing keys, SHA-256
`6a8b603ab94843de1f4ae8178f0b5b62db13016ba1c701bf610751b4f707ecb8`.
Metadata/config/receipt are root-private under `rehearsal-v1/stage-v1/`,
`origin_enabled: false`. No listener/service/route was started or clone data read.
This confirms public team metadata, **not** Access application/policy correctness.

`stage_access.cjs` is a pure, local preparation component, **not a complete public
gateway**. Its 14 synthetic Node tests cover fixed issuer/host, exact audience and
email, RS256 signature, expiry/future times, 24-hour lifetime, bounded fresh signing
keys/rotation, malformed tokens, algorithm/key-URL confusion and duplicate headers.
It never fetches from token-supplied URLs or trusts unsigned identity headers.
The reviewer address, real bearer assertions and credentials are not in source/tests.

## Exact approved scope

1. **Gateway:** new rootless container `ndf-dalali-stage-gateway-v1`, using the
   already verified exact-runtime image with `--pull=never`, UID/GID 1000,
   readonly root, capability-drop ALL, no-new-privileges and default seccomp.
   Maximum **96 MiB RAM / 0.25 CPU / 64 tasks**, bounded tmpfs **16 MiB**. Attach
   only to the existing internal rehearsal network; no external/default-route
   application network, public Docker bind or Docker/RootlessKit socket mount.
   Operator-controlled RootlessKit mapping exposes only VM **127.0.0.1:18089**,
   after exact ownership/conflict checks; preserve existing 18088 mapping.
2. **Connector:** new `ndf-dalali-stage-tunnel.service`, separate from existing
   `cloudflared.service`. Reuse root-owned `/usr/local/bin/cloudflared` 2026.3.0,
   autoupdate disabled. Maximum **128 MiB RAM / 0.25 CPU / 128 tasks**, swap off.
   Run non-root as the dedicated rootless account in **user-5001.slice**, so the
   existing aggregate 2-CPU/3-GiB/256-MiB-swap/1024-task cap still applies; verify
   actual cgroup membership before accepting startup. No host/application policy
   relaxation or capability grant. Readonly filesystem/hardening, private tmp,
   no clone/source data or Docker socket access. One new tunnel, only d-stage,
   only the guarded loopback origin; no private-network routes or raw frontend.
3. **Public signing-key refresh:** separate bounded unprivileged one-shot/timer
   for the fixed team HTTPS endpoint, every **15 minutes**, response cap **64 KiB**,
   timeout/redirect checks and validated atomic public-key snapshots. Gateway
   accepts snapshots for at most **1 hour**, fails closed on stale/unknown keys,
   never gains Internet egress to fetch keys itself. Timer runs within the same
   aggregate slice, maximum **32 MiB / 0.1 CPU / 16 tasks**, no app secrets/mounts.
   Expected key traffic at observed size is ~0.5 MB/day; bounded body maximum is
   6 MiB/day, plus TLS/HTTP overhead. Tunnel/reviewer traffic depends on use.
4. **Files/credential scope:** new stage-only code/config/unit paths, retained
   private receipts and a new tunnel credential only. No overwrite of existing
   native/shared connector configuration or site/DB/volume. Load tunnel credential
   privately via systemd credentials/token-file, never shell argv, chat, command
   history, source control or logs. Never use the dashboard's generic service
   install command or an existing SSH tunnel token.
5. **Disk/tool budget:** no package, image, npm/pip/apt or browser downloads.
   Stage code, key snapshots and bounded receipts: maximum **64 MiB added**, still
   within the existing **1-GiB UI subcap / 8-GiB rehearsal cap**, **60-GiB free floor**.
   No resource-budget increase. Stop if headroom, an additional dependency or a
   wider policy change is needed; never clean up retained artifacts implicitly.

Launch/unit files and readiness-only gateway were implemented/reviewed/tested.
The full clone-capable action gateway still requires implementation and exact-code
proof before clone connection. If the supported systemd/
RootlessKit/connector behavior cannot satisfy these limits, stop rather than
silently substitute another identity, networking mode, path or policy.

## Access/action contract before customer data

- Cloudflare Access: entire d-stage hostname/all paths, exactly one email, verified
  IdP/email PIN, no Everyone, Bypass, service-token or secondary unprotected route.
  Enable tunnel **Protect with Access** with the supplied team/AUD.
- Gateway independently validates signed JWT, exact issuer/AUD/email, bounded
  session and current signing keys. Missing/invalid identity must never reach even
  login/assets/customer-page endpoints. Unknown hosts fail closed.
- Reverse gateway must not reuse the UI forward proxy's broad fallback GET rule.
  Review exact paths/methods/parameters before allowing them. Reject ambiguous
  duplicate arguments, custom-query dispatch, unsafe GET/RPC overrides, arbitrary
  dynamic Page/document methods, open CONNECT and unsupported WebSocket frames.
- Begin **read-only except required clone login/logout**. All business saves,
  posting, payments, checkout, stock/payroll/disbursement, messages/webhooks,
  backup/restore/deployment and source actions denied. Existing implicit-write
  recommendation path remains denied. A synthetic Note exception is not part of
  this initial public-review proposal.
- Exact origin checks, header/cookie handling, internal/public host and HTTPS
  mapping, redirects and Socket.IO namespace must be tested with synthetic content;
  never fix incompatibilities by allowing all methods/origins or disabling checks.
- No shared cache of authenticated data, no search indexing, explicit staging
  warning; no PII/raw request/console/cookie/JWT logs or public diagnostic bodies.
- Reviewer must use a separately reviewed clone-only login/roles; no reuse or
  publication of native/test Administrator credentials. Welcome mail/integrations
  and clone workers/scheduler stay disabled. Access login is not Frappe login.

## Manual-browser boundary requiring explicit agreement

The operator has approved authorized browsers receiving clone pages/data. An
ordinary Internet-connected browser cannot inherit the VM test browser's network
namespace/proxy fence. Preserve restrictive staging CSP and block unsafe staging
actions/redirects, but do **not** promise every external top-level navigation can
be prevented. Manual reviewers must stay on staging and avoid provider/customer
links/actions. If a hard browser-wide network fence is required, stop and design
a separate VM-resident interactive-browser solution instead. Existing automated
tests retain their original external/native/provider-destination blocks.

## Deployment ordering / remaining operator action

1. Obtain exact scope approval. Implement and prove gateway policy with synthetic
   keys/upstream before credentials or clone data. Verify protected native health,
   resource/disk headroom, no listeners/conflicting ownership and no source changes.
2. Account administrator creates **a new staging-only tunnel**, not an existing
   tunnel modification. Place its connector credential privately on the VM using
   the prepared hidden-input procedure. Do not paste token/install command into
   chat and do not publish an origin route yet. A tunnel ID is safe to share.
3. Start only the new guarded services with clone upstream **disabled**. Audit
   Access application/policy settings; configure only d-stage via new tunnel,
   Protect with Access enabled, no blanket path/hostname bypass.
4. Prove public anonymous/wrong-identity denial and authorized operator login
   against synthetic readiness content first. Never treat a redirect to login as
   proof that the authorized policy and origin assertion checks are correct.
5. Only after access/action tests and clone reviewer-login scope are verified,
   enable the existing clone upstream behind the gateway. Recheck all protected
   native/config/PID/HTTP/resource baselines. Native Dalali remains maintenance=1.
6. Any failure: disable/close **only the new staging route/service**; retain files,
   secrets/configs/receipts for review. No deletion/recreation of clone deployments,
   volumes or existing services, and no production switch/write-open/cleanup.

**Status: scope separately approved; readiness/key refresh and the separate
connector active. Operator saved route; received version 2 audited. Public
readiness anonymous denial verified; permitted-browser login pending. Clone
upstream remains disabled. No existing application policy/native/shared service
change. See STAGING_CONNECTOR_RESULT.md.**
