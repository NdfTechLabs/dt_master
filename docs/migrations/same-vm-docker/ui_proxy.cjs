/* Clone-only browser proxy. Never logs request bodies, cookies, paths or responses. */
"use strict";
const http = require("node:http");

const HOST = "dalali-rehearsal.invalid";
const SITE_NAMESPACE = "/" + HOST;
const READ_METHODS = new Set([
  "ping",
  "frappe.auth.get_logged_user", "frappe.realtime.get_user_info",
  "frappe.client.get", "frappe.client.get_list", "frappe.client.get_count",
  "frappe.desk.desktop.get_workspace_sidebar_items", "frappe.desk.desktop.get_desktop_page",
  "frappe.desk.desktop.get_sidebar_items", "frappe.desk.desktop.get_deskdata",
  "frappe.desk.reportview.get", "frappe.desk.reportview.get_count",
  "frappe.desk.form.load.getdoc", "frappe.desk.form.load.getdoctype", "frappe.desk.form.load.getmetadata",
  "frappe.desk.form.load.get_permissions", "frappe.desk.form.utils.get_docinfo",
  "frappe.desk.form.linked_with.get_linked_docs", "frappe.desk.form.linked_with.get_submitted_linked_docs",
  "frappe.desk.search.search_link", "frappe.desk.search.search_widget", "frappe.desk.search.search",
  "frappe.desk.listview.get_list_settings", "frappe.desk.listview.get_filters",
  "frappe.desk.listview.get_list_sidebar_stats", "frappe.desk.listview.get_list_footer_stats",
  "frappe.desk.notifications.get_notifications", "frappe.desk.notifications.get_open_count",
  "frappe.desk.doctype.notification_log.notification_log.get_unread_count",
  "frappe.desk.doctype.notification_log.notification_log.get_notification_logs",
  "frappe.desk.doctype.workspace.workspace.get_module_wise_workspaces",
  "frappe.core.doctype.user.user.get_all_roles", "frappe.desk.form.utils.getdoctype",
  "webshop.webshop.doctype.webshop_settings.webshop_settings.is_cart_enabled",
  "frappe.core.doctype.session_default_settings.session_default_settings.get_session_default_values",
  // Frozen-source reviewed reads: SQL/cached settings only, no posting or stock
  // updates. Regional round-off dispatch remains denied pending deeper review.
  "erpnext.stock.dashboard.item_dashboard.get_data",
  "erpnext.stock.doctype.item.item.get_item_prices",
  "erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_dimensions",
  "erpnext.controllers.taxes_and_totals.get_rounding_tax_settings",
  "erpnext.stock.doctype.stock_settings.stock_settings.get_enable_stock_uom_editing",
  "erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment.doc_has_references",
]);
const TYPES = new Set(["Item", "Website Item", "Sales Order", "Sales Invoice", "Employee", "Loan", "Note", "User"]);
const METADATA_LABELS = new Set(["DocType", "DocField", "Kanban Board", "List Filter", "List View Settings", "Module Profile",
  "Company", "Workspace", "Workspace Sidebar", "Workspace Sidebar Item", "Number Card", "Dashboard Chart", "User Group"]);

function localURL(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== "http:" || u.hostname !== HOST || (u.port && u.port !== "8080") || u.username || u.password) return null;
  let decoded;
  try { decoded = decodeURIComponent((raw.match(/^http:\/\/[^/]+(\/[^?#]*)/) || [])[1] || u.pathname); } catch { return null; }
  if (decoded.includes("\\") || decoded.split("/").includes("..")) return null;
  return u;
}
function fields(body, contentType) {
  try {
    if (contentType.includes("application/json")) return JSON.parse(body.toString("utf8"));
    return Object.fromEntries(new URLSearchParams(body.toString("utf8")));
  } catch { return null; }
}
function safeSocketPacket(text) {
  return text.split("\x1e").every(packet => {
    if (["2", "3", "2probe", "3probe", "5", "6", "1"].includes(packet)) return true;
    if (packet === "40" + SITE_NAMESPACE + "," || packet === "41" + SITE_NAMESPACE + ",") return true;
    const prefix = "42" + SITE_NAMESPACE + ",";
    if (!packet.startsWith(prefix)) return false;
    let values;
    try { values = JSON.parse(packet.slice(prefix.length).replace(/^\d+(?=\[)/, "")); } catch { return false; }
    if (!Array.isArray(values)) return false;
    const [event, doctype] = values;
    return ["doctype_subscribe", "doctype_unsubscribe", "doc_subscribe", "doc_unsubscribe", "doc_open", "doc_close"].includes(event) && TYPES.has(doctype);
  });
}
function allowed(request, url, body, plan) {
  if (!["GET", "HEAD", "POST"].includes(request.method)) return false;
  if (url.pathname.startsWith("/socket.io")) return request.method !== "POST" || safeSocketPacket(body.toString("utf8"));
  const data = { ...Object.fromEntries(url.searchParams), ...fields(body, request.headers["content-type"] || "") };
  const legacy = ["/", "/api/method"].includes(url.pathname) && typeof data?.cmd === "string" ? data.cmd : null;
  if (url.pathname.startsWith("/api/method/") || legacy) {
    const name = legacy || decodeURIComponent(url.pathname.slice("/api/method/".length));
    if (name === "frappe.desk.reportview.get_list") return TYPES.has(data.doctype) || data.doctype === "List Filter";
    // validate_link_and_fetch dispatches custom queries when supplied; permit
    // only the reviewed ordinary lookup on these nonvirtual doctypes.
    if (name === "frappe.client.validate_link_and_fetch") return TYPES.has(data.doctype) && !data.query;
    if (READ_METHODS.has(name)) return true;
    if (name === "login" && request.method === "POST") return data && plan.users.includes(data.usr);
    if (name === "logout" && request.method === "POST") return true;
    if (name === "frappe.desk.form.save.savedocs" && request.method === "POST" && data?.action === "Save") {
      let doc;
      try { doc = typeof data.doc === "string" ? JSON.parse(data.doc) : data.doc; } catch { return false; }
      return doc?.doctype === "Note" && doc.name === plan.fixture_name && doc.title === plan.fixture_title &&
        Number(doc.docstatus || 0) === 0 && !Number(doc.public || 0) && String(doc.content || "").includes("UI rehearsal save proof");
    }
    return false;
  }
  if (url.pathname.startsWith("/api/resource/")) {
    const doctype = decodeURIComponent(url.pathname.slice("/api/resource/".length).split("/")[0]);
    return ["GET", "HEAD"].includes(request.method) && TYPES.has(doctype);
  }
  if (url.pathname.startsWith("/api/")) return false;
  return ["GET", "HEAD"].includes(request.method);
}
function frameGuard(input, output, denied) {
  let buffer = Buffer.alloc(0);
  input.on("data", chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    if (buffer.length > 131072) return denied();
    while (buffer.length >= 2) {
      const first = buffer[0], second = buffer[1], opcode = first & 15;
      if (!(first & 128) || (first & 112) || !(second & 128)) return denied();
      let length = second & 127, offset = 2;
      if (length === 126) { if (buffer.length < 4) return; length = buffer.readUInt16BE(2); offset = 4; }
      if (length === 127 || length > 65536) return denied();
      if (buffer.length < offset + 4 + length) return;
      const frame = buffer.subarray(0, offset + 4 + length);
      const payload = Buffer.from(frame.subarray(offset + 4));
      for (let n = 0; n < payload.length; n++) payload[n] ^= frame[offset + n % 4];
      if (opcode === 1) { if (!safeSocketPacket(payload.toString("utf8"))) return denied(); }
      else if (![8, 9, 10].includes(opcode) || length > 125) return denied();
      output.write(frame); buffer = buffer.subarray(frame.length);
    }
  });
}
function createProxy(plan) {
  const stats = { allowed: 0, deniedOrigins: 0, deniedActions: 0, deniedSocketFrames: 0, deniedMethods: {}, deniedRouteClasses: {}, listResponses: {}, deniedReadShapes: [] };
  const server = http.createServer((request, response) => {
    const url = localURL(request.url);
    if (!url) { stats.deniedOrigins++; request.resume(); response.writeHead(403); return response.end("Origin denied"); }
    let size = 0, chunks = [];
    request.on("data", part => {
      size += part.length;
      if (size > 2 * 1024 * 1024) request.destroy(); else chunks.push(part);
    });
    request.on("end", () => {
      const body = Buffer.concat(chunks);
      if (!allowed(request, url, body, plan)) {
        stats.deniedActions++;
        if (["/api/method/frappe.desk.reportview.get_list", "/api/method/frappe.client.validate_link_and_fetch"].includes(url.pathname)) {
          const data = { ...Object.fromEntries(url.searchParams), ...fields(body, request.headers["content-type"] || "") };
          if (stats.deniedReadShapes.length < 10) stats.deniedReadShapes.push({
            list_read: url.pathname.endsWith("get_list"), has_doctype: typeof data.doctype === "string",
            reviewed_doctype: TYPES.has(data.doctype), has_query: !!data.query,
            metadata_label: METADATA_LABELS.has(data.doctype) ? data.doctype : "other",
            has_nested_args: typeof data.args === "string", has_nested_data: typeof data.data === "string" });
        }
        const route = url.pathname === "/" ? "root" : url.pathname === "/login" ? "login_page" : url.pathname.startsWith("/api/") ? "api" : "other_local";
        const routeClass = request.method + " " + route;
        stats.deniedRouteClasses[routeClass] = (stats.deniedRouteClasses[routeClass] || 0) + 1;
        if (url.pathname.startsWith("/api/method/")) {
          const name = url.pathname.slice("/api/method/".length);
          if (/^[a-zA-Z0-9_.]+$/.test(name)) stats.deniedMethods[name] = (stats.deniedMethods[name] || 0) + 1;
        } else {
          const data = fields(body, request.headers["content-type"] || "");
          if (typeof data?.cmd === "string" && /^[a-zA-Z0-9_.]+$/.test(data.cmd)) {
            const name = "legacy:" + data.cmd;
            stats.deniedMethods[name] = (stats.deniedMethods[name] || 0) + 1;
          }
        }
        response.writeHead(403); return response.end("Action denied");
      }
      stats.allowed++;
      const headers = { ...request.headers, host: HOST + ":8080" };
      delete headers["proxy-connection"]; delete headers["proxy-authorization"];
      const upstream = http.request({ hostname: "frontend", port: 8080, method: request.method,
        path: url.pathname + url.search, headers, timeout: 30000 }, result => {
        const data = { ...Object.fromEntries(url.searchParams), ...fields(body, request.headers["content-type"] || "") };
        const method = url.pathname.startsWith("/api/method/") ? url.pathname.slice("/api/method/".length) : data.cmd;
        if (["frappe.desk.reportview.get", "frappe.desk.reportview.get_list"].includes(method) && TYPES.has(data.doctype)) {
          result.on("end", () => {
            stats.listResponses[data.doctype] = { status: result.statusCode,
              responses: (stats.listResponses[data.doctype]?.responses || 0) + 1 };
          });
        }
        response.writeHead(result.statusCode, result.headers); result.pipe(response);
      });
      upstream.on("error", () => { if (!response.headersSent) response.writeHead(502); response.end("Private upstream unavailable"); });
      upstream.on("timeout", () => upstream.destroy());
      upstream.end(body);
    });
  });
  const upgrade = (request, client, head) => {
    const url = localURL(request.url);
    if (!url || !url.pathname.startsWith("/socket.io") || request.method !== "GET") {
      stats.deniedOrigins++; return client.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    }
    const headers = { ...request.headers, host: HOST + ":8080" };
    delete headers["sec-websocket-extensions"]; delete headers["proxy-connection"]; delete headers["proxy-authorization"];
    const upstream = http.request({ hostname: "frontend", port: 8080, method: "GET", path: url.pathname + url.search, headers, timeout: 15000 });
    upstream.on("upgrade", (response, socket, remaining) => {
      if (response.statusCode !== 101) return client.destroy();
      stats.allowed++;
      client.write("HTTP/1.1 101 Switching Protocols\r\n" + Object.entries(response.headers).map(([k,v]) => `${k}: ${v}`).join("\r\n") + "\r\n\r\n");
      if (remaining.length) client.write(remaining);
      const denied = () => { stats.deniedSocketFrames++; client.destroy(); socket.destroy(); };
      frameGuard(client, socket, denied); socket.pipe(client);
      if (head.length) client.emit("data", head);
      client.on("close", () => socket.destroy()); socket.on("close", () => client.destroy());
      client.on("error", () => socket.destroy()); socket.on("error", () => client.destroy());
    });
    upstream.on("response", () => client.destroy()); upstream.on("error", () => client.destroy());
    upstream.on("timeout", () => upstream.destroy()); upstream.end();
  };
  server.on("upgrade", upgrade);
  server.on("connect", (request, client, head) => {
    const target = localURL("http://" + request.url + "/");
    if (!target || target.port !== "8080") {
      stats.deniedOrigins++; return client.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
    }
    // Only a plain clone WebSocket handshake may cross a CONNECT tunnel. Never
    // relay arbitrary TCP/TLS or uninspected HTTP through an allowed authority.
    client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    let buffer = head;
    client.setTimeout(10000, () => client.destroy());
    const receive = chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > 16384) return client.destroy();
      const split = buffer.indexOf("\r\n\r\n");
      if (split < 0) return;
      client.removeListener("data", receive); client.setTimeout(0);
      const lines = buffer.subarray(0, split).toString("utf8").split("\r\n");
      const match = /^GET (\/socket\.io[^ ]*) HTTP\/1\.1$/.exec(lines.shift() || "");
      if (!match) { stats.deniedActions++; return client.destroy(); }
      const headers = {};
      for (const line of lines) {
        const colon = line.indexOf(":"); if (colon < 1) return client.destroy();
        headers[line.slice(0, colon).toLowerCase()] = line.slice(colon + 1).trim();
      }
      if (headers.upgrade?.toLowerCase() !== "websocket" || headers.host !== HOST + ":8080") return client.destroy();
      upgrade({ method: "GET", url: "http://" + HOST + ":8080" + match[1], headers }, client, buffer.subarray(split + 4));
    };
    client.on("data", receive);
    if (buffer.length) receive(Buffer.alloc(0));
  });
  return { server, stats };
}
module.exports = { localURL, allowed, safeSocketPacket, createProxy, frameGuard };
