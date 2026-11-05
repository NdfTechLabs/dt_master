/* Pure staging identity boundary. No listener, network access or credentials on import. */
"use strict";
const crypto = require("node:crypto");

const STAGE_HOST = "d-stage.ndftechlabs.com";
const MAX_TOKEN_BYTES = 16384;
const MAX_KEY_AGE_SECONDS = 3600;
const MAX_SESSION_SECONDS = 86400;

function reject() { throw new Error("staging_access_denied"); }
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function integer(value) { return Number.isSafeInteger(value); }
function email(value) {
  return typeof value === "string" && value.length <= 254 &&
    /^[a-z0-9.!#$%&'+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$/i.test(value) && !value.includes("..");
}
function configuration(input) {
  if (!object(input) || input.hostname !== STAGE_HOST ||
      typeof input.issuer !== "string" || !/^https:\/\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com$/.test(input.issuer) ||
      typeof input.audience !== "string" || !/^[a-f0-9]{64}$/.test(input.audience) ||
      !Array.isArray(input.allowed_emails) || !input.allowed_emails.length || input.allowed_emails.length > 25 ||
      input.allowed_emails.some(value => !email(value))) reject();
  const emails = [...new Set(input.allowed_emails.map(value => value.toLowerCase()))];
  if (emails.length !== input.allowed_emails.length) reject();
  return Object.freeze({ hostname: input.hostname, issuer: input.issuer, audience: input.audience,
    allowedEmails: Object.freeze(emails) });
}
function decode(segment) {
  if (!segment || !/^[A-Za-z0-9_-]+$/.test(segment)) reject();
  const bytes = Buffer.from(segment, "base64url");
  if (bytes.toString("base64url") !== segment) reject();
  return bytes;
}
function json(segment) {
  try {
    const raw = decode(segment);
    const text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    const value = JSON.parse(text);
    if (!object(value)) reject();
    return value;
  } catch { reject(); }
}
function publicKey(snapshot, kid, now) {
  if (!object(snapshot) || !integer(snapshot.fetched_at) || snapshot.fetched_at > now + 30 ||
      now - snapshot.fetched_at > MAX_KEY_AGE_SECONDS ||
      !Array.isArray(snapshot.keys) || !snapshot.keys.length || snapshot.keys.length > 8) reject();
  const matches = snapshot.keys.filter(value => object(value) && value.kid === kid);
  if (matches.length !== 1) reject();
  const jwk = matches[0];
  if (jwk.kty !== "RSA" || (jwk.alg !== undefined && jwk.alg !== "RS256") ||
      (jwk.use !== undefined && jwk.use !== "sig") ||
      (jwk.key_ops !== undefined && (!Array.isArray(jwk.key_ops) || !jwk.key_ops.includes("verify"))) ||
      ["d", "p", "q", "dp", "dq", "qi", "oth"].some(name => name in jwk) ||
      typeof jwk.n !== "string" || jwk.n.length > 1400 || typeof jwk.e !== "string" || jwk.e.length > 16) reject();
  decode(jwk.n);decode(jwk.e);
  try {
    // Import only public RSA components, never token-supplied key URLs or keys.
    const key = crypto.createPublicKey({ key: { kty: "RSA", n: jwk.n, e: jwk.e }, format: "jwk" });
    const bits = key.asymmetricKeyDetails?.modulusLength;
    if (key.asymmetricKeyType !== "rsa" || !bits || bits < 2048 || bits > 8192) reject();
    return key;
  } catch { reject(); }
}
function createVerifier(input) {
  const config = configuration(input);
  return function verify(token, snapshot, now = Math.floor(Date.now() / 1000)) {
    try {
      if (!integer(now) || typeof token !== "string" || Buffer.byteLength(token) > MAX_TOKEN_BYTES) reject();
      const parts = token.split(".");
      if (parts.length !== 3) reject();
      const header = json(parts[0]);
      if (header.alg !== "RS256" || (header.typ !== undefined && header.typ !== "JWT") ||
          typeof header.kid !== "string" || !/^[A-Za-z0-9_-]{1,256}$/.test(header.kid) ||
          ["jku", "jwk", "x5u", "crit", "b64"].some(name => name in header)) reject();
      const signature = decode(parts[2]);
      if (signature.length < 256 || signature.length > 1024) reject();
      const key = publicKey(snapshot, header.kid, now);
      if (!crypto.verify("RSA-SHA256", Buffer.from(parts[0] + "." + parts[1]),
        { key, padding: crypto.constants.RSA_PKCS1_PADDING }, signature)) reject();
      const payload = json(parts[1]);
      if (payload.iss !== config.issuer || payload.type !== "app" ||
          !Array.isArray(payload.aud) || !payload.aud.length || payload.aud.length > 8 ||
          payload.aud.some(value => typeof value !== "string") || !payload.aud.includes(config.audience) ||
          !integer(payload.exp) || !integer(payload.iat) || payload.exp <= now || payload.iat > now + 30 ||
          payload.exp <= payload.iat || payload.exp - payload.iat > MAX_SESSION_SECONDS ||
          (payload.nbf !== undefined && (!integer(payload.nbf) || payload.nbf > now + 30)) ||
          typeof payload.sub !== "string" || !payload.sub || payload.sub.length > 256 ||
          !email(payload.email) || !config.allowedEmails.includes(payload.email.toLowerCase())) reject();
      // Do not return the bearer token or arbitrary claims to callers/logs.
      return Object.freeze({ authorized: true, email: payload.email.toLowerCase(), expires: payload.exp });
    } catch { reject(); }
  };
}
function authorizeRequest(request, verify, snapshot, now) {
  try {
    const headers = request.headers;
    if (!object(headers) || typeof headers.host !== "string" ||
        ![STAGE_HOST, STAGE_HOST + ":443"].includes(headers.host.toLowerCase()) ||
        typeof headers["cf-access-jwt-assertion"] !== "string") reject();
    if (request.rawHeaders !== undefined) {
      if (!Array.isArray(request.rawHeaders) || request.rawHeaders.length % 2) reject();
      const counts = { host: 0, "cf-access-jwt-assertion": 0 };
      for (let index = 0; index < request.rawHeaders.length; index += 2) {
        if (typeof request.rawHeaders[index] !== "string") reject();
        const name = request.rawHeaders[index].toLowerCase();
        if (Object.hasOwn(counts, name)) counts[name]++;
      }
      if (Object.values(counts).some(value => value !== 1)) reject();
    }
    return verify(headers["cf-access-jwt-assertion"], snapshot, now);
  } catch { reject(); }
}
module.exports = { createVerifier, authorizeRequest, MAX_KEY_AGE_SECONDS, MAX_SESSION_SECONDS };
