#!/usr/bin/env python3
"""Pull/inspect only the pinned candidate on the VM; never load site data."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
REMOTE = r'''
import hashlib,json,os,pathlib,pwd,shutil,subprocess
if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='mail.ndftechlabs.com':
    raise RuntimeError('Unexpected host/account')
state=pathlib.Path('/var/lib/ndf-rootless-migration')
foundation=json.loads((state/'state.json').read_text())
if not foundation.get('complete'):
    raise RuntimeError('Verified rootless foundation required')
user=pwd.getpwnam('dt-containers')
if user.pw_uid!=foundation['account']['uid']:
    raise RuntimeError('Deployment account identity changed')
if shutil.disk_usage('/').free<60*1024**3:
    raise RuntimeError('Insufficient host disk margin')
env=['sudo','-n','-H','-u','dt-containers','env',f'HOME={user.pw_dir}',
     f'XDG_RUNTIME_DIR=/run/user/{user.pw_uid}',f'DOCKER_HOST=unix:///run/user/{user.pw_uid}/docker.sock',
     'PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin']
docker=env+['/opt/ndf-rootless/29.6.1/bin/docker']
def output(args,timeout=30):
    return subprocess.check_output(docker+args,text=True,timeout=timeout)
info=json.loads(output(['info','--format','{{json .}}']))
if 'name=rootless' not in info.get('SecurityOptions',[]) or info.get('DockerRootDir')!='/home/dt-containers/.local/share/docker':
    raise RuntimeError('Wrong Docker daemon')
paths=[pathlib.Path('/home/frappe/bench/config/supervisor.conf'),pathlib.Path('/home/frappe/bench/sites/common_site_config.json'),
       *pathlib.Path('/home/frappe/bench/sites').glob('*/site_config.json'),*pathlib.Path('/etc/nginx/conf.d').glob('*.conf')]
def fingerprints():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
before=fingerprints()
reference='frappe/erpnext@'+REQUEST['digest']
log=state/'base-image-pull.log'
inspect=subprocess.run(docker+['image','inspect',reference],capture_output=True,text=True)
if inspect.returncode:
    with open(log,'a') as stream:
        subprocess.run(docker+['pull','--platform','linux/amd64',reference],stdout=stream,stderr=stream,check=True,timeout=2400)
image=json.loads(output(['image','inspect',reference]))[0]
if reference not in image.get('RepoDigests',[]):
    raise RuntimeError('Pulled image digest mismatch')
INSPECT=r"""
import ast,json,pathlib,shutil,subprocess,sys
root=pathlib.Path('/home/frappe/frappe-bench')
versions={}
for app in ('frappe','erpnext'):
    text=(root/'apps'/app/app/'__init__.py').read_text()
    tree=ast.parse(text)
    versions[app]=next((node.value.value for node in tree.body if isinstance(node,ast.Assign)
        and isinstance(node.value,ast.Constant) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets)),None)
commits={}
for app in ('frappe','erpnext'):
    if shutil.which('git'):
        p=subprocess.run(['git','-C',str(root/'apps'/app),'rev-parse','HEAD'],capture_output=True,text=True)
        commits[app]=p.stdout.strip() if p.returncode==0 else None
    else:
        commits[app]=None
packages=json.loads(subprocess.check_output([sys.executable,'-m','pip','--disable-pip-version-check','list','--format=json'],text=True))
print(json.dumps({'source_versions':versions,'git_commits':commits,'python':sys.version.split()[0],
                  'node':subprocess.check_output(['node','--version'],text=True).strip().lstrip('v'),
                  'os':next(line.split('=',1)[1].strip(chr(34)) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if line.startswith('PRETTY_NAME=')),
                  'python_packages':packages}))
"""
candidate=json.loads(output(['run','--rm','--pull','never','--network','none','--read-only','--cap-drop','ALL',
    '--security-opt','no-new-privileges','--memory','256m','--cpus','0.25','--pids-limit','64',
    '--entrypoint','/home/frappe/frappe-bench/env/bin/python',reference,'-c',INSPECT],timeout=180))
if fingerprints()!=before:
    raise RuntimeError('Native configuration changed during image preparation; review required')
expected=REQUEST['runtime']
matches=(candidate['source_versions'].get('frappe')==expected['frappe'] and candidate['source_versions'].get('erpnext')==expected['erpnext']
         and candidate['python']==expected['python'] and candidate['node']==expected['node'])
receipt={'reference':reference,'image_id':image['Id'],'compressed_bytes_planned':REQUEST['compressed_bytes'],
         'docker_reported_image_size_bytes':image.get('Size'),'candidate':candidate,'runtime_versions_match':matches,
         'exact_source_revision_parity_proven':all(candidate['git_commits'].get(app)==REQUEST['commits'][app] for app in ('frappe','erpnext')),
         'customer_data_loaded':False,'native_configuration_unchanged':True,'application_network_enabled':False}
(state/'base-image.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps({key:value for key,value in receipt.items() if key!='candidate'}|{'source_versions':candidate['source_versions'],
     'python':candidate['python'],'node':candidate['node'],'image_os':candidate['os']}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    package_plan = json.loads((HERE / "rootless-packages.json").read_text())
    source = json.loads((HERE / "source-manifest.json").read_text())
    candidate = package_plan["later_application_image_candidate_not_authorized"]
    request = {"digest": candidate["amd64_digest"], "compressed_bytes": candidate["compressed_bytes"],
               "runtime": source["runtime"], "commits": {app["app"]:app["commit"] for app in source["apps"] if app["app"] in {"frappe", "erpnext"}}}
    if not args.execute:
        print(json.dumps({"dry_run": True, "vm_only": True, "image": candidate["image"], **request,
                          "customer_data_loaded": False}, indent=2))
        return
    code = "REQUEST = " + repr(request) + "\n" + REMOTE
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=60", "ssh.ndftechlabs.com", "sudo -n nice -n 10 python3 -"],
                            input=code, text=True, timeout=2800)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
