#!/usr/bin/env python3
"""Reconcile ONLY the recorded staging loopback mapping to the same owned gateway."""

import http.client
import ipaddress
from pathlib import Path
import socket
import stat


GATEWAY = "ndf-dalali-stage-gateway-v1"


def expected_spec(recorded, container, network, ports):
    def deny():raise RuntimeError("Staging mapping identity/scope differs; no unknown mapping may be replaced")
    if not isinstance(recorded, dict) or type(recorded.get("id")) is not int or recorded["id"] <= 0:deny()
    if recorded.get("gateway_id") != container.get("Id") or not container.get("State", {}).get("Running"):deny()
    if not network.get("Internal") or network.get("EnableIPv6"):deny()
    networks = container.get("NetworkSettings", {}).get("Networks", {})
    if set(networks) != {network.get("Name")}:deny()
    old = recorded.get("spec", {})
    if set(old) != {"proto", "parentIP", "parentPort", "childIP", "childPort"}:deny()
    if old.get("proto") != "tcp" or old.get("parentIP") != "127.0.0.1" or old.get("parentPort") != 18089 or old.get("childPort") != 18089:deny()
    rows = [row for row in ports if row.get("id") == recorded["id"]]
    if len(rows) != 1 or rows[0].get("spec") != old:deny()
    if len([row for row in ports if row.get("spec", {}).get("parentPort") == 18089]) != 1:deny()
    current = networks[network["Name"]].get("IPAddress")
    try:
        subnets = [ipaddress.ip_network(row["Subnet"]) for row in network["IPAM"]["Config"] if row.get("Subnet")]
        for value in (old["childIP"], current):
            address = ipaddress.ip_address(value)
            if address.version != 4 or not any(address in subnet for subnet in subnets):deny()
    except (ValueError, KeyError, TypeError):deny()
    return {**old, "childIP": current}


def reconcile(r, namespace, allow_retarget=False):
    stage = r.state["staging"]
    recorded = stage["loopback_mapping"]
    container = r.owned("container", GATEWAY)
    if container["Image"] != namespace["APP_IMAGE"]:raise RuntimeError("Staging mapping gateway image differs")
    network = r.owned("network", namespace["NETWORK"])
    ports = r.rootless_port_api("GET", "/v1/ports") or []
    expected = expected_spec(recorded, container, network, ports)
    if expected == recorded["spec"]:return False
    if not allow_retarget:raise RuntimeError("Owned staging gateway IP changed; explicit loopback retarget required")
    # Never remove by port number, wildcard or unknown ID. The complete existing
    # spec, gateway ID/image, private network and exact parent bind were verified.
    api = Path("/run/user/5001/dockerd-rootless/api.sock")
    info = api.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 5001:raise RuntimeError("Unexpected dedicated RootlessKit API")
    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(10);self.sock.connect(str(api))
    connection = Connection("rootlesskit")
    try:
        connection.request("DELETE", "/v1/ports/" + str(recorded["id"]))
        response = connection.getresponse();response.read(65537)
        if response.status != 200:raise RuntimeError("Owned staging mapping removal failed; no unknown mapping touched")
    finally:connection.close()
    remaining = r.rootless_port_api("GET", "/v1/ports") or []
    other_ports = [row for row in ports if row["id"] != recorded["id"]]
    if sorted(remaining,key=lambda row:row['id']) != sorted(other_ports,key=lambda row:row['id']):
        raise RuntimeError("Rootless mapping set changed unexpectedly; do not overwrite other mappings")
    created = r.rootless_port_api("POST", "/v1/ports", expected)
    if not created or created.get("spec") != expected:raise RuntimeError("Owned staging retarget failed; leave new connector closed")
    updated = {"id": created["id"], "spec": expected, "gateway_id": container["Id"]}
    stage.setdefault("mapping_history", []).append({"before": recorded, "after": updated,
        "reason": "same-owned-gateway private IP changed on restart"})
    stage["loopback_mapping"] = updated;r.save()
    listeners = [line.split()[3] for line in r.run(["ss", "-H", "-lnt", "sport = :18089"]).splitlines() if len(line.split()) >= 4]
    if listeners != ["127.0.0.1:18089"]:raise RuntimeError("Reconciled staging mapping not exclusively loopback")
    return True
