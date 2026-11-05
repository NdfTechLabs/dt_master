#!/usr/bin/env python3
"""Runs only inside the private rehearsal client; prints no cookies/data/credentials."""

import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit


def safe_path(path):
    if not path.startswith("/") or path.startswith("//") or "\\" in path or urlsplit(path).netloc or ".." in Path(unquote(urlsplit(path).path)).parts:
        raise RuntimeError("Unsafe rehearsal request path")
    return path


def internal_redirect_path(location):
    redirect = urlsplit(location)
    if redirect.hostname and redirect.hostname not in {"frontend", "dalali-rehearsal.invalid"}:
        raise RuntimeError("Page redirects outside private clone")
    if redirect.scheme not in {"", "http"} or redirect.port not in {None, 8080}:
        raise RuntimeError("Page redirect requires unapproved transport/port")
    return safe_path((redirect.path or "/") + ("?" + redirect.query if redirect.query else ""))


def main():
    import requests
    auth = json.loads(Path("/run/ndf-http-auth.json").read_text())
    plan = json.loads(Path("/run/ndf-http-plan.json").read_text())
    base = "http://frontend:8080"
    def session():
        value = requests.Session()
        value.trust_env = False
        value.headers.update({"Host": "dalali-rehearsal.invalid", "Origin": "http://dalali-rehearsal.invalid"})
        return value
    guest, admin = session(), session()
    def get(client, path, **kwargs):
        return client.get(base + safe_path(path), timeout=20, allow_redirects=False, **kwargs)
    def internal_redirects(client, response):
        for _ in range(5):
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            response = get(client, internal_redirect_path(response.headers.get("Location", "")))
        raise RuntimeError("Too many private page redirects")
    result = {}
    ping = get(guest, "/api/method/ping")
    if ping.status_code != 200 or ping.json().get("message") != "pong":
        raise RuntimeError("HTTP ping invalid")
    result["ping"] = True
    for path in ("/site_config.json", "/common_site_config.json", "/protected/private/files/ndf-probe"):
        if get(guest, path).status_code != 404:
            raise RuntimeError("Private config/protected location exposed")
    result["config_and_internal_locations_denied"] = True
    denied = get(guest, "/api/method/frappe.client.get_count", params={"doctype": "User"})
    if denied.status_code not in {401, 403}:
        raise RuntimeError("Guest privileged API access not denied")
    response = admin.post(base + "/api/method/login", data={"usr": auth["user"], "pwd": auth["password"]}, timeout=30, allow_redirects=False)
    if response.status_code != 200 or get(admin, "/api/method/frappe.auth.get_logged_user").json().get("message") != "Administrator":
        raise RuntimeError("Clone-only login failed")
    result["clone_operator_login"] = True
    desk = internal_redirects(admin, get(admin, "/app"))
    if desk.status_code != 200 or "text/html" not in desk.headers.get("Content-Type", ""):
        raise RuntimeError("Authenticated Desk page not rendered")
    result["authenticated_desk_html"] = True
    website = internal_redirects(guest, get(guest, "/"))
    if website.status_code != 200 or "text/html" not in website.headers.get("Content-Type", ""):
        raise RuntimeError("Private website HTML not rendered")
    result["guest_website_html"] = True
    for doctype, expected in plan["doctype_counts"].items():
        response = get(admin, "/api/method/frappe.client.get_count", params={"doctype": doctype})
        if response.status_code != 200 or int(response.json().get("message", -1)) != expected:
            raise RuntimeError("Representative record count differs")
    result["record_count_checks"] = len(plan["doctype_counts"])
    fixture = get(admin, "/api/resource/Note/" + plan["fixture_name"])
    if fixture.status_code != 200 or fixture.json().get("data", {}).get("title") != plan["fixture_title"]:
        raise RuntimeError("Clone-only fixture read failed")
    denied = get(guest, "/api/resource/Note/" + plan["fixture_name"])
    if denied.status_code not in {401, 403}:
        raise RuntimeError("Guest read of private fixture not denied")
    result["clone_fixture_read_and_guest_denial"] = True
    for path in plan["assets"]:
        response = get(guest, path)
        if response.status_code != 200 or not response.content or response.headers.get("Content-Type", "").startswith("text/html"):
            raise RuntimeError("Compiled asset not served correctly")
    if not plan["assets"]:
        raise RuntimeError("No compiled assets tested")
    result["compiled_assets_served"] = len(plan["assets"])
    private = auth.get("private_probe")
    if not private:
        raise RuntimeError("No private attachment available for authorization test")
    if get(guest, private["url"]).status_code not in {401, 403, 404}:
        raise RuntimeError("Anonymous private attachment access not denied")
    response = get(admin, private["url"])
    if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != private["sha256"]:
        raise RuntimeError("Authenticated private attachment bytes differ")
    result["private_attachment_guest_denial_and_authenticated_hash"] = True
    sid = admin.cookies.get("sid")
    if not sid or sid == "Guest":
        raise RuntimeError("Clone authenticated cookie unavailable")
    node = r'''const fs=require('fs');
const {io}=require('/home/frappe/bench/apps/frappe/node_modules/socket.io-client');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const socket=io('http://frontend:8080/dalali-rehearsal.invalid',{
 transports:[data.transport],reconnection:false,timeout:10000,
 extraHeaders:{Host:'dalali-rehearsal.invalid',Origin:'http://dalali-rehearsal.invalid',Cookie:data.cookie}
});
const timer=setTimeout(()=>{socket.close();process.exit(2)},15000);
socket.on('connect',()=>{clearTimeout(timer);socket.close();console.log('authenticated socket connected');process.exit(0)});
socket.on('connect_error',()=>{clearTimeout(timer);socket.close();console.error('socket authentication/connect failed');process.exit(1)});'''
    for transport in ("polling", "websocket"):
        connected = subprocess.run(["node", "-e", node], input=json.dumps({"cookie": "sid=" + sid, "transport": transport}), capture_output=True, text=True, timeout=20)
        if connected.returncode != 0 or "authenticated socket connected" not in connected.stdout:
            raise RuntimeError("Authenticated Socket.IO probe failed: " + transport)
    result["authenticated_socketio_polling"] = True
    result["authenticated_socketio_websocket"] = True
    guest.close()
    admin.close()
    result["external_requests_followed"] = False
    return result


if __name__ == "__main__":
    print(json.dumps(main()))
