/* Authenticated public-host to private-clone forwarding, always as Guest. */
"use strict";
const http = require("node:http");
const { createVerifier, authorizeRequest } = require("./stage_access.cjs");
const policy = require("./stage_policy.cjs");
const BANNER = '<aside role="status" style="position:relative;z-index:99999;padding:12px;background:#fff3cd;color:#332700;text-align:center;font:14px sans-serif">DALALI STAGING — read-only website preview. Login, Desk, cart and business writes are blocked. Do not follow external/provider links.</aside>';
const security = {
  "cache-control": "no-store, private", pragma: "no-cache", expires: "0",
  "x-robots-tag": "noindex, nofollow, noarchive, nosnippet", "x-content-type-options": "nosniff",
  "x-frame-options": "DENY", "referrer-policy": "no-referrer", "x-ndf-environment": "dalali-staging-guest-review",
};
function createReviewServer(config, getSnapshot, testOrigin) {
  if (config.origin_enabled !== true || config.review_mode !== "guest-website-v1" || config.review_site !== "dalali-rehearsal.invalid") {
    throw new Error("staging_review_scope_not_approved");
  }
  const verify = createVerifier(config);
  // The production origin cannot be selected by HTTP/config input. Optional
  // dependency injection is only for synthetic loopback tests of this function.
  const origin = testOrigin || { hostname: "frontend", port: 8080 };
  const agent = new http.Agent({ keepAlive: true, maxSockets: 4, maxTotalSockets: 4, maxFreeSockets: 2 });
  const server = http.createServer({ maxHeaderSize: 24576, requireHostHeader: true }, async (request, response) => {
    function reply(status, message) {
      if (response.headersSent) return response.destroy();
      response.writeHead(status, { ...security, "content-type": "text/plain; charset=utf-8" });
      response.end(request.method === "HEAD" ? undefined : message + "\n");
    }
    try { authorizeRequest(request, verify, getSnapshot()); }
    catch { request.resume();return reply(403, "Staging access denied."); }
    let action;
    try {
      const declared = request.headers["content-length"];
      if (declared !== undefined && (!/^\d+$/.test(declared) || Number(declared) > 65536)) throw new Error();
      const parts = [];let size = 0;
      for await (const part of request) {
        size += part.length;
        if (size > 65536) { reply(413, "Staging request too large.");return; }
        parts.push(part);
      }
      action = policy.classify(request, Buffer.concat(parts));
    } catch { request.resume();return reply(403, "Read-only staging: this route or action is not enabled."); }
    if (action.kind === "robots") {
      response.writeHead(200, { ...security, "content-type": "text/plain; charset=utf-8" });
      return response.end(action.method === "HEAD" ? undefined : "User-agent: *\nDisallow: /\n");
    }
    // Whitelist, not a copy of caller headers. Never forward CF bearer/email,
    // Authorization, cookies/SID, caller site/forwarding headers or API keys.
    const headers = { host: "d-stage.ndftechlabs.com", "x-frappe-site-name": "dalali-rehearsal.invalid",
      "x-forwarded-host": "d-stage.ndftechlabs.com", "x-forwarded-proto": "https",
      "accept-encoding": "identity", accept: action.kind === "html" ? "text/html" : "*/*" };
    const upstream = http.request({ hostname: origin.hostname, port: origin.port, method: action.method,
      path: action.path, headers, agent, timeout: 15000 }, result => {
      const out = { ...security };
      for (const name of ["content-type", "content-security-policy"]) {
        if (typeof result.headers[name] === "string") out[name] = result.headers[name];
      }
      // Set-Cookie is intentionally NOT forwarded: no Frappe session or source
      // cookie is used/overwritten. Cloudflare manages its own Access cookies.
      if (result.headers.location !== undefined) {
        try { out.location = policy.redirect(result.headers.location); }
        catch { result.resume();return reply(403, "Staging redirect denied."); }
      }
      if (result.statusCode >= 500) {
        result.resume();return reply(result.statusCode, "Clone website error; private diagnostics retained on the VM.");
      }
      if (result.headers["content-encoding"] && result.headers["content-encoding"] !== "identity") {
        result.resume();return reply(502, "Unexpected private upstream encoding.");
      }
      const max = action.kind === "html" ? 4 * 1024 * 1024 : 32 * 1024 * 1024;
      const declared = Number(result.headers["content-length"] || 0);
      if (!Number.isFinite(declared) || declared > max) { result.resume();return reply(502, "Staging response exceeds limit."); }
      if (action.kind === "html" && result.statusCode === 200 && action.method !== "HEAD") {
        if (!String(result.headers["content-type"] || "").toLowerCase().startsWith("text/html")) {
          result.resume();return reply(502, "Expected clone website HTML.");
        }
        const chunks = [];let size = 0;
        result.on("data", part => {
          size += part.length;
          if (size > max) { result.destroy();reply(502, "Staging HTML exceeds limit."); } else chunks.push(part);
        });
        result.on("end", () => {
          if (response.destroyed || response.writableEnded) return;
          let body = Buffer.concat(chunks).toString("utf8");
          if (!/<body\b[^>]*>/i.test(body)) return reply(502, "Expected clone website document.");
          body = body.replace(/<body\b[^>]*>/i, match => match + BANNER);
          response.writeHead(result.statusCode, out);response.end(body);
        });
      } else {
        response.writeHead(result.statusCode, out);
        let size = 0;
        result.on("data", part => { size += part.length;if (size > max) { result.destroy();response.destroy(); } });
        result.pipe(response);
      }
      result.on("error", () => reply(502, "Private staging response interrupted."));
    });
    upstream.on("timeout", () => upstream.destroy());
    upstream.on("error", () => reply(502, "Private staging origin unavailable."));
    response.on("close", () => { if (!response.writableEnded) upstream.destroy(); });
    upstream.end();
  });
  function denySocket(request, socket) {
    request.resume();socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  }
  server.on("upgrade", denySocket);server.on("connect", denySocket);
  server.on("clientError", (_error, socket) => socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"));
  server.on("close", () => agent.destroy());
  server.maxHeadersCount = 64;server.maxRequestsPerSocket = 100;
  server.headersTimeout = 5000;server.requestTimeout = 10000;server.keepAliveTimeout = 5000;
  server.setTimeout(10000, socket => socket.destroy());
  return server;
}
module.exports = { createReviewServer };
