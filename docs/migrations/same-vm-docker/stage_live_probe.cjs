/* VM-only real Guest origin proof behind the already-tested action guard.
 * No public port, real bearer/cookie/login credentials or raw page diagnostics. */
"use strict";
const crypto = require("node:crypto");
const http = require("node:http");
const { createReviewServer } = require("./stage_reverse.cjs");
const { classify, PUBLIC_ORIGIN } = require("./stage_policy.cjs");
async function main() {
  const pair = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const config = { hostname: "d-stage.ndftechlabs.com", issuer: "https://synthetic-team.cloudflareaccess.com",
    audience: "a".repeat(64), allowed_emails: ["synthetic@example.invalid"], origin_enabled: true,
    review_mode: "guest-website-v1", review_site: "dalali-rehearsal.invalid" };
  const now = Math.floor(Date.now()/1000), encode = x => Buffer.from(JSON.stringify(x)).toString("base64url");
  const value = encode({ alg: "RS256", kid: "synthetic" }) + "." + encode({ iss: config.issuer, aud: [config.audience],
    type: "app", sub: "synthetic", email: config.allowed_emails[0], iat: now-1, exp: now+600 });
  const token = value + "." + crypto.sign("RSA-SHA256", Buffer.from(value), pair.privateKey).toString("base64url");
  const server = createReviewServer(config, () => ({ fetched_at: now, keys: [{ ...pair.publicKey.export({ format: "jwk" }), kid: "synthetic" }] }));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  async function request(route, method = "GET", body = "", authorized = true) {
    return new Promise((resolve, reject) => {
      const req = http.request({ host: "127.0.0.1", port: server.address().port, path: route, method, timeout: 20000,
        headers: { host: config.hostname, ...(authorized ? { "cf-access-jwt-assertion": token } : {}),
          ...(body ? { "content-type": "application/x-www-form-urlencoded", "content-length": Buffer.byteLength(body) } : {}) } }, res => {
        const parts=[];let size=0;
        res.on("data", part => { size+=part.length;if(size>8*1024*1024)req.destroy();else parts.push(part); });
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(parts).toString("utf8") }));
      });
      req.on("error", () => reject(new Error("private_stage_probe_request_failed")));
      req.on("timeout", () => req.destroy());req.end(body);
    });
  }
  try {
    if ((await request("/", "GET", "", false)).status !== 403) throw new Error("anonymous_stage_probe_denial_failed");
    const root = await request("/");
    if (root.status !== 200 || !root.body.includes("DALALI STAGING") || !/<body\b/i.test(root.body)) throw new Error("private_clone_homepage_not_ready");
    if (root.headers["set-cookie"] !== undefined || root.headers["cache-control"] !== "no-store, private") throw new Error("private_stage_cookie_cache_proof_failed");
    const found = [];
    for (const match of root.body.matchAll(/(?:src|href)=["']([^"']+)["']/gi)) {
      let url;
      try { url = new URL(match[1], PUBLIC_ORIGIN); } catch { continue; }
      if (url.origin !== PUBLIC_ORIGIN) continue;
      const route = url.pathname + url.search;
      try { if(classify({ url: route, method: "GET", headers: {} }).kind !== "static") continue; } catch { continue; }
      if (!found.includes(route)) found.push(route);
    }
    const selected = found.slice(0, 8);
    if (!selected.length) throw new Error("private_stage_asset_scope_empty");
    for (const route of selected) if ((await request(route)).status !== 200) throw new Error("private_stage_asset_failed");
    const ping = await request("/api/method/ping");
    if (ping.status !== 200 || JSON.parse(ping.body).message !== "pong") throw new Error("private_stage_ping_failed");
    for (const route of ["/login", "/desk", "/private/files/synthetic.png", "/api/resource/Sales%20Invoice", "/api/method/dt_recomendations.api.get_dynamic_sections"]) {
      if ((await request(route)).status !== 403) throw new Error("private_stage_unsafe_route_not_denied");
    }
    if ((await request("/", "POST", "cmd=frappe.client.insert&doc=synthetic")).status !== 403) throw new Error("private_stage_unsafe_write_not_denied");
    return { guest_review_origin_proof_passed: true, homepage_rendered_with_banner: true,
      checked_static_assets: selected.length, clone_ping_passed: true, unsafe_routes_and_insert_denied: true,
      no_upstream_session_cookies: true, real_bearer_or_credentials_used: false, raw_page_records_exported: false };
  } finally {
    server.closeAllConnections();await new Promise(resolve => server.close(resolve));
  }
}
main().then(result => console.log(JSON.stringify(result))).catch(() => {
  console.error('{"guest_review_origin_proof_failed":true,"raw_page_credentials_exported":false}');process.exitCode=1;
});
