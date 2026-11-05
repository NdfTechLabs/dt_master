#!/usr/bin/env python3
"""Read-only VM rehearsal sizing/readiness; never dump/copy data or print secrets."""

import argparse
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
REMOTE = r'''
from datetime import datetime,timezone
import hashlib,json,os,pathlib,shutil,stat,subprocess
if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='mail.ndftechlabs.com':
 raise RuntimeError('Unexpected host/account')
bench=pathlib.Path('/home/frappe/bench');site=bench/'sites/dalali.ndftechlabs.com'
config=json.loads((site/'site_config.json').read_text());common=json.loads((bench/'sites/common_site_config.json').read_text())
host=config.get('db_host') or common.get('db_host') or 'localhost'
if host not in {'localhost','127.0.0.1','::1'}:raise RuntimeError('Unexpected native database destination')
def run(args,timeout=30,env=None):
 p=subprocess.run(args,capture_output=True,text=True,timeout=timeout,env=env)
 return {'exit':p.returncode,'output':p.stdout.strip()}
client=shutil.which('mariadb') or shutil.which('mysql')
def query(sql):
 env=dict(os.environ,MYSQL_PWD=config['db_password'])
 p=run([client,'--connect-timeout=5','--batch','--skip-column-names','--host',host,
        '--port',str(config.get('db_port') or common.get('db_port') or 3306),
        '--user',str(config.get('db_user') or config['db_name']),'--database',config['db_name'],
        '--execute','START TRANSACTION READ ONLY; '+sql+'; COMMIT;'],env=env)
 return {'exit':p['exit'],'rows':[line.split('\t') for line in p['output'].splitlines()] if p['exit']==0 else []}
def size(path):
 p=run(['du','-x','-s','-B1',str(path)],timeout=120)
 return int(p['output'].split()[0]) if p['exit']==0 else None
def tree_stats(root,allowed):
 files=links=outside=special=0;apparent=0
 for parent,dirs,names in os.walk(root,followlinks=False):
  for name in dirs+names:
   p=pathlib.Path(parent)/name;info=p.lstat()
   if stat.S_ISLNK(info.st_mode):
    links+=1
    if not any(p.resolve().is_relative_to(a) for a in allowed):outside+=1
   elif stat.S_ISREG(info.st_mode):files+=1;apparent+=info.st_size
   elif not stat.S_ISDIR(info.st_mode):special+=1
 return {'regular_files':files,'symlinks':links,'outside_allowed_roots':outside,'special_files':special,
         'apparent_file_bytes':apparent,'allocated_bytes':size(root)}
state_root=pathlib.Path('/var/lib/ndf-rootless-migration');foundation=json.loads((state_root/'state.json').read_text())
build=json.loads((state_root/'exact-runtime-v1/state.json').read_text())
docker=['sudo','-n','-H','-u','dt-containers','env','XDG_RUNTIME_DIR=/run/user/5001',
        'DOCKER_HOST=unix:///run/user/5001/docker.sock','PATH=/opt/ndf-rootless/29.6.1/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        '/opt/ndf-rootless/29.6.1/bin/docker']
changed=[p for p,h in foundation['before']['hashes'].items() if not pathlib.Path(p).is_file() or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]
report={'checked_utc':datetime.now(timezone.utc).isoformat(),'read_only':True,'pilot':'dalali.ndftechlabs.com',
 'free_bytes':shutil.disk_usage('/').free,'validated_image_id':build.get('validated_image_id'),
 'image_validated':bool(build.get('yarn_fix',{}).get('validation_passed')),'changed_protected_fingerprints':changed,
 'maintenance':config.get('maintenance_mode',0),'encryption_key_present':bool(config.get('encryption_key')),
 'source_files':{},'database':{},'service_states':{},'redis_version':run(['redis-server','--version']),
 'native_timezone':pathlib.Path('/etc/timezone').read_text().strip() if pathlib.Path('/etc/timezone').is_file() else None,
 'loopback_port_18088':run(['ss','-H','-lnt','sport = :18088']),
 'docker_images':run(docker+['image','ls','--format','{{json .}}']),
 'docker_containers':run(docker+['ps','-a','--format','{{json .}}']),
 'supervisor':run(['supervisorctl','status'])}
for name in ['public/files','private/files']:
 report['source_files'][name]=tree_stats(site/name,[site/'public/files',site/'private/files'])
report['assets']=tree_stats(bench/'sites/assets',[bench/'sites/assets',bench/'apps'])
sqls={
 'version_settings':'SELECT VERSION(),@@sql_mode,@@character_set_server,@@collation_server,@@time_zone,@@lower_case_table_names,@@max_allowed_packet,TIMESTAMPDIFF(SECOND,UTC_TIMESTAMP(),NOW())',
 'table_types':"SELECT TABLE_TYPE,COALESCE(ENGINE,'NONE'),COUNT(*),COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() GROUP BY TABLE_TYPE,ENGINE",
 'triggers':"SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()",
 'routines':"SELECT ROUTINE_TYPE,COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() GROUP BY ROUTINE_TYPE",
 'events':"SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()",
 'scheduler':'SELECT COUNT(*),SUM(stopped=0),MAX(last_execution) FROM `tabScheduled Job Type`',
 'outgoing_email':"SELECT COUNT(*) FROM `tabEmail Account` WHERE enable_outgoing=1",
 'enabled_webhooks':'SELECT COUNT(*) FROM tabWebhook WHERE enabled=1',
 'pending_email':"SELECT status,COUNT(*) FROM `tabEmail Queue` WHERE status IN ('Not Sent','Sending') GROUP BY status",
}
for name,sql in sqls.items():report['database'][name]=query(sql)
for unit in foundation['before']['services']:
 report['service_states'][unit]=run(['systemctl','is-active',unit])['output']
report['services_match_foundation']=report['service_states']==foundation['before']['services']
report['http_baseline']={}
for name in ['dalali.ndftechlabs.com','demo.ndftechlabs.com','dulamanenterprise.ndftechlabs.com','erp.ndftechlabs.com']:
 report['http_baseline'][name]=run(['curl','--silent','--show-error','--max-time','10','--resolve',name+':443:127.0.0.1','--output','/dev/null','--write-out','%{http_code}','https://'+name+'/api/method/ping'])
print(json.dumps(report,indent=2))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Read-only preflight; --execute runs size/SELECT/service checks. No data copies, dump, lock or download.")
        return
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
                             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com",
                             "sudo -n nice -n 15 ionice -c 3 python3 -"], input=REMOTE, text=True, timeout=900)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
