# d-stage connector connected — readiness only

**Later status:** operator corrected DNS, confirmed signed-in readiness and asked
to connect the existing clone. Guest homepage preview is active with read-only
HTTP mode and a narrow reverse action guard. See STAGING_GUEST_REVIEW_RESULT.md;
login/Desk/cart/private data and unreviewed routes remain blocked. The readiness
steps/results below are historical, not a claim that clone forwarding is still off.

**The new staging connector is healthy. Clone/customer pages are NOT connected.**
Operator browser login to static readiness content remains the next gate. Do not
mistake connector health or anonymous denial for complete business/UI acceptance.

## Authorization and execution

Previously approved exact guarded staging scope; operator then saved the one
published application route and supplied settings showing Access enabled, team
`ndftech`, expected AUD, and guarded HTTP origin. Subsequent screenshot confirmed
route saved with three customized Access settings. No additional exposure scope
was inferred from that confirmation.

Added dry-run-default `stage_connect.py` requiring explicit `--route-confirmed`,
guarded remote handler and pure received-route validator. Connector token content
is never read by these scripts: only root-only file metadata and safe exact-UUID
receipt are checked. Systemd provides the token privately to cloudflared through
LoadCredential/token-file, not shell argv or logs.

Only `ndf-dalali-stage-tunnel.service` started/enabled. Existing connector config,
service and process were not changed. Exact source/unit/config and owned gateway
identity, private credential metadata, fresh public keys, anonymous loopback denial,
disk/free floor and aggregate memory headroom were checked before start.

## Received configuration verified, 2026-10-26 03:05:35 UTC

The running connector's bounded, loopback-only `/config` metadata was parsed on VM
without exporting or persisting raw provider configuration. Actual received
**version 2** contains exactly:

| Property | Verified value |
|---|---|
| New tunnel UUID | `9388ab42-7fa1-429c-a21b-bca54d301486` |
| Host | `d-stage.ndftechlabs.com`, all paths |
| Service | `http://127.0.0.1:18089` |
| Origin Protect with Access | Required |
| Team | `ndftech` |
| AUD | Exactly the previously supplied staging application AUD, no extra audiences |
| Fallback | Unknown hosts return `http_status:404` |
| Private/WARP routing | Disabled |
| TLS bypass / SOCKS / bastion | No bypass/proxy-mode expansion |

`/ready` reported 200. Actual service cgroup belongs to
`/user.slice/user-5001.slice/`, non-root UID 5001, zero effective capabilities,
128-MiB memory maximum, no swap, CPU quota no more than 0.25, 128-task unit limit;
original aggregate rootless limits retained. Metrics bound only at
**127.0.0.1:18090**. Shared cloudflared MainPID unchanged. Protected native health,
configuration/PIDs/maintenance, aggregate disk/resources and app egress guards
passed after startup.

Public anonymous HEAD returned **403**, without reading a body, exporting cookies
or following redirects. It was not an expected-team login redirect; do not infer
the exact edge denial reason or full Access allow-policy correctness from this
status. Actual operator browser authentication is still required. The gateway
independently checks signed issuer/AUD/email before serving any readiness content.

No package/image/binary download, source pause/data/schema change, host Nginx/TLS
change, original service restart, archive decryption, production route/write-open
or cleanup. Clone upstream remains hard-disabled and cannot be enabled by config.

## Failure handling / validation

If received config is malformed, differs from the exact route/AUD/team/fallback,
enables private/proxy routing, exits, times out, escapes limits or public anonymous
denial fails, activation stops **only the new connector**. Existing services are
never used as rollback targets and no credential/config/volume is deleted.

Local suite **171 tests passed**; added route-mutation/negative, inert-import and
explicit-route-confirmation checks; `git diff --check` clean. Pure policy tests
reject raw frontend 18088, wrong hostname/path/team/AUD, extra routes, weaker
Access, private routing and proxy/TLS-bypass changes. Original token/parser and
synthetic identity/HTTP tests retained. A remote-managed route must not be changed
after this audit without review; do not bypass the gateway while debugging.

## Next gate

Operator opens **https://d-stage.ndftechlabs.com/** in a normal browser and signs
in using the sole approved reviewer identity. Expected static response:

```text
Dalali staging: access verification only.
Clone pages are not connected yet.
```

Report only whether this text appears or the non-secret error category. No Access
JWT/cookie, token, raw page trace or customer screenshots are needed. Unexpected
403/login loops are investigated without disabling Access or expanding policies.

After that, implement/review the clone-capable reverse action gateway and exact
clone reviewer-login/role scope, with fresh synthetic no-data/hash-bound proof
before credentials/customer data. Existing bugs can be reviewed behind that
gateway; neither readiness publication nor login grants production cutover or
business sign-off. Final sync/writer fencing/cutover/write-open remain separate.

VM evidence: `rehearsal-v1/stage-v1/connector-result.json` and private
`state.json.staging`. Local sanitized operation receipt:
`healthchecks/stage-connect-readiness-20261026.log`.

## Follow-up: browser received native missing-site page (03:09 UTC)

Operator screenshot showed `d-stage.ndftechlabs.com does not exist`, not readiness
content. Read-only VM diagnosis reverified received config version 2, exactly the
protected 18089 route, connector `/ready` 200 and local gateway's marked/static
anonymous 403. An unknown-host request to native Bench on loopback 8081 reproduced
the exact missing-site message; no existing site credentials/data were requested
or exported. Gateway source has no clone/native forwarding or missing-site page.

Public proxied DNS returns Cloudflare addresses and hides the configured CNAME.
VM anonymous HEAD/GET metadata returns Cloudflare 403 without gateway marker or
expected-team redirect; no public body/cookies/queries exported. These facts do
not distinguish a wrong DNS target, account-side routing override or stale browser
response. Do not claim the exact cause or fix it by creating a native Frappe site,
adding host Nginx routing, disabling Access or bypassing the guarded gateway.

Next: operator tries a fresh private window and checks Cloudflare DNS record
type/name/target/proxy status for this exact hostname. Expected route target is
`9388ab42-7fa1-429c-a21b-bca54d301486.cfargotunnel.com`, proxied. If the current
record differs, inspect/report before overwriting; leave unrelated DNS/mail/SSH
and production routes untouched. No DNS/route/native/service change was made in
diagnosis. Permitted readiness login and clone connection remain pending.

VM private receipt: `stage-v1/unexpected-404-audit.json`; local sanitized receipt:
`healthchecks/stage-404-routing-audit-20261026.json`.

### Host/private Nginx mapping rechecked (03:16 UTC)

Operator asked whether d-stage should be forwarded by global Nginx to native
Dalali because of the Frappe missing-site message. Read-only `nginx -T` validates:
native Dalali server_name exists, but **d-stage is not included anywhere in host
Nginx**. This is deliberate for the Access → separate tunnel → guarded 18089
design, not an omitted host vhost. No host Nginx change is required for that path.

Existing private clone Nginx is running with its exact readonly config mount and
already sets `X-Frappe-Site-Name: dalali-rehearsal.invalid`. Native
`dalali.ndftechlabs.com` is the protected production source, maintenance still on;
do not point the staging hostname there or clear maintenance to address this.
Public readiness gateway still cannot forward any Frappe request, so the observed
Frappe error points to an unintended route/cache rather than a site header missing
from readiness. First inspect the exact Cloudflare DNS target/overrides. Any future
clone connection must remain behind the separate action/authentication gateway.
No source/config/route/service changes made; native health and app egress passed.

VM receipt: `stage-v1/nginx-routing-audit.json`; local sanitized receipt:
`healthchecks/stage-nginx-routing-audit-20261026.json`.

## DNS cause identified from operator's record view

Operator supplied the actual exact-host DNS record: **A**, name **d-stage**,
content **161.97.69.61**, **Proxied**, TTL Auto. It targets the native origin rather
than the new staging tunnel, explaining the native unknown-Frappe-site response
despite the correctly configured tunnel/gateway. Proxied status alone did not make
the A record use the tunnel; public DNS masked this origin target behind edge IPs.

The agreed staging correction is to edit **only the existing d-stage record** to
**CNAME → 9388ab42-7fa1-429c-a21b-bca54d301486.cfargotunnel.com**, keep **Proxied**
and TTL Auto, then verify the approved operator's browser reaches protected static
readiness. Do not delete records, change native Dalali/demo/ERP/mail/root records,
create a native site/global Nginx vhost or point the tunnel to raw frontend 18088.
Agent has not applied the DNS change; awaiting operator save/verification. Clone
homepage is still not connected, and the separate named-copy proposal was paused
by the operator (no new DB/site/data copy performed).
