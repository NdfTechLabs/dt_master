/* Staging entrypoint: readiness by default; explicit guest-only review mode. */
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const { createVerifier, authorizeRequest } = require("./stage_access.cjs");

function createReadinessServer(config, getSnapshot) {
  if (config.origin_enabled !== false) throw new Error("clone_origin_not_implemented_in_readiness_gateway");
  const verify = createVerifier(config);
  const server = http.createServer({ maxHeaderSize: 24576, requireHostHeader: true }, (request, response) => {
    request.resume();
    const headers = {
      "Cache-Control": "no-store, private", "Pragma": "no-cache",
      "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
      "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
      "X-NDF-Environment": "dalali-staging-readiness", "Content-Type": "text/plain; charset=utf-8",
    };
    function reply(status, text) { response.writeHead(status, headers);response.end(request.method === "HEAD" ? undefined : text); }
    try { authorizeRequest(request, verify, getSnapshot()); }
    catch { return reply(403, "Staging access denied.\n"); }
    if (!["GET", "HEAD"].includes(request.method) ||
        !["/", "/robots.txt"].includes(request.url) ||
        request.headers["transfer-encoding"] !== undefined ||
        (request.headers["content-length"] !== undefined && request.headers["content-length"] !== "0")) {
      return reply(403, "Staging action denied.\n");
    }
    if (request.url === "/robots.txt") return reply(200, "User-agent: *\nDisallow: /\n");
    reply(200, "Dalali staging: access verification only.\nClone pages are not connected yet.\nDo not follow external/provider links during review.\n");
  });
  function denySocket(request, socket) {
    request.resume();socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  }
  server.on("upgrade", denySocket);server.on("connect", denySocket);
  server.on("clientError", (_error, socket) => socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"));
  server.maxHeadersCount = 64;
  server.headersTimeout = 5000;server.requestTimeout = 10000;server.keepAliveTimeout = 5000;
  server.setTimeout(10000, socket => socket.destroy());
  return server;
}
function readJSON(path) {
  const stat = fs.lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536 || stat.mode & 0o022) throw new Error("unsafe_staging_config");
  return JSON.parse(fs.readFileSync(path, "utf8"));
}
if (require.main === module) {
  try {
    if (process.argv.length !== 4) throw new Error("staging_gateway_arguments");
    const config = readJSON(process.argv[2]);
    const getSnapshot = () => readJSON(process.argv[3]);
    const server = config.origin_enabled === true
      ? require("./stage_reverse.cjs").createReviewServer(config, getSnapshot)
      : createReadinessServer(config, getSnapshot);
    server.on("error", () => { console.error('{"staging_gateway_failed":true}');process.exit(1); });
    server.listen(18089, "0.0.0.0", () => console.log(JSON.stringify({ staging_gateway_started: true,
      clone_origin_enabled: config.origin_enabled === true, guest_only: config.origin_enabled === true })));
  } catch { console.error('{"staging_gateway_failed":true}');process.exitCode = 1; }
}
module.exports = { createReadinessServer };
