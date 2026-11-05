/* VM-only CDP runner: reports booleans/counts, never DOM/records/cookies/passwords. */
"use strict";
const fs = require("node:fs");
const http = require("node:http");
const { spawn } = require("node:child_process");
const { createProxy, localURL, allowed, safeSocketPacket } = require("/opt/ndf-ui-proxy.cjs");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const ORIGIN = "http://dalali-rehearsal.invalid:8080";
let currentStep = "startup", lastGuard = {}, networkErrors = {}, browserState = {}, jsErrorKinds = {}, jsErrorSites = [], cspEvalBlocked = false, completedChecks = {};

function requestProxy(url, method = "GET", body = "") {
  return new Promise((resolve, reject) => {
    const request = http.request({ hostname: "127.0.0.1", port: 18180, path: url, method,
      headers: { "content-type": "application/x-www-form-urlencoded", "content-length": Buffer.byteLength(body) }, timeout: 10000 }, response => {
      let data = ""; response.on("data", part => { if (data.length < 4096) data += part; });
      response.on("end", () => resolve({ status: response.statusCode, pong: data.includes('"pong"') }));
    });
    request.on("error", reject); request.on("timeout", () => request.destroy()); request.end(body);
  });
}
async function debuggerURL() {
  for (let n = 0; n < 100; n++) {
    try {
      const data = await new Promise((resolve, reject) => {
        http.get("http://127.0.0.1:19222/json/version", response => {
          let text = ""; response.on("data", part => text += part); response.on("end", () => resolve(JSON.parse(text)));
        }).on("error", reject);
      });
      return data.webSocketDebuggerUrl;
    } catch { await sleep(100); }
  }
  throw new Error("browser_debugger_start_failed");
}
class CDP {
  constructor(socket) {
    this.socket = socket; this.next = 1; this.pending = new Map(); this.listeners = new Map();
    socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const item = this.pending.get(message.id); if (!item) return;
        this.pending.delete(message.id); clearTimeout(item.timer);
        if (message.error) item.reject(new Error("cdp_command_failed_" + item.method)); else item.resolve(message.result || {});
      } else for (const fn of this.listeners.get(message.method) || []) fn(message.params || {}, message.sessionId);
    });
  }
  on(method, fn) { if (!this.listeners.has(method)) this.listeners.set(method, []); this.listeners.get(method).push(fn); }
  send(method, params = {}, sessionId) {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error("cdp_timeout_" + method)); }, 30000);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  async evaluate(session, expression) {
    const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, session);
    if (result.exceptionDetails) {
      const detail = result.exceptionDetails.exception || {};
      const kind = /^[A-Za-z]+Error$/.test(detail.className || "") ? detail.className : "JavaScriptError";
      const nullDOM = /^TypeError: Cannot (read|set) properties of null/.test(detail.description || "");
      throw new Error("browser_evaluation_" + kind + (nullDOM ? "_null_dom" : ""));
    }
    return result.result?.value;
  }
  async wait(session, expression, milliseconds = 45000) {
    const end = Date.now() + milliseconds;
    while (Date.now() < end) {
      try { if (await this.evaluate(session, expression)) return; } catch {}
      await sleep(200);
    }
    throw new Error("ui_condition_timeout");
  }
}
async function main() {
  const mode = process.argv[2] || "guard";
  const input = mode === "guard" ? { users: ["ndf-ui-synthetic"], fixture_name: "ndf-ui-guard-fixture", fixture_title: "UI guard fixture" } : JSON.parse(fs.readFileSync("/run/ui-input.json", "utf8"));
  const proxy = createProxy(input);
  await new Promise(resolve => proxy.server.listen(18180, "127.0.0.1", resolve));
  const checks = {};
  completedChecks = checks;
  let chrome, cdp, activeSession;
  try {
    for (const url of ["http://example.com/", "http://127.0.0.1:8081/", "http://dalali.ndftechlabs.com/"]) {
      if ((await requestProxy(url)).status !== 403) throw new Error("proxy_origin_guard_failed");
    }
    if ((await requestProxy(ORIGIN + "/api/method/frappe.client.insert", "POST", "doc=%7B%22doctype%22%3A%22Sales%20Invoice%22%7D")).status !== 403) throw new Error("proxy_mutation_guard_failed");
    const ping = await requestProxy(ORIGIN + "/api/method/ping");
    if (ping.status !== 200 || !ping.pong) throw new Error("proxy_private_upstream_failed");
    if (safeSocketPacket('42/dalali-rehearsal.invalid,["submit_invoice","Sales Invoice"]')) throw new Error("proxy_socket_guard_failed");
    checks.proxy_synthetic_origin_action_guard = true;
    currentStep = "browser_start";
    chrome = spawn("/opt/ndf-ui/chrome", ["--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions", "--disable-default-apps",
      "--disable-breakpad", "--disable-crash-reporter", "--metrics-recording-only", "--mute-audio", "--disable-quic", "--dns-prefetch-disable",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      "--proxy-server=http://127.0.0.1:18180", "--proxy-bypass-list=<-loopback>", "--user-data-dir=/tmp/ui-profile",
      "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=19222", "about:blank"], { stdio: ["ignore", "ignore", "ignore"] });
    const socket = new WebSocket(await debuggerURL());
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", () => reject(new Error("cdp_connection_failed")), { once: true }); });
    cdp = new CDP(socket);
    let jsExceptions = 0;
    cdp.on("Runtime.exceptionThrown", event => {
      jsExceptions++;
      const detail = event.exceptionDetails?.exception || {};
      const kind = /^[A-Za-z]+Error$/.test(detail.className || "") ? detail.className : "JavaScriptError";
      jsErrorKinds[kind] = (jsErrorKinds[kind] || 0) + 1;
      const property = /Cannot (?:read|set) properties of (?:null|undefined) \((?:reading|setting) '([A-Za-z0-9_]+)'\)/.exec(detail.description || "");
      const frame = event.exceptionDetails?.stackTrace?.callFrames?.[0];
      let file = "inline_or_other";
      try { const name = new URL(frame?.url || "").pathname.split("/").pop(); if (/^[A-Za-z0-9_.-]+\.js$/.test(name)) file = name; } catch {}
      if (jsErrorSites.length < 10) jsErrorSites.push({ kind, property: property?.[1] || null, file,
        function: /^[A-Za-z0-9_.$ ]{0,100}$/.test(frame?.functionName || "") ? frame?.functionName : "code" });
      if (String(detail.description || "").includes("Content Security Policy") || String(detail.description || "").includes("unsafe-eval")) cspEvalBlocked = true;
    });
    cdp.on("Log.entryAdded", event => {
      if (String(event.entry?.text || "").includes("unsafe-eval")) cspEvalBlocked = true;
    });
    const context = await cdp.send("Target.createBrowserContext", { disposeOnDetach: true,
      proxyServer: "http://127.0.0.1:18180", proxyBypassList: "<-loopback>" });
    await cdp.send("Browser.setDownloadBehavior", { behavior: "deny", browserContextId: context.browserContextId });
    const target = await cdp.send("Target.createTarget", { url: "about:blank", browserContextId: context.browserContextId });
    const attached = await cdp.send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
    const session = attached.sessionId;
    activeSession = session;
    await cdp.send("Log.enable", {}, session);
    await cdp.send("Page.enable", {}, session); await cdp.send("Runtime.enable", {}, session); await cdp.send("Network.enable", {}, session);
    cdp.on("Network.loadingFailed", event => {
      const code = /^net::[A-Z_]+$/.test(event.errorText || "") ? event.errorText : "network_failure";
      networkErrors[code] = (networkErrors[code] || 0) + 1;
    });
    await cdp.send("Network.setBypassServiceWorker", { bypass: true }, session);
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: `(()=>{
      window.__ndfCspEvalBlocked=false;
      window.addEventListener('securitypolicyviolation',e=>{if(e.blockedURI==='eval'||e.sample?.includes('Function'))window.__ndfCspEvalBlocked=true});
      try { if(navigator.serviceWorker) navigator.serviceWorker.register=()=>Promise.reject(new Error('service_worker_blocked')); } catch {}
      try { Object.defineProperty(window,'RTCPeerConnection',{value:undefined}); } catch {}
      try { navigator.sendBeacon=()=>false; } catch {}
      window.open=()=>null;
    })();` }, session);
    await cdp.send("Page.navigate", { url: "data:text/html,<p>ndf-browser-guard</p>" }, session);
    const deniedBefore = proxy.stats.deniedOrigins;
    await cdp.send("Page.navigate", { url: "http://example.com/" }, session);
    await cdp.wait(session, "document.readyState==='complete' && document.body && document.body.textContent.includes('Origin denied')", 10000);
    if (proxy.stats.deniedOrigins <= deniedBefore) throw new Error("browser_proxy_not_enforced");
    checks.normal_sandbox_and_browser_proxy_guard = true;
    const actionBaseline = proxy.stats.deniedActions, frameBaseline = proxy.stats.deniedSocketFrames;
    if (mode === "guard") {
      await cdp.send("Target.disposeBrowserContext", { browserContextId: context.browserContextId });
      return { ui_request_action_guard_passed: true, customer_pages_loaded: false, sandbox_disabled: false, checks };
    }
    currentStep = "login";
    await cdp.send("Page.navigate", { url: ORIGIN + "/login" }, session);
    await cdp.wait(session, "document.readyState==='complete' && typeof login!=='undefined' && typeof login.call==='function' && window.frappe && typeof frappe.call==='function' && !!document.querySelector('#login_email') && !!document.querySelector('#login_password')", 60000);
    // Credentials remain in VM process memory/CDP; never included in diagnostics.
    await cdp.evaluate(session, `(()=>{const p=${JSON.stringify({ user: input.user, password: input.password })};
      const email=document.querySelector('#login_email'),password=document.querySelector('#login_password');
      email.value=p.user;password.value=p.password;
      email.dispatchEvent(new Event('input',{bubbles:true}));password.dispatchEvent(new Event('input',{bubbles:true}));
      const form=email.closest('form');
      const button=form?.querySelector('button[type=submit], .btn-login') || document.querySelector('.for-login .btn-login, .btn-login');
      if(button)button.click();else if(form)form.requestSubmit();else throw new Error('login_form_selector_missing');
      return true;})()`);
    await cdp.wait(session, "window.frappe && frappe.session && frappe.session.user==='Administrator'", 60000);
    currentStep = "desk_boot";
    await cdp.send("Page.navigate", { url: ORIGIN + "/desk" }, session);
    await cdp.wait(session, "window.frappe && frappe.session && frappe.session.user==='Administrator' && !!document.querySelector('.layout-main-section, .desk-container, #body')", 60000);
    await cdp.evaluate(session, "new Promise(resolve=>frappe.after_ajax(()=>resolve(true)))");
    checks.real_browser_login_desk = true;
    checks.read_views = {};
    checks.read_forms = {};
    for (const doctype of ["Item", "Website Item", "Sales Order", "Sales Invoice", "Employee", "Loan"]) {
      currentStep = "read_view_" + doctype.replaceAll(" ", "_");
      await cdp.evaluate(session, `Promise.resolve(frappe.set_route('List',${JSON.stringify(doctype)})).then(()=>true)`);
      await cdp.wait(session, `window.cur_list && cur_list.doctype===${JSON.stringify(doctype)} && !!document.querySelector('.list-row-container, .list-view-container, .frappe-list')`);
      await cdp.evaluate(session, "new Promise(resolve=>frappe.after_ajax(()=>resolve(true)))");
      for (let n=0;n<150 && !proxy.stats.listResponses[doctype];n++) await sleep(200);
      if (proxy.stats.listResponses[doctype]?.status !== 200) throw new Error("list_data_response_not_successful");
      await cdp.evaluate(session, "new Promise(resolve=>frappe.after_ajax(()=>resolve(true)))");
      await cdp.wait(session, "Array.isArray(cur_list.data)");
      checks.read_views[doctype] = true;
      currentStep = "read_form_" + doctype.replaceAll(" ", "_");
      const available = await cdp.evaluate(session, "!!cur_list.data[0]?.name");
      if (available) {
        // Existing record names never leave this VM page/CDP process.
        await cdp.evaluate(session, "Promise.resolve(frappe.set_route('Form',cur_list.doctype,cur_list.data[0].name)).then(()=>true)");
        await cdp.wait(session, `window.cur_frm && cur_frm.doctype===${JSON.stringify(doctype)} && !!cur_frm.doc?.name && !cur_frm.doc.__islocal`);
        await cdp.evaluate(session, "new Promise(resolve=>frappe.after_ajax(()=>resolve(true)))");
        checks.read_forms[doctype] = { rendered: true, dirty: await cdp.evaluate(session, "!!cur_frm.is_dirty()") };
      } else checks.read_forms[doctype] = { rendered: false, no_list_rows: true };
    }
    currentStep = "synthetic_note";
    await cdp.evaluate(session, `frappe.set_route('Form','Note',${JSON.stringify(input.fixture_name)});true`);
    await cdp.wait(session, `window.cur_frm && cur_frm.doctype==='Note' && cur_frm.doc.name===${JSON.stringify(input.fixture_name)}`);
    await cdp.evaluate(session, "new Promise(resolve=>frappe.after_ajax(()=>resolve(true)))");
    currentStep = "synthetic_note_set_content";
    await cdp.evaluate(session, `window.__ndfNoteSaveText='UI rehearsal save proof. Synthetic nonfinancial test only. Run '+Date.now();
      cur_frm.set_value('content',window.__ndfNoteSaveText).then(()=>true)`);
    currentStep = "synthetic_note_save_click";
    await cdp.wait(session, "cur_frm.is_dirty() && !!cur_frm.page.btn_primary?.is(':visible')");
    const saveButton = await cdp.evaluate(session, `(()=>{const b=cur_frm.page.btn_primary?.[0];
      if(!b||b.disabled) return null; const r=b.getBoundingClientRect();return r.width&&r.height?{x:r.x+r.width/2,y:r.y+r.height/2}:null})()`);
    if (!saveButton) throw new Error("note_save_button_unavailable");
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...saveButton }, session);
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...saveButton }, session);
    await cdp.wait(session, "cur_frm && !cur_frm.is_dirty()");
    currentStep = "synthetic_note_reload";
    await cdp.evaluate(session, "cur_frm.reload_doc().then(()=>true)");
    if (!await cdp.evaluate(session, "cur_frm.doc.content===window.__ndfNoteSaveText")) throw new Error("note_ui_save_persistence_failed");
    checks.synthetic_note_ui_save = true;
    checks.synthetic_note_save_reload_persistence = true;
    currentStep = "guest_homepage";
    const guest = await cdp.send("Target.createBrowserContext", { disposeOnDetach: true,
      proxyServer: "http://127.0.0.1:18180", proxyBypassList: "<-loopback>" });
    await cdp.send("Browser.setDownloadBehavior", { behavior: "deny", browserContextId: guest.browserContextId });
    const guestTarget = await cdp.send("Target.createTarget", { url: "about:blank", browserContextId: guest.browserContextId });
    const guestAttached = await cdp.send("Target.attachToTarget", { targetId: guestTarget.targetId, flatten: true });
    await cdp.send("Page.enable", {}, guestAttached.sessionId); await cdp.send("Runtime.enable", {}, guestAttached.sessionId);
    await cdp.send("Network.enable", {}, guestAttached.sessionId); await cdp.send("Network.setBypassServiceWorker", { bypass: true }, guestAttached.sessionId);
    await cdp.send("Page.navigate", { url: ORIGIN + "/" }, guestAttached.sessionId);
    await cdp.wait(guestAttached.sessionId, "document.readyState==='complete' && !!document.querySelector('main, .page_content, .web-page-content, .page-container')");
    checks.guest_homepage_render = true;
    currentStep = "guest_permissions";
    const deniedStatus = await cdp.evaluate(guestAttached.sessionId, `fetch(${JSON.stringify(ORIGIN + "/api/resource/Sales Invoice?limit_page_length=1")},
      {credentials:'same-origin'}).then(r=>r.status)`);
    checks.guest_financial_data_denied = [401,403].includes(deniedStatus);
    const result = { automated_ui_checks_passed: true, business_signoff_pending: true, checks,
      js_exception_count: jsExceptions, js_error_kinds: jsErrorKinds, js_error_sites: jsErrorSites,
      guard: { allowed: proxy.stats.allowed, denied_origins: proxy.stats.deniedOrigins,
        list_responses: proxy.stats.listResponses,
        denied_actions: proxy.stats.deniedActions, denied_socket_frames: proxy.stats.deniedSocketFrames, denied_methods: proxy.stats.deniedMethods },
      screenshots_dom_records_exported: false, source_changed: false, public_cutover: false };
    if (jsExceptions || proxy.stats.deniedActions > actionBaseline || proxy.stats.deniedSocketFrames > frameBaseline) result.automated_ui_checks_passed = false;
    if (!checks.guest_financial_data_denied || Object.values(checks.read_forms).some(x=>!x.rendered || x.dirty)) result.automated_ui_checks_passed = false;
    await cdp.send("Target.disposeBrowserContext", { browserContextId: context.browserContextId });
    await cdp.send("Target.disposeBrowserContext", { browserContextId: guest.browserContextId });
    return result;
  } catch (error) {
    if (cdp && activeSession) {
      try { browserState = await cdp.evaluate(activeSession, `({on_login:location.pathname==='/login',on_desk:location.pathname.startsWith('/desk'),
        frappe_defined:typeof frappe!=='undefined',session_defined:typeof frappe!=='undefined'&&!!frappe.session,
        is_administrator:typeof frappe!=='undefined'&&frappe.session?.user==='Administrator',
        list_global_defined:typeof cur_list!=='undefined',list_is_item:typeof cur_list!=='undefined'&&cur_list?.doctype==='Item',
        list_doctype:typeof cur_list!=='undefined'&&['Item','Website Item','Sales Order','Sales Invoice','Employee','Loan','Note'].includes(cur_list?.doctype)?cur_list.doctype:'none_or_other',
        route_is_list:typeof frappe!=='undefined'&&frappe.get_route?.()[0]==='List',
        route_is_workspace:typeof frappe!=='undefined'&&frappe.get_route?.()[0]==='Workspaces',
        page_csp_eval_blocked:window.__ndfCspEvalBlocked===true,
        form_is_note:typeof cur_frm!=='undefined'&&cur_frm?.doctype==='Note',
        form_dirty:typeof cur_frm!=='undefined'&&!!cur_frm?.is_dirty(),
        list_dom_present:!!document.querySelector('.list-row-container, .list-view-container, .frappe-list'),
        document_complete:document.readyState==='complete',login_error_visible:!!document.querySelector('.login-error-banner:not(.hidden)')})`); } catch {}
    }
    throw error;
  } finally {
    lastGuard = { denied_methods: proxy.stats.deniedMethods, denied_origins: proxy.stats.deniedOrigins,
      denied_actions: proxy.stats.deniedActions, denied_socket_frames: proxy.stats.deniedSocketFrames,
      denied_route_classes: proxy.stats.deniedRouteClasses, list_responses: proxy.stats.listResponses,
      denied_read_shapes: proxy.stats.deniedReadShapes };
    if (cdp) { try { await cdp.send("Browser.close"); } catch {} cdp.socket.close(); }
    if (chrome && chrome.exitCode === null) { chrome.kill("SIGTERM"); await sleep(1000); if (chrome.exitCode === null) chrome.kill("SIGKILL"); }
    proxy.server.closeAllConnections(); await new Promise(resolve => proxy.server.close(resolve));
  }
}
main().then(result => console.log(JSON.stringify(result))).catch(error => {
  const category = /^[a-zA-Z0-9_ -]{1,100}$/.test(error.message || "") ? error.message : "unclassified_ui_error";
  console.error(JSON.stringify({ ui_runner_failed: true, step: currentStep, category, guard: lastGuard,
    network_errors: networkErrors, browser_state: browserState, js_error_kinds: jsErrorKinds,
    js_error_sites: jsErrorSites, csp_eval_blocked: cspEvalBlocked, completed_checks: completedChecks,
    dom_credentials_records_exported: false })); process.exitCode = 1;
});
