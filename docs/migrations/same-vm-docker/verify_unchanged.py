#!/usr/bin/env python3
"""Read-only pre-change check against audit.py JSON. Never repairs differences."""

import json
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_unchanged.py /path/to/baseline-audit.json")
    baseline = json.loads(Path(sys.argv[1]).read_text())
    expected = {item["path"]: item["sha256"] for item in baseline["nginx_configs"]}
    expected["/home/frappe/bench/config/supervisor.conf"] = baseline["supervisor_config_sha256"]
    expected["/home/frappe/bench/sites/common_site_config.json"] = baseline["common_config_sha256"]
    for site in baseline["sites"]:
        expected[f"/home/frappe/bench/sites/{site['site']}/site_config.json"] = site["config_sha256"]
    for config in baseline["cloudflare"]["config_files"]:
        expected[config["path"]] = config["sha256"]
    code = ("expected = " + repr(expected) + "\nexpected_host = " + repr(baseline["hostname"])
            + "\nexpected_routes = " + repr(baseline["routes"])
            + "\nexpected_forward_policy = " + repr(baseline["iptables"]["FORWARD"].get("policy"))
            + "\n") + '''
import hashlib,json,pathlib,shutil,subprocess
hostname=subprocess.run(['hostname'],capture_output=True,text=True,check=True).stdout.strip()
if hostname!=expected_host:
    raise SystemExit('Unexpected host; refusing to inspect another server')
changed=[]
for path,digest in expected.items():
    try: actual=hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except OSError: actual=None
    if actual!=digest: changed.append(path)
units=['nginx','supervisor','mariadb','redis-server','postfix@-','dovecot','rspamd','opendkim','cloudflared','pm2-root','netdata','fail2ban','ssh']
services={unit:subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip() for unit in units}
status=subprocess.run(['supervisorctl','status'],capture_output=True,text=True)
bench_processes=[line for line in status.stdout.splitlines() if line.startswith(('bench-web:','bench-workers:','bench-redis:'))]
bench_running=status.returncode==0 and len(bench_processes)==7 and all('RUNNING' in line for line in bench_processes)
def route_signature(rows):
    keys=('dst','gateway','dev','table','metric','prefsrc')
    return sorted(json.dumps({key:row.get(key) for key in keys},sort_keys=True) for row in rows)
routes_unchanged=True
for family in ('-4','-6'):
    result=subprocess.run(['ip',family,'-j','route','show'],capture_output=True,text=True)
    if result.returncode or route_signature(json.loads(result.stdout))!=route_signature(expected_routes[family]):
        routes_unchanged=False
policy=subprocess.run(['iptables','-S','FORWARD'],capture_output=True,text=True)
forward_policy=next((line for line in policy.stdout.splitlines() if line.startswith('-P ')),None)
forward_unchanged=policy.returncode==0 and forward_policy==expected_forward_policy
wireguard=pathlib.Path('/sys/class/net/wg0').exists()
print(json.dumps({'hostname':hostname,'config_fingerprints_unchanged':not changed,'changed_config_paths':changed,
                  'protected_services':services,'native_bench_processes_running':bench_running,
                  'docker_found_on_root_path':bool(shutil.which('docker')),
                  'wireguard_interface_present':wireguard,'routes_unchanged':routes_unchanged,
                  'forward_policy_unchanged':forward_unchanged},indent=2))
if changed or not bench_running or not wireguard or not routes_unchanged or not forward_unchanged or any(value!='active' for value in services.values()):
    raise SystemExit(1)
'''
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=30", "ssh.ndftechlabs.com", "sudo -n python3 -"],
                            input=code, capture_output=True, text=True, timeout=70)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
