#!/usr/bin/env python3
"""Fail-closed checks of the running staging connector's public config metadata."""


def validate_config(document, audience):
    def deny():
        raise RuntimeError("Received tunnel configuration differs from the approved guarded staging route")
    if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] < 0:
        deny()
    config = document.get("config")
    if not isinstance(config, dict):deny()
    rules = config.get("ingress")
    if not isinstance(rules, list) or len(rules) != 2 or not all(isinstance(rule, dict) for rule in rules):deny()
    route, fallback = rules
    if route.get("hostname") != "d-stage.ndftechlabs.com" or route.get("path") not in (None, ""):
        deny()
    if route.get("service") not in ("http://127.0.0.1:18089", "http://127.0.0.1:18089/"):
        deny()
    if fallback.get("hostname") not in (None, "") or fallback.get("path") not in (None, "") or fallback.get("service") != "http_status:404":
        deny()
    warp = config.get("warp-routing", {})
    if not isinstance(warp, dict) or warp.get("enabled") not in (None, False):deny()
    for origin in (config.get("originRequest", {}), route.get("originRequest", {})):
        if not isinstance(origin, dict) or origin.get("noTLSVerify") not in (None, False) or origin.get("bastionMode") not in (None, False):deny()
        if origin.get("proxyType") not in (None, "") or origin.get("httpHostHeader") not in (None, "", "d-stage.ndftechlabs.com"):deny()
    access = route.get("originRequest", {}).get("access")
    if not isinstance(access, dict) or access.get("required") is not True or access.get("teamName") != "ndftech":deny()
    if access.get("audTag") != [audience] or access.get("environment") not in (None, ""):deny()
    return {"config_version": document["version"], "hostname": "d-stage.ndftechlabs.com",
        "service": "http://127.0.0.1:18089", "all_paths": True, "protect_with_access": True,
        "team_name": "ndftech", "single_expected_audience": True, "unknown_hosts_return_404": True,
        "private_network_routing_enabled": False}
