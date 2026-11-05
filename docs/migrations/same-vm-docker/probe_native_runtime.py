#!/usr/bin/env python3
"""Feasibility probe using private COPIES, never live production mounts/downloads."""

import argparse
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,os,pathlib,pwd,shutil,stat,subprocess
if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='mail.ndftechlabs.com':
    raise RuntimeError('Unexpected host/account')
if shutil.disk_usage('/').free<60*1024**3:raise RuntimeError('Insufficient disk safety margin')
user=pwd.getpwnam('dt-containers')
state=pathlib.Path('/var/lib/ndf-rootless-migration')
if not json.loads((state/'state.json').read_text()).get('complete'):raise RuntimeError('Verified foundation required')
base=json.loads((state/'base-image.json').read_text())
reference=base['reference']
target=pathlib.Path('/opt/ndf-rootless/native-runtime-probe-v1')
marker=target/'ndf-probe-marker.json'
if target.is_symlink():raise RuntimeError('Unsafe probe symlink')
if target.exists():
    if target.stat().st_uid!=0 or not marker.is_file() or json.loads(marker.read_text()).get('purpose')!='native-runtime-probe-v1':
        raise RuntimeError('Unrecognized existing probe path')
else:
    target.mkdir(mode=0o750)
    marker.write_text(json.dumps({'purpose':'native-runtime-probe-v1','production_mounts':False}))
def ignore(path,names):
    excluded={'__pycache__','.env','.npmrc','npmrc','pip.conf','.pypirc','direct_url.json','id_rsa','id_ed25519'}
    return [name for name in names if name in excluded or name.endswith('.pyc')]
copies=[('/usr/bin/python3.14','python3.14'),('/usr/lib/python3.14','stdlib'),
        ('/home/frappe/bench/env','application-env'),('/home/frappe/.venv','bench-env'),
        ('/home/frappe/.nvm/versions/node/v24.14.0','node')]
for source,name in copies:
    destination=target/name
    done=target/(name+'.copied')
    if destination.is_symlink():raise RuntimeError('Unexpected copy symlink')
    if done.exists():continue
    if destination.exists():raise RuntimeError('Interrupted copy requires review; not overwriting')
    path=pathlib.Path(source)
    if path.is_dir():shutil.copytree(path,destination,symlinks=True,ignore=ignore)
    else:shutil.copy2(path,destination)
    done.write_text('copied\n')
compat=target/'compatibility'
compat.mkdir(exist_ok=True)
for name in ['libmysqlclient.so.21','libssl.so.3','libcrypto.so.3','libmariadb.so.3']:
    source=pathlib.Path('/usr/lib/x86_64-linux-gnu')/name
    destination=compat/name
    if not destination.exists():shutil.copyfile(source.resolve(),destination)
clients=target/'native-clients'
clients.mkdir(exist_ok=True)
for name in ['mariadb','mariadb-dump']:
    source=pathlib.Path('/usr/bin')/name
    destination=clients/name
    if not destination.exists():
        shutil.copyfile(source.resolve(),destination)
        destination.chmod(0o750)
# Only this newly marked private copy, never the original runtime paths.
for parent,dirs,files in os.walk(target,followlinks=False):
    os.chown(parent,0,user.pw_gid)
    os.chmod(parent,0o750)
    for name in files:
        path=pathlib.Path(parent)/name
        if not path.is_symlink():
            executable=bool(path.stat().st_mode&0o111)
            os.chown(path,0,user.pw_gid)
            path.chmod(0o750 if executable else 0o640)
env=['sudo','-n','-H','-u','dt-containers','env',f'HOME={user.pw_dir}',
     f'XDG_RUNTIME_DIR=/run/user/{user.pw_uid}',f'DOCKER_HOST=unix:///run/user/{user.pw_uid}/docker.sock',
     'PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin']
docker=env+['/opt/ndf-rootless/29.6.1/bin/docker']
PROBE=r"""
import importlib,json,subprocess,sys,ssl
result={'python':sys.version.split()[0],'openssl':ssl.OPENSSL_VERSION,
        'node':subprocess.check_output(['/home/frappe/.nvm/versions/node/v24.14.0/bin/node','--version'],text=True).strip(),
        'imports':{},'native_clients':{}}
for name in ['pymysql','MySQLdb','PIL.Image','cryptography','redis','rq','gunicorn','numpy','pandas','pyarrow']:
    try:importlib.import_module(name)
    except Exception as error:result['imports'][name]={'ok':False,'error':type(error).__name__,'message':str(error)[:300]}
    else:result['imports'][name]={'ok':True}
for name in ['mariadb','mariadb-dump']:
    p=subprocess.run(['/opt/ndf-native/bin/'+name,'--version'],capture_output=True,text=True)
    result['native_clients'][name]={'ok':p.returncode==0,'version':p.stdout.strip(),'error':p.stderr.strip()[:300] if p.returncode else None}
print(json.dumps(result))
"""
mounts=[(target/'python3.14','/usr/bin/python3.14'),(target/'stdlib','/usr/lib/python3.14'),
        (target/'application-env','/home/frappe/bench/env'),(target/'bench-env','/home/frappe/.venv'),
        (target/'node','/home/frappe/.nvm/versions/node/v24.14.0')]
mounts += [(compat,'/opt/ndf-native/lib'),(clients,'/opt/ndf-native/bin')]
args=['run','--rm','--pull','never','--network','none','--read-only','--user','0:0','--cap-drop','ALL',
      '--security-opt','no-new-privileges','--memory','768m','--cpus','0.5','--pids-limit','96',
      '--env','PYTHONDONTWRITEBYTECODE=1','--env','OPENBLAS_NUM_THREADS=1','--env','OMP_NUM_THREADS=1',
      '--env','NUMEXPR_NUM_THREADS=1','--env','LD_LIBRARY_PATH=/opt/ndf-native/lib']
for source,destination in mounts:
    args+=['--mount',f'type=bind,src={source},dst={destination},readonly']
args+=['--entrypoint','/home/frappe/bench/env/bin/python',reference,'-c',PROBE]
p=subprocess.run(docker+args,capture_output=True,text=True,timeout=180)
with open(state/'native-runtime-probe-v2.log','w') as log:log.write(p.stdout+p.stderr)
if p.returncode:raise RuntimeError('Isolated runtime probe failed; inspect private native-runtime-probe.log')
result=json.loads(p.stdout)
result.update({'purpose':'feasibility_only','production_mounts':False,'new_downloads':False,'layer_built':False,'site_data_loaded':False,
               'copy_directory':str(target)})
(state/'native-runtime-probe-v2.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"dry_run": True, "purpose": "Native runtime/library feasibility using private copies",
                          "downloads": False, "build": False, "production_mounts": False, "site_data": False}))
        return
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
                             "ssh.ndftechlabs.com", "sudo -n nice -n 15 ionice -c 3 python3 -"], input=REMOTE, text=True, timeout=900)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
