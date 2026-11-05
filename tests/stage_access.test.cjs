"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const path = require("node:path");
const access = require(path.join(process.env.NDF_STAGE_SOURCE_DIR || path.join(__dirname, "../docs/migrations/same-vm-docker"), "stage_access.cjs"));
const now = 1800000000;
const config = { hostname: "d-stage.ndftechlabs.com", issuer: "https://synthetic-team.cloudflareaccess.com",
  audience: "a".repeat(64), allowed_emails: ["reviewer@example.invalid"] };
const keys = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const otherKeys = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...keys.publicKey.export({ format: "jwk" }), kid: "synthetic-key", alg: "RS256", use: "sig" };
const snapshot = { fetched_at: now - 60, keys: [jwk] };
const claims = { iss: config.issuer, aud: [config.audience], type: "app", sub: "synthetic-reviewer", email: config.allowed_emails[0],
  iat: now - 10, exp: now + 3590 };
function encode(value) { return Buffer.from(JSON.stringify(value)).toString("base64url"); }
function token(payload = claims, header = {}, signingKey = keys.privateKey) {
  const body = encode({ alg: "RS256", typ: "JWT", kid: jwk.kid, ...header }) + "." + encode(payload);
  return body + "." + crypto.sign("RSA-SHA256", Buffer.from(body), signingKey).toString("base64url");
}
const verify = access.createVerifier(config);
function denied(fn) { assert.throws(fn, { message: "staging_access_denied" }); }

test("signed exact issuer/audience/email accepted, no arbitrary claims returned", () => {
  assert.deepEqual(verify(token(), snapshot, now), { authorized: true, email: claims.email, expires: claims.exp });
});
test("either current or rotated public signing key is usable by kid", () => {
  const next = { ...otherKeys.publicKey.export({ format: "jwk" }), kid: "synthetic-next", alg: "RS256" };
  const rotated = { fetched_at: now, keys: [jwk, next] };
  assert.equal(verify(token(claims, { kid: next.kid }, otherKeys.privateKey), rotated, now).authorized, true);
});
test("missing, malformed, padded and oversized bearer assertions denied", () => {
  for (const value of [undefined, null, "", "not-a-jwt", "a.b.c", token() + "=", "a".repeat(16385)]) {
    denied(() => verify(value, snapshot, now));
  }
});
test("forged signature and modified claims denied", () => {
  denied(() => verify(token(claims, {}, otherKeys.privateKey), snapshot, now));
  const parts = token().split(".");parts[1] = encode({ ...claims, email: "attacker@example.invalid" });
  denied(() => verify(parts.join("."), snapshot, now));
});
test("algorithm confusion, unsupported critical headers and token key URLs denied", () => {
  for (const header of [{ alg: "none" }, { alg: "HS256" }, { jku: "https://evil.invalid/key" },
    { jwk }, { x5u: "https://evil.invalid/key" }, { crit: [] }, { b64: true }, { typ: "OTHER" }]) {
    denied(() => verify(token(claims, header), snapshot, now));
  }
});
test("wrong issuer, audience, email, token type and missing subject denied", () => {
  for (const change of [{ iss: "https://wrong-team.cloudflareaccess.com" }, { aud: ["b".repeat(64)] },
    { aud: config.audience }, { email: "other@example.invalid" }, { email: undefined }, { type: "service" }, { sub: "" }]) {
    denied(() => verify(token({ ...claims, ...change }), snapshot, now));
  }
});
test("expired, future-dated, malformed and overly long sessions denied", () => {
  for (const change of [{ exp: now }, { exp: now - 1 }, { iat: now + 60 }, { nbf: now + 60 },
    { exp: String(now + 60) }, { iat: null }, { exp: claims.iat + 86401 }, { exp: claims.iat }]) {
    denied(() => verify(token({ ...claims, ...change }), snapshot, now));
  }
});
test("configured 24-hour session remains supported, not silently reduced", () => {
  assert.equal(verify(token({ ...claims, exp: claims.iat + 86400 }), snapshot, now).authorized, true);
});
test("unknown, duplicate, stale and future key snapshots denied", () => {
  for (const value of [{ fetched_at: now, keys: [] }, { fetched_at: now, keys: [{ ...jwk, kid: "other" }] },
    { fetched_at: now, keys: [jwk, jwk] }, { fetched_at: now - 3601, keys: [jwk] },
    { fetched_at: now + 31, keys: [jwk] }, { fetched_at: String(now), keys: [jwk] }]) {
    denied(() => verify(token(), value, now));
  }
});
test("private or non-signing RSA keys rejected", () => {
  for (const key of [{ ...jwk, d: "private-not-allowed" }, { ...jwk, use: "enc" }, { ...jwk, alg: "HS256" },
    { ...jwk, key_ops: ["encrypt"] }, { ...jwk, kty: "oct" }]) {
    denied(() => verify(token(), { fetched_at: now, keys: [key] }, now));
  }
});
test("wildcards, duplicate reviewers, unrelated hosts and unsafe issuer config denied", () => {
  for (const change of [{ allowed_emails: [] }, { allowed_emails: ["*@example.invalid"] },
    { allowed_emails: [claims.email, claims.email.toUpperCase()] }, { allowed_emails: [" @example.invalid"] },
    { hostname: "dalali.ndftechlabs.com" }, { issuer: "http://synthetic-team.cloudflareaccess.com" },
    { issuer: "https://synthetic-team.cloudflareaccess.com/redirect" }, { audience: "policy-uuid" }]) {
    denied(() => access.createVerifier({ ...config, ...change }));
  }
});
test("request host and signed JWT are required; unsigned email header is insufficient", () => {
  const good = { headers: { host: config.hostname, "cf-access-jwt-assertion": token() } };
  assert.equal(access.authorizeRequest(good, verify, snapshot, now).authorized, true);
  denied(() => access.authorizeRequest({ headers: { host: config.hostname,
    "cf-access-authenticated-user-email": claims.email } }, verify, snapshot, now));
  for (const host of ["dalali.ndftechlabs.com", config.hostname + ".evil.invalid", config.hostname + ":8080", "localhost"]) {
    denied(() => access.authorizeRequest({ headers: { ...good.headers, host } }, verify, snapshot, now));
  }
});
test("duplicate Host or JWT headers denied before forwarding", () => {
  const jwt = token();
  const request = { headers: { host: config.hostname, "cf-access-jwt-assertion": jwt },
    rawHeaders: ["Host", config.hostname, "Cf-Access-Jwt-Assertion", jwt] };
  assert.equal(access.authorizeRequest(request, verify, snapshot, now).authorized, true);
  for (const duplicate of [["host", config.hostname], ["Cf-Access-Jwt-Assertion", jwt]]) {
    denied(() => access.authorizeRequest({ ...request, rawHeaders: [...request.rawHeaders, ...duplicate] }, verify, snapshot, now));
  }
});
test("case-normalized exact email matches without allowing other identities", () => {
  assert.equal(verify(token({ ...claims, email: claims.email.toUpperCase() }), snapshot, now).email, claims.email);
});
