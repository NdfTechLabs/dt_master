# Dalali protected staging URL — preparation only

**Update:** operator supplied team/AUD and separately approved the exact guarded
service/security scope. Private readiness gateway and bounded key refresh are
running; `origin_enabled: false`. Connector remains inactive pending the new
tunnel credential/account checks. No public route yet. See
STAGING_DEPLOYMENT_PROPOSAL.md and STAGING_READINESS_RESULT.md for precise executed
scope, evidence and remaining clone/publication gates.

## Operator decisions

- Use **https://d-stage.ndftechlabs.com** for Dalali staging, not production.
- Initially permit only the operator's explicitly supplied email. Keep the exact
  address in private deployment/Access configuration, not committed source/docs.
- More reviewers may be explicitly added later without changing the hostname.
  Do not authorize an entire email domain or Everyone to anticipate that change.
- Operator explicitly permits authorized reviewers' browsers to receive cloned
  pages/customer data, replacing the earlier VM-only viewing restriction for
  those reviewers. This is not permission to export backups, passwords, cookies,
  raw logs or customer-name lists, or publish screenshots/traces.
- Existing application bugs may be reviewed; complete UI acceptance is not a
  prerequisite for a restricted staging review. Do not mislabel staging exposure
  as business sign-off, final synchronization, public production cutover or write-open.

## Read-only discovery (2026-10-26)

- Host Nginx configuration validates; no d-stage server name is configured.
- d-stage does not resolve in the VM's DNS lookup at audit time.
- Dalali's existing origin certificate does **not** cover d-stage. Do not reuse it
  with TLS verification disabled or modify its renewal merely to add staging.
- Existing cloudflared service is active, using `/etc/cloudflared/config.yml`.
  Leave its configuration, routes, credentials and service untouched: it provides
  an existing protected path, not an expendable staging connector.
- Installed `/usr/local/bin/cloudflared` is **2026.3.0**; its run command supports
  `--token-file` and `--no-autoupdate`. No upgrade/download was performed.
- No Cloudflare account API/Access administrative visibility has been established;
  do not search for or reuse unrelated private control-plane credentials.
- Clone technical rehearsal passed; UI/business acceptance remains blocked.
  Native Dalali maintenance remains enabled. ~79.63 GiB host free at discovery.

Private audit receipts: VM `ui-v1/stage-readonly-audit.json` and
`ui-v1/stage-connector-capabilities.json`; local sanitized
`healthchecks/stage-readonly-audit-20261026.json`.

## Proposed routing — requires exact deployment/security approval

```text
reviewer HTTPS browser
  -> Cloudflare Access (exact email, deny by default)
  -> NEW staging-only Cloudflare Tunnel
  -> NEW loopback-only guarded staging gateway
  -> existing private rehearsal frontend / clone
```

Do not route a public hostname directly to `127.0.0.1:18088`. The current UI
forward proxy applies only to the automated VM browser; exposing the frontend
would bypass its action policy. Build/review/test a reverse gateway suitable for
untrusted HTTP clients before forwarding any authenticated customer-page request.

Candidate gateway loopback port: **18089**, subject to conflict/ownership checks.
No inbound host firewall opening, new public Docker port, origin Nginx vhost,
origin certificate, host Nginx reload or existing tunnel restart is proposed.
Public TLS is terminated at Cloudflare and carried over the separate tunnel to
loopback HTTP; TLS validation for Cloudflare connections must remain enabled.

The new gateway must fail closed on missing/invalid Access assertions, exact host
mismatch, unreviewed API methods, unsafe RPC/GET dispatch, custom-query handlers,
business writes and unsupported WebSocket frames. Verify signature, issuer,
application AUD, expiry and exact reviewer identity; never trust an unsigned email
header as authentication. Tunnel **Protect with Access** is an additional required
layer, covering the entire hostname/all paths, including assets and WebSockets.
No bypass/service-token/Everyone policy or unprotected secondary origin route.
Handle signing-key rotation with bounded public-JWKS retrieval and fail closed on
unavailable/unknown signing keys; no token/cookie/request-body logging.

Use a separate hardened connector service and the already installed binary,
with autoupdate off. It necessarily makes outbound connections to Cloudflare;
that is a **new staging-edge network role**, not permission to give the application
containers an external/default-route network. Keep existing rootless aggregate
limits, 8-GiB rehearsal cap, UI disk subcap and 60-GiB free floor. Plan exact service
identity/cgroup/hardening/resource allocations before starting anything. No Docker
socket, source credentials or clone data mounts in the connector. No broad host
AppArmor/seccomp/sysctl change or new capabilities.

No new binary/image/npm/pip/apt download is currently planned. Public documentation,
Access metadata/JWKS and tunnel protocol traffic are not application/tool artifact
downloads; estimate any additional package need and ask before downloading.

## Review scope / limitations

- Keep an explicit staging banner and no-index/no-shared-cache behavior.
- Start read-only apart from necessary clone login/logout. A marked safe Note
  exception can be separately retained/reviewed; do not permit general saves.
- Recommendations remain blocked: their party/price path can modify existing
  Customer/Supplier links or insert Customer/Contact. No unsafe allowlist expansion
  to make an incomplete app appear complete. Existing JS issues remain visible as
  staging findings, not falsely successful business results.
- No real checkout, posting, stock/payment/payroll/disbursement, messages, webhook,
  backup/restore/deployment, source account changes or app/runtime upgrades.
- Provision/review a clone-only reviewer login and exact roles before opening.
  Access identity is not automatically a Frappe session. Do not reuse or publish
  native credentials or the automated test Administrator password. Welcome mail,
  integrations, clone workers/scheduler and external server egress remain disabled.
- An ordinary reviewer browser is **not** the VM's network-isolated test browser.
  Origin CSP/gateway checks can constrain requests to staging, but cannot promise
  to prevent every external top-level navigation in the reviewer's normal browser.
  Present this boundary explicitly; agree manual-review navigation rules or use
  a separately reviewed VM-resident interactive-browser approach if the stronger
  browser-wide network boundary remains required. No silent CSP/security changes.
- No downloaded archive decryption, fresh production snapshot or promotion of the
  test-mutated clone. Native maintenance and other sites/mail stay unchanged.

## Account-side setup required before any route

An authenticated Cloudflare account administrator must first create an Access
application; no account credentials/API tokens should be pasted into chat.

1. Cloudflare dashboard: **Zero Trust → Access controls → Applications → Create
   new application → Self-hosted and private → Add public hostname**.
2. Name: **Dalali staging**. Domain: **d-stage.ndftechlabs.com**, all paths.
3. Allow policy: **Emails**, exactly the operator-supplied address. No Everyone,
   domain-wide access, Bypass or Service Auth. Deny other identities by default.
4. Use an available verified-email identity provider or email one-time PIN. An
   appropriate initial session duration is **1 hour**; do not weaken other apps.
5. Save, then provide only the **team domain** (`<team>.cloudflareaccess.com`) and
   the application's **AUD tag** from Additional settings. These are identifiers,
   not passwords/tokens. Do not provide a browser Access JWT or cookie.
6. Prepare a **new** staging-only tunnel, without changing/starting the existing
   connector or routing d-stage to the raw frontend. Connector credential placement
   must occur privately on the VM after exact deployment approval. Do not run the
   dashboard's generic service-install command, which could replace the existing
   cloudflared service. Do not publish a route until the guarded origin is ready.

Official references reviewed:
- https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/
- https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access

## Publication gates / rollback

1. Exact gateway/connector/service/identity/network/budget plan approved; Access
   app identifiers supplied; original protected health/config baselines checked.
2. New loopback-only gateway on synthetic upstream: prove authentication and
   origin/method/parameter/body/WS/header/redirect defenses before customer data.
3. Audit Access app/policies, key rotation and entire-hostname Protect with Access.
   Verify missing, spoofed, expired, wrong issuer/AUD, wrong-email and direct-origin
   requests receive no clone content. Keep a default deny/404 for unknown hosts.
4. Configure only the new hostname/tunnel route. Verify anonymous public denial
   before connecting clone upstream; operator verifies permitted email login.
5. Verify guarded clone rendering and staging warning, no business side effects,
   protected native health/PIDs/HTTP and resource floors. No public PII diagnostics.
6. If access or containment fails, close/disable only the staging route/service;
   retain exact configs/artifacts for diagnosis. No deployment/volume removal,
   production rollback or blanket cleanup. Keep other tunnels/sites untouched.

**Current state: separately approved private readiness services only. No DNS,
public tunnel/clone route, shared connector, native Nginx/TLS/service or production
write-open change applied.**
