/* Guest-only website review policy. No arbitrary website routes or business RPC. */
"use strict";
const PUBLIC_ORIGIN = "https://d-stage.ndftechlabs.com";
const READS = new Set(["ping", "frappe.auth.get_logged_user",
  "webshop.webshop.doctype.webshop_settings.webshop_settings.is_cart_enabled"]);
const ASSETS = new Set(["css", "js", "svg", "png", "jpg", "jpeg", "gif", "webp", "avif", "ico", "woff", "woff2", "ttf", "otf", "eot"]);
const IMAGES = new Set(["png", "jpg", "jpeg", "gif", "webp", "avif", "ico"]);

function denied() { throw new Error("staging_action_denied"); }
function pathOf(raw) {
  if (typeof raw !== "string" || raw.length > 8192 || !raw.startsWith("/") || raw.startsWith("//") || /[\x00-\x20\x7f\\#]/.test(raw)) denied();
  const query = raw.indexOf("?");
  const pathname = query < 0 ? raw : raw.slice(0, query);
  let decoded;
  try { decoded = decodeURIComponent(pathname); } catch { denied(); }
  if (/[\x00-\x1f\x7f\\%]/.test(decoded) || decoded.includes("//") || decoded.split("/").some(x => x === "." || x === ".." || x.startsWith("."))) denied();
  return { raw: pathname, decoded, search: query < 0 ? "" : raw.slice(query + 1) };
}
function parameters(text) {
  if (text.length > 65536 || /%(?![a-fA-F0-9]{2})/.test(text)) denied();
  const result = Object.create(null);
  for (const [key, value] of new URLSearchParams(text)) {
    if (Object.hasOwn(result, key)) denied();
    result[key] = value;
  }
  return result;
}
function classify(request, body = Buffer.alloc(0)) {
  const { method, headers } = request;
  if (!["GET", "HEAD", "POST"].includes(method) || body.length > 65536 ||
      ["x-http-method-override", "x-method-override", "x-frappe-cmd"].some(x => headers[x] !== undefined) ||
      headers["content-encoding"] !== undefined || headers["transfer-encoding"] !== undefined) denied();
  if (headers.origin !== undefined && headers.origin !== PUBLIC_ORIGIN) denied();
  const route = pathOf(request.url);
  const args = parameters(route.search);
  if (method !== "POST" && body.length) denied();
  if (method === "POST") {
    if ((headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase() !== "application/x-www-form-urlencoded") denied();
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(body); } catch { denied(); }
    for (const [key, value] of Object.entries(parameters(text))) {
      if (Object.hasOwn(args, key)) denied();
      args[key] = value;
    }
  }
  if (Object.hasOwn(args, "_") && !/^\d{1,20}$/.test(args._)) denied();
  const api = route.decoded.startsWith("/api/method/") ? route.decoded.slice("/api/method/".length) : null;
  const legacy = route.decoded === "/" && typeof args.cmd === "string" ? args.cmd : null;
  if (api || legacy) {
    const name = api || legacy;
    if (!READS.has(name) || (args.cmd !== undefined && args.cmd !== name) || Object.keys(args).some(x => !["cmd", "_"].includes(x))) denied();
    // These reviewed no-argument reads are canonically forwarded as GET, never
    // arbitrary POST bodies/cmd overrides, and never as an authenticated User.
    return { kind: "read", method: method === "HEAD" ? "HEAD" : "GET", path: "/api/method/" + name };
  }
  if (method === "POST" || Object.hasOwn(args, "cmd")) denied();
  if (route.decoded === "/" && Object.keys(args).every(x => x === "_")) return { kind: "html", method, path: "/" };
  if (route.decoded === "/robots.txt" && !route.search) return { kind: "robots", method };
  if (Object.keys(args).some(x => !["_", "v", "ver", "modified"].includes(x))) denied();
  // Frozen www/website_script.py reads Website Script/theme settings only.
  // Tracking RPC remains denied and the clone's CSP blocks external scripts.
  if (route.decoded === "/website_script.js") return { kind: "static", method, path: route.raw };
  const ext = route.decoded.split(".").pop().toLowerCase();
  if (route.decoded.startsWith("/assets/") && ASSETS.has(ext)) return { kind: "static", method, path: route.raw };
  if (route.decoded.startsWith("/files/") && IMAGES.has(ext)) return { kind: "static", method, path: route.raw };
  denied();
}
function redirect(location) {
  if (typeof location !== "string" || location.length > 8192 || /[\x00-\x20\x7f\\]/.test(location)) denied();
  let url;
  try { url = new URL(location, PUBLIC_ORIGIN); } catch { denied(); }
  if (url.username || url.password || ![PUBLIC_ORIGIN, "http://dalali-rehearsal.invalid:8080"].includes(url.origin)) denied();
  const target = url.pathname + url.search;
  classify({ method: "GET", url: target, headers: {} });
  return PUBLIC_ORIGIN + target;
}
module.exports = { classify, redirect, PUBLIC_ORIGIN };
