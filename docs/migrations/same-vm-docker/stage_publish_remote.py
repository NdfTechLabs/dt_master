#!/usr/bin/env python3
"""Bounded Guest preview activation with exact-code proof and config rollback."""

from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request


CODE = Path("/opt/ndf-rootless/dalali-stage-v1")
GATEWAY = "ndf-dalali-stage-gateway-v1"
SITE = "dalali-rehearsal.invalid"
SITE_BACKUP = "ndf-site-config-before-guest-review-v1.json"


def synthetic_proof_passed(output):
    summary = {}
    for line in output.splitlines():
        clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
        match = re.fullmatch(r"\s*(?:#|ℹ)?\s*(tests|pass|fail|cancelled|skipped)\s+(\d+)\s*", clean)
        if match:summary[match.group(1)] = int(match.group(2))
    return summary == {"tests": 32, "pass": 32, "fail": 0, "cancelled": 0, "skipped": 0}


def publish(request, helper):
    ns = {"__name__": "stage_guest_publish_support"}
    exec(compile(helper, "rehearsal_remote.py", "exec"), ns)
    r = ns["Rehearsal"](request)
    r.health();r.runtime_egress()
    root = ns["WORK"] / "stage-v1"
    state = r.state["staging"]
    if request.get("readiness_confirmed") is not True or not state.get("connector_started"):
        raise RuntimeError("Protected reviewer readiness and managed connector required")
    if state.get("guest_review_enabled"):
        raise RuntimeError("Guest review already active; inspect instead of replaying activation")
    if state.get("guest_publish_attempted") and not (request.get("retry_rolled_back") and state.get("guest_rollback_verified")):
        raise RuntimeError("Prior activation requires diagnosis and verified rollback before explicit retry")
    def digest_bytes(data):return hashlib.sha256(data).hexdigest()
    def guard():
        r.guard(reserve=4 * 1024**2)
        free = shutil.disk_usage("/").free
        if max(0, state["initial_free_bytes"] - free) + 4 * 1024**2 > 64 * 1024**2:
            raise RuntimeError("Guest stage-code 64-MiB subcap reached")
        if max(0, r.state["ui"]["initial_free_bytes"] - free) + 4 * 1024**2 > 1024**3:
            raise RuntimeError("Guest review within UI 1-GiB subcap required")
    def write_new(path, data, mode=0o444):
        if isinstance(data, str):data=data.encode()
        if path.is_symlink():raise RuntimeError("Unsafe guest staging file")
        if path.exists():
            if path.read_bytes() != data or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
                raise RuntimeError("Existing guest staging file differs; no overwrite")
        else:
            with open(path, "xb") as output:output.write(data)
            path.chmod(mode)
    def inplace(path, data):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:raise RuntimeError("Unsafe bound gateway configuration")
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+b") as output:
            if os.fstat(output.fileno()).st_ino != info.st_ino:raise RuntimeError("Gateway config inode drift")
            output.write(data);output.truncate();output.flush();os.fsync(output.fileno())
    def replace_code(path, data):
        temporary=path.with_name(path.name + ".guest-new")
        with open(temporary, "xb") as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        temporary.chmod(0o444);temporary.replace(path)
    def mapping():
        mapping_support={"__name__":"stage_owned_mapping_support"}
        exec(compile(request["mapping_support"],"stage_mapping.py","exec"),mapping_support)
        mapping_support["reconcile"](r,ns,allow_retarget=request.get("repair_owned_mapping") is True)
        item=r.owned("container", GATEWAY)
        expected=state["loopback_mapping"]["spec"]
        if item["NetworkSettings"]["Networks"][ns["NETWORK"]]["IPAddress"] != expected["childIP"]:
            raise RuntimeError("Gateway address changed; do not replace retained mapping silently")
        ports=r.rootless_port_api("GET", "/v1/ports") or []
        if len([row for row in ports if row["id"]==state["loopback_mapping"]["id"] and row.get("spec")==expected]) != 1:
            raise RuntimeError("Owned gateway mapping drift")
    def metrics(path):
        connection=http.client.HTTPConnection("127.0.0.1",18090,timeout=5)
        try:
            connection.request("GET",path);response=connection.getresponse();body=response.read(65537)
            if response.status!=200 or len(body)>65536:raise RuntimeError("Connector metadata/health unavailable")
            return body
        finally:connection.close()
    guard();mapping()
    old_gateway=(CODE/"stage_gateway.cjs").read_bytes()
    if digest_bytes(old_gateway)!=request["old_gateway_sha256"]:raise RuntimeError("Expected readiness entrypoint version differs")
    if ns["digest"](CODE/"stage_access.cjs")!=digest_bytes(request["files"]["stage_access.cjs"].encode()):
        raise RuntimeError("Access identity boundary drift")
    runtime=CODE/"runtime/access.json";old_runtime=runtime.read_bytes();access=json.loads(old_runtime)
    if access.get("origin_enabled") is not False or access.get("issuer")!="https://ndftech.cloudflareaccess.com":
        raise RuntimeError("Expected readiness config differs")
    policy={"__name__":"stage_received_policy"};exec(compile(request["route_policy"],"stage_route_policy.py","exec"),policy)
    received=policy["validate_config"](json.loads(metrics("/config")),access["audience"]);metrics("/ready")
    keys=json.loads(Path("/var/lib/ndf-dalali-stage-keys/jwks.json").read_text())
    if not 0<=int(time.time())-keys["fetched_at"]<=3600:raise RuntimeError("Public signing keys stale")
    proof_hash=digest_bytes(json.dumps(request["files"],sort_keys=True).encode())
    proof=Path("/opt/ndf-rootless/dalali-stage-guest-proof-"+proof_hash[:16])
    if proof.is_symlink():raise RuntimeError("Unsafe guest proof directory")
    if not proof.exists():proof.mkdir(mode=0o755);proof.chmod(0o755)
    if proof.stat().st_uid!=0 or proof.stat().st_mode & 0o022:raise RuntimeError("Untrusted guest proof directory")
    for name,text in request["files"].items():
        if Path(name).name!=name:raise RuntimeError("Unexpected guest proof source name")
        write_new(proof/name,text)
    flags=["run","--rm","--pull=never","--label",ns["LABEL"],"--read-only","--user=1000:1000","--cap-drop=ALL",
        "--security-opt=no-new-privileges","--memory=96m","--memory-swap=96m","--cpus=0.25","--pids-limit=64",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=16777216,mode=1777","--mount","type=bind,src="+str(proof)+",dst=/opt/ndf-stage,readonly"]
    output=r.docker(flags+["--network=none","--label=ndf.role=stage-guest-synthetic-proof","--env=NDF_STAGE_SOURCE_DIR=/opt/ndf-stage",
        ns["APP_IMAGE"],"node","--test","--test-concurrency=1","/opt/ndf-stage/stage_access.test.cjs",
        "/opt/ndf-stage/stage_gateway.test.cjs","/opt/ndf-stage/stage_reverse.test.cjs"],timeout=180)
    if not synthetic_proof_passed(output):
        raise RuntimeError("Exact guest identity/action proof incomplete")
    state["guest_guard_proof_sha256"]=proof_hash;r.save()
    write_new(root/"gateway-entry-before-guest.cjs",old_gateway,mode=0o600)
    write_new(root/"gateway-config-before-guest.json",old_runtime,mode=0o600)
    control=r.state["role_containers"]["control"]
    def operator(code):
        return r.docker(["exec","--workdir=/home/frappe/bench/sites",control,"/usr/local/bin/ndf-native-exec",
            "/home/frappe/bench/env/bin/python","-c",code],timeout=120)
    before_site=json.loads(operator("import hashlib,json,pathlib; p=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid/site_config.json'); print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"))['sha256']
    overlay=r'''import hashlib,json,pathlib
root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid');path=root/'site_config.json'
if root.is_symlink() or path.is_symlink():raise RuntimeError('Unexpected clone config path')
raw=path.read_bytes();config=json.loads(raw)
if hashlib.sha256(raw).hexdigest()!=EXPECTED_BEFORE:raise RuntimeError('Clone config changed since preflight; no overwrite')
if config.get('db_host')!='db' or config.get('maintenance_mode')!=0 or config.get('host_name')!='http://dalali-rehearsal.invalid:8080':raise RuntimeError('Unexpected original clone config; no overwrite')
backup=root/'private/backups/ndf-site-config-before-guest-review-v1.json'
if backup.is_symlink():raise RuntimeError('Unsafe clone config backup')
if backup.exists():
 if backup.read_bytes()!=raw:raise RuntimeError('Existing clone backup differs')
else:
 with open(backup,'xb') as output:output.write(raw)
 backup.chmod(0o600)
config.update(maintenance_mode=1,allow_reads_during_maintenance=1,read_from_replica=0,
 host_name='https://d-stage.ndftechlabs.com',cookie_domain=None,pause_scheduler=1,mute_emails=1,disable_email_queue=1,disable_webhooks=1,developer_mode=0)
new=json.dumps(config,indent=2).encode();temporary=path.with_name('site_config.guest-new')
with open(temporary,'xb') as output:output.write(new)
temporary.chmod(0o600);temporary.replace(path)
print(json.dumps({'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest(),'read_only_http':True,'new_site_created':False}))'''
    rollback_site=r'''import hashlib,json,pathlib
root=pathlib.Path('/home/frappe/bench/sites/dalali-rehearsal.invalid');path=root/'site_config.json';backup=root/'private/backups/ndf-site-config-before-guest-review-v1.json'
if path.is_symlink() or backup.is_symlink():raise RuntimeError('Unexpected clone rollback symlink; no overwrite')
current=hashlib.sha256(path.read_bytes()).hexdigest()
if not backup.is_file():
 if current!=EXPECTED_BEFORE:raise RuntimeError('Clone config changed without a retained backup; no overwrite')
 print('clone-config-unchanged');raise SystemExit(0)
original=backup.read_bytes()
if hashlib.sha256(original).hexdigest()!=EXPECTED_BEFORE:raise RuntimeError('Retained clone config differs; no overwrite')
config=json.loads(original);config.update(maintenance_mode=1,allow_reads_during_maintenance=1,read_from_replica=0,
 host_name='https://d-stage.ndftechlabs.com',cookie_domain=None,pause_scheduler=1,mute_emails=1,disable_email_queue=1,disable_webhooks=1,developer_mode=0)
expected={'before_sha256':EXPECTED_BEFORE,'after_sha256':hashlib.sha256(json.dumps(config,indent=2).encode()).hexdigest()}
if current not in (expected['before_sha256'],expected['after_sha256']):raise RuntimeError('Clone config changed unexpectedly; do not overwrite')
if hashlib.sha256(original).hexdigest()!=expected['before_sha256']:raise RuntimeError('Retained clone config differs')
temporary=path.with_name('site_config.guest-rollback')
with open(temporary,'xb') as output:output.write(original)
temporary.chmod(0o600);temporary.replace(path)
print('clone-config-restored')'''
    site_receipt=None;site_attempted=False;gateway_changed=False;runtime_changed=False
    state['guest_publish_attempted']=True;state['guest_rollback_verified']=False;r.save()
    try:
        site_attempted=True
        site_receipt=json.loads(operator('EXPECTED_BEFORE = '+repr(before_site)+'\n'+overlay))
        r.docker(["restart","--time=15",r.state["role_containers"]["web"]],timeout=120)
        r.refresh_private_frontend()
        proof_sql=r'''import json,frappe
from frappe.app import init_request
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request
environ=EnvironBuilder(path='/',base_url='https://d-stage.ndftechlabs.com',headers={'X-Frappe-Site-Name':'dalali-rehearsal.invalid'}).get_environ()
blocked=False
try:
 init_request(Request(environ))
 if frappe.local.site!='dalali-rehearsal.invalid' or frappe.session.user!='Guest' or not frappe.flags.read_only or frappe.conf.db_host!='db':raise RuntimeError('Guest/read-only request scope differs')
 if frappe.db.sql('SELECT @@hostname,@@server_id')[0]!=('ndf-dalali-rehearsal-v1-db',65001):raise RuntimeError('Wrong DB')
 try:frappe.db.sql('UPDATE `tabUser` SET enabled=enabled WHERE 1=0')
 except frappe.InReadOnlyMode:blocked=True
 if not blocked:raise RuntimeError('Read-only SQL transaction not enforced')
 print(json.dumps({'guest_request_verified':True,'read_only_sql_mutation_denied':True,'affected_business_rows':0}))
finally:frappe.destroy()'''
        readonly=json.loads(operator(proof_sql))
        live=json.loads(r.docker(flags+["--network",ns["NETWORK"],"--dns=127.0.0.1","--label=ndf.role=stage-guest-origin-proof",
            ns["APP_IMAGE"],"node","/opt/ndf-stage/stage_live_probe.cjs"],timeout=180))
        if not live.get("guest_review_origin_proof_passed"):raise RuntimeError("Real Guest origin proof incomplete")
        for name in ("stage_policy.cjs","stage_reverse.cjs"):
            write_new(CODE/name,request["files"][name])
        new_gateway=request["files"]["stage_gateway.cjs"].encode()
        if ns["digest"](CODE/"stage_gateway.cjs")!=request["old_gateway_sha256"]:raise RuntimeError("Readiness entrypoint changed before activation")
        replace_code(CODE/"stage_gateway.cjs",new_gateway);gateway_changed=True
        new_access={**access,"origin_enabled":True,"review_mode":"guest-website-v1","review_site":SITE,"reviewer_readiness_verified":True}
        new_runtime=json.dumps(new_access,sort_keys=True).encode()
        if runtime.read_bytes()!=old_runtime:raise RuntimeError("Readiness config changed before activation")
        inplace(runtime,new_runtime);runtime_changed=True
        r.docker(["restart","--time=10",GATEWAY],timeout=90)
        mapping()
        headers=r.run(["curl","--silent","--show-error","--retry","5","--retry-connrefused","--retry-delay","1","--max-time","10",
            "--header","Host: d-stage.ndftechlabs.com","--dump-header","-","--output","/dev/null","http://127.0.0.1:18089/"],timeout=60)
        if not re.search(r"^HTTP/\S+ 403\b",headers) or "x-ndf-environment: dalali-staging-guest-review" not in headers.lower():
            raise RuntimeError("Live Guest gateway identity denial/marker differs")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        try:response=urllib.request.build_opener(NoRedirect).open(urllib.request.Request("https://d-stage.ndftechlabs.com/",method="HEAD"),timeout=20)
        except urllib.error.HTTPError as error:response=error
        with response:
            public_status=response.status;target=urllib.parse.urlsplit(response.headers.get("Location",""))
        if public_status not in (302,303,401,403) or public_status in (302,303) and (target.scheme!="https" or target.hostname!="ndftech.cloudflareaccess.com"):
            raise RuntimeError("Public anonymous access not denied by expected layer")
        r.health();r.runtime_egress();guard()
        policy["validate_config"](json.loads(metrics("/config")),access["audience"])
        state.update(guest_review_enabled=True,origin_enabled=True,operator_readiness_login_pending=False,
            guest_homepage_browser_confirmation_pending=True,review_mode="guest-website-v1",guest_site_config=site_receipt)
        r.save()
        result={"checked_utc":datetime.now(timezone.utc).isoformat(),"guest_website_connected":True,"public_hostname":"d-stage.ndftechlabs.com",
            "existing_clone_site":SITE,"new_site_database_or_data_copy":False,"exact_guard_proof_sha256":proof_hash,
            "sql_read_only_proof":readonly,"private_origin_proof":live,"public_anonymous_status":public_status,
            "login_desk_cart_private_files_business_writes":"blocked","browser_homepage_confirmation_pending":True,
            "native_changed":False,"credentials_cookies_raw_pages_exported":False}
        (root/"guest-website-result.json").write_text(json.dumps(result,indent=2));r.progress(json.dumps(result))
    except Exception:
        try:
            if gateway_changed or runtime_changed:r.docker(["stop","--time=10",GATEWAY],timeout=60)
            if runtime_changed:
                if runtime.read_bytes()!=new_runtime:raise RuntimeError("Unexpected gateway config drift during rollback")
                inplace(runtime,old_runtime)
            if gateway_changed:
                if ns["digest"](CODE/"stage_gateway.cjs")!=digest_bytes(new_gateway):raise RuntimeError("Unexpected entrypoint drift during rollback")
                replace_code(CODE/"stage_gateway.cjs",old_gateway)
            if site_attempted:
                operator("EXPECTED_BEFORE = "+repr(before_site)+"\n"+rollback_site)
                r.docker(["restart","--time=15",r.state["role_containers"]["web"]],timeout=120);r.refresh_private_frontend()
            if gateway_changed or runtime_changed:r.docker(["start",GATEWAY],timeout=60);mapping()
            state.update(guest_review_enabled=False,origin_enabled=False,guest_rollback_verified=True);r.save()
            r.health();r.runtime_egress()
        except Exception:
            r.run(["systemctl","stop","ndf-dalali-stage-tunnel.service"],timeout=30)
            state.update(guest_review_enabled=False,guest_rollback_verified=False,connector_started=False);r.save()
            raise RuntimeError("Guest activation failed; only staging edge stopped, private rollback inspection required") from None
        raise


if __name__=="__main__":
    if not all(name in globals() for name in ("REQUEST","HELPER")):raise SystemExit("Use stage_publish.py after protected readiness confirmation")
    publish(REQUEST,HELPER)
