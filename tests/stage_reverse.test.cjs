"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");
const source = process.env.NDF_STAGE_SOURCE_DIR || path.join(__dirname, "../docs/migrations/same-vm-docker");
const policy = require(path.join(source, "stage_policy.cjs"));
const { createReviewServer } = require(path.join(source, "stage_reverse.cjs"));
const keys = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const config = { hostname: "d-stage.ndftechlabs.com", issuer: "https://synthetic-team.cloudflareaccess.com",
  audience: "a".repeat(64), allowed_emails: ["reviewer@example.invalid"], origin_enabled: true,
  review_mode: "guest-website-v1", review_site: "dalali-rehearsal.invalid" };
function token(email = config.allowed_emails[0]) {
  const encode = x => Buffer.from(JSON.stringify(x)).toString("base64url");
  const now = Math.floor(Date.now()/1000);
  const value = encode({ alg: "RS256", kid: "synthetic" }) + "." + encode({ iss: config.issuer, aud: [config.audience],
    type: "app", sub: "synthetic", email, iat: now-1, exp: now+3600 });
  return value + "." + crypto.sign("RSA-SHA256", Buffer.from(value), keys.privateKey).toString("base64url");
}
const snapshot = () => ({ fetched_at: Math.floor(Date.now()/1000),
  keys: [{ ...keys.publicKey.export({ format: "jwk" }), kid: "synthetic" }] });
async function request(server, route = "/", method = "GET", body = "", headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port: server.address().port, path: route, method, timeout: 5000,
      headers: { host: config.hostname, "cf-access-jwt-assertion": token(),
        ...(body ? { "content-type": "application/x-www-form-urlencoded", "content-length": Buffer.byteLength(body) } : {}), ...headers } }, res => {
      let text = "";res.on("data", x => text += x);res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: text }));
    });
    req.on("error", reject);req.on("timeout", () => req.destroy());req.end(body);
  });
}
function denied(route, method = "GET", body = "", headers = {}) {
  assert.throws(() => policy.classify({ url: route, method, headers }, Buffer.from(body)), /staging_action_denied/);
}

test("strict guest policy denies financial/login/Desk/dynamic page/route and dispatch bypasses", () => {
  for (const route of ["/desk", "/login", "/api/method/login", "/api/resource/Item", "/private/files/test.png", "/shop",
    "/api/method/dt_recomendations.api.get_dynamic_sections", "/?cmd=frappe.client.insert", "/socket.io/",
    "/api/method/frappe.website.doctype.web_page_view.web_page_view.make_view_log",
    "/api/method/frappe.desk.form.save.savedocs", "/api/method/frappe.model.utils.user_settings.save",
    "/assets/../site_config.json", "/assets/%2e%2e/site_config.json", "/assets/%252e%252e/file.js",
    "//evil.invalid/file.js", "http://evil.invalid/", "/assets\\file.js", "/assets/file.js?cmd=login", "/files/file.html",
    "/?cmd=ping&cmd=frappe.client.insert", "/api/method/ping?cmd=login", "/api/method/ping?query=evil.method"]) denied(route);
  denied("/", "POST", "cmd=login&usr=Administrator&pwd=synthetic", { "content-type": "application/x-www-form-urlencoded" });
  denied("/?cmd=ping", "POST", "cmd=ping", { "content-type": "application/x-www-form-urlencoded" });
  denied("/api/method/ping", "POST", '{"cmd":"ping","cmd":"login"}', { "content-type": "application/json" });
  denied("/", "GET", "", { "x-http-method-override": "POST" });
  denied("/", "GET", "", { origin: "https://dalali.ndftechlabs.com" });
  denied("/", "GET", "", { "transfer-encoding": "chunked" });
});
test("only reviewed no-argument reads are canonicalized to safe upstream GET", () => {
  const action = policy.classify({ url: "/", method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" } },
    Buffer.from("cmd=webshop.webshop.doctype.webshop_settings.webshop_settings.is_cart_enabled"));
  assert.deepEqual(action, { kind: "read", method: "GET", path: "/api/method/webshop.webshop.doctype.webshop_settings.webshop_settings.is_cart_enabled" });
  assert.equal(policy.classify({ url: "/assets/frappe/js/app.js?v=1", method: "GET", headers: {} }).kind, "static");
  assert.equal(policy.classify({ url: "/files/Synthetic%20image.jpg", method: "GET", headers: {} }).kind, "static");
  assert.equal(policy.classify({ url: "/website_script.js", method: "GET", headers: {} }).kind, "static");
});
test("redirects cannot escape approved origins or action scope", () => {
  assert.equal(policy.redirect("http://dalali-rehearsal.invalid:8080/"), "https://d-stage.ndftechlabs.com/");
  for (const value of ["https://dalali.ndftechlabs.com/", "https://provider.invalid/", "//evil.invalid/", "/login", "/?cmd=frappe.client.insert",
    "https://user@d-stage.ndftechlabs.com/", "https://d-stage.ndftechlabs.com.evil.invalid/"]) {
    assert.throws(() => policy.redirect(value), /staging_action_denied/);
  }
});
test("review mode requires exact explicit Guest/site scope", () => {
  for (const change of [{ origin_enabled: false }, { review_mode: "all" }, { review_site: "dalali.ndftechlabs.com" }]) {
    assert.throws(() => createReviewServer({ ...config, ...change }, snapshot), /scope_not_approved/);
  }
});
test("reverse HTTP proof uses synthetic upstream only", async t => {
  let received = [], scenario = "normal";
  const upstream = http.createServer((req, res) => {
    received.push({ url: req.url, method: req.method, headers: req.headers });req.resume();
    if (scenario === "external-redirect") { res.writeHead(302, { location: "https://provider.invalid/pay" });return res.end(); }
    if (scenario === "internal-redirect") { res.writeHead(302, { location: "http://dalali-rehearsal.invalid:8080/" });return res.end(); }
    if (scenario === "error") { res.writeHead(500, { "content-type": "text/html" });return res.end("synthetic-private-diagnostic"); }
    res.writeHead(200, { "content-type": req.url.startsWith("/api/") ? "application/json" : "text/html; charset=utf-8",
      "set-cookie": "sid=synthetic-upstream-session; Path=/", "content-security-policy": "default-src 'self'" });
    res.end(req.url.startsWith("/api/") ? '{"message":true}' : "<html><body><main>Synthetic catalog</main></body></html>");
  });
  await new Promise(resolve => upstream.listen(0, "127.0.0.1", resolve));
  const server = createReviewServer(config, snapshot, { hostname: "127.0.0.1", port: upstream.address().port });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    await new Promise(resolve => { server.closeAllConnections();server.close(resolve); });
    await new Promise(resolve => { upstream.closeAllConnections();upstream.close(resolve); });
  });
  await t.test("no bearer or wrong identity is denied before upstream", async () => {
    const before = received.length;
    assert.equal((await request(server, "/", "GET", "", { "cf-access-jwt-assertion": "invalid" })).status, 403);
    assert.equal((await request(server, "/", "GET", "", { "cf-access-jwt-assertion": token("other@example.invalid") })).status, 403);
    assert.equal(received.length, before);
  });
  await t.test("homepage banner/no-store and correct site mapping; tokens/cookies/auth not relayed", async () => {
    const r = await request(server, "/", "GET", "", { cookie: "sid=synthetic-client-session; CF_Authorization=synthetic",
      authorization: "Bearer synthetic", "x-frappe-site-name": "dalali.ndftechlabs.com", "x-forwarded-host": "evil.invalid" });
    assert.equal(r.status, 200);assert.match(r.body, /Synthetic catalog/);assert.match(r.body, /DALALI STAGING/);
    assert.equal(r.headers["set-cookie"], undefined);assert.equal(r.headers["cache-control"], "no-store, private");
    assert.equal(r.headers["content-security-policy"], "default-src 'self'");
    const h = received.at(-1).headers;
    assert.equal(h.host, "d-stage.ndftechlabs.com");assert.equal(h["x-frappe-site-name"], "dalali-rehearsal.invalid");
    assert.equal(h["x-forwarded-proto"], "https");assert.equal(h["x-forwarded-host"], "d-stage.ndftechlabs.com");
    for (const field of ["cookie", "authorization", "cf-access-jwt-assertion", "cf-access-authenticated-user-email"]) assert.equal(h[field], undefined);
  });
  await t.test("business/private/login/Desk paths never reach synthetic upstream", async () => {
    const before = received.length;
    for (const route of ["/login", "/desk", "/private/files/test.png", "/api/method/frappe.client.insert", "/?cmd=login"]) {
      assert.equal((await request(server, route)).status, 403);
    }
    assert.equal((await request(server, "/", "POST", "cmd=frappe.client.insert&doc=synthetic")).status, 403);
    assert.equal(received.length, before);
  });
  await t.test("read RPC forwards no caller arguments or POST body", async () => {
    const r = await request(server, "/", "POST", "cmd=ping");
    assert.equal(r.status, 200);assert.equal(received.at(-1).method, "GET");assert.equal(received.at(-1).url, "/api/method/ping");
  });
  await t.test("external redirects blocked and internal redirect safely mapped", async () => {
    scenario = "external-redirect";assert.equal((await request(server)).status, 403);
    scenario = "internal-redirect";const r = await request(server);
    assert.equal(r.status, 302);assert.equal(r.headers.location, "https://d-stage.ndftechlabs.com/");
  });
  await t.test("server diagnostics are not forwarded", async () => {
    scenario = "error";const r = await request(server);
    assert.equal(r.status, 500);assert.equal(r.body.includes("synthetic-private-diagnostic"), false);
  });
});
