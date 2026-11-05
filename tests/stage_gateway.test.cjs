"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const crypto = require("node:crypto");
const path = require("node:path");
const { createReadinessServer } = require(path.join(process.env.NDF_STAGE_SOURCE_DIR || path.join(__dirname, "../docs/migrations/same-vm-docker"), "stage_gateway.cjs"));
const pair = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const config = { hostname: "d-stage.ndftechlabs.com", issuer: "https://synthetic-team.cloudflareaccess.com",
  audience: "a".repeat(64), allowed_emails: ["reviewer@example.invalid"], origin_enabled: false };
const snapshot = () => ({ fetched_at: Math.floor(Date.now()/1000), keys: [{ ...pair.publicKey.export({ format: "jwk" }), kid: "test" }] });
function token(email = config.allowed_emails[0]) {
  const now = Math.floor(Date.now()/1000);
  const encode = x => Buffer.from(JSON.stringify(x)).toString("base64url");
  const value = encode({ alg: "RS256", kid: "test" }) + "." + encode({ iss: config.issuer, aud: [config.audience],
    type: "app", sub: "synthetic", email, iat: now-1, exp: now+3600 });
  return value + "." + crypto.sign("RSA-SHA256", Buffer.from(value), pair.privateKey).toString("base64url");
}
async function request(server, route = "/", headers = {}, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port: server.address().port, path: route, method,
      headers: { host: config.hostname, ...headers }, timeout: 3000 }, res => {
      let body = "";res.on("data", x => body += x);res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on("error", reject);req.on("timeout", () => req.destroy());req.end();
  });
}
test("readiness HTTP boundary never forwards customer requests", async t => {
  const server = createReadinessServer(config, snapshot);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections();server.close(resolve); }));
  await t.test("anonymous and unsigned email spoof denied", async () => {
    assert.equal((await request(server)).status, 403);
    assert.equal((await request(server, "/", { "cf-access-authenticated-user-email": config.allowed_emails[0] })).status, 403);
  });
  await t.test("signed permitted reviewer receives only static readiness text", async () => {
    const r = await request(server, "/", { "cf-access-jwt-assertion": token() });
    assert.equal(r.status, 200);assert.match(r.body, /Clone pages are not connected/);
    assert.equal(r.body.includes(config.allowed_emails[0]), false);
    assert.equal(r.headers["cache-control"], "no-store, private");assert.match(r.headers["x-robots-tag"], /noindex/);
  });
  await t.test("wrong reviewer or host receives no readiness content", async () => {
    assert.equal((await request(server, "/", { "cf-access-jwt-assertion": token("other@example.invalid") })).status, 403);
    assert.equal((await request(server, "/", { host: "dalali.ndftechlabs.com", "cf-access-jwt-assertion": token() })).status, 403);
  });
  await t.test("API, legacy RPC, arbitrary paths and financial writes remain denied even with valid identity", async () => {
    for (const [route, method] of [["/api/method/frappe.client.insert", "POST"], ["/?cmd=frappe.client.insert", "GET"],
      ["/api/resource/Sales%20Invoice", "GET"], ["/login", "GET"], ["/assets/test.js", "GET"], ["/", "POST"]]) {
      assert.equal((await request(server, route, { "cf-access-jwt-assertion": token() }, method)).status, 403);
    }
  });
  await t.test("key-read failures fail closed without raw diagnostics", async () => {
    const bad = createReadinessServer(config, () => { throw new Error("private diagnostic must not escape"); });
    await new Promise(resolve => bad.listen(0, "127.0.0.1", resolve));
    try { const r = await request(bad, "/", { "cf-access-jwt-assertion": token() });
      assert.equal(r.status, 403);assert.equal(r.body.includes("private diagnostic"), false);
    } finally { await new Promise(resolve => { bad.closeAllConnections();bad.close(resolve); }); }
  });
});
test("clone upstream cannot be enabled by configuration", () => {
  assert.throws(() => createReadinessServer({ ...config, origin_enabled: true }, snapshot), /clone_origin_not_implemented/);
});
