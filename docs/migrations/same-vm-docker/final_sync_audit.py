#!/usr/bin/env python3
"""Scoped read-only source control-plane/queue audit; no fencing or data sync."""

import argparse
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
REMOTE = r'''
from datetime import datetime,timezone
import hashlib,json,os,pathlib,re,subprocess
if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='mail.ndftechlabs.com':raise RuntimeError('Wrong audit host/account')
bench=pathlib.Path('/home/frappe/bench');common=json.loads((bench/'sites/common_site_config.json').read_text())
def query(site,sql):
 c=json.loads((bench/'sites'/site/'site_config.json').read_text());host=c.get('db_host') or common.get('db_host') or 'localhost'
 if host not in ['localhost','127.0.0.1']:raise RuntimeError('Unexpected native metadata endpoint')
 p=subprocess.run(['mariadb','--connect-timeout=5','--batch','--skip-column-names','--host',host,
  '--port',str(c.get('db_port') or common.get('db_port') or 3306),'--user',c.get('db_user') or c['db_name'],
  '--database',c['db_name'],'--execute','START TRANSACTION READ ONLY; '+sql+'; COMMIT;'],
  env=dict(os.environ,MYSQL_PWD=c['db_password']),capture_output=True,text=True,timeout=20)
 if p.returncode:raise RuntimeError('Read-only metadata query failed')
 return [line.split('\t') for line in p.stdout.splitlines() if line]
def identifier(value):
 if '\0' in value:raise RuntimeError('Unsafe metadata identifier')
 return '`'+value.replace('`','``')+'`'
metadata=query('erp.ndftechlabs.com',"SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND DATA_TYPE IN ('varchar','text','mediumtext','longtext') AND (TABLE_NAME IN ('tabTenant Site','tabTenant','tabInfrastructure Node') OR TABLE_NAME LIKE 'tabTenant%Domain%' OR TABLE_NAME LIKE 'tabTenant%Alias%') ORDER BY TABLE_NAME,ORDINAL_POSITION")
tables={}
for table,column in metadata:tables.setdefault(table,[]).append(column)
details={};summaries={}
allowed={'name','fqdn','site','site_name','domain','hostname','tenant','node','automation_state','state','status','project_base_dir','scripts_base_dir','parent','parenttype'}
searchable={'name','fqdn','site','site_name','domain','hostname'}
for table,columns in tables.items():
 fields=[field for field in columns if field in allowed];search=[field for field in columns if field in searchable]
 if not fields or not search:continue
 where=' OR '.join('LOWER('+identifier(field)+") LIKE '%dalali%'" for field in search)
 rows=query('erp.ndftechlabs.com','SELECT '+','.join(map(identifier,fields))+' FROM '+identifier(table)+' WHERE '+where+' LIMIT 25')
 details[table]={'fields':fields,'rows':rows};summaries[table]={'matching_records':len(rows),'limit_reached':len(rows)==25}
pilot={'scheduler':query('dalali.ndftechlabs.com','SELECT COUNT(*),SUM(stopped=0),MAX(last_execution) FROM `tabScheduled Job Type`'),
 'pending_email':query('dalali.ndftechlabs.com',"SELECT status,COUNT(*) FROM `tabEmail Queue` WHERE status IN ('Not Sent','Sending') GROUP BY status")}
queue_counts={}
for q in ['short','default','long']:
 key='rq:queue:bench:'+q;p=subprocess.run(['redis-cli','-h','127.0.0.1','-p','11000','--raw','LLEN',key],capture_output=True,text=True,timeout=5)
 queue_counts[q]=int(p.stdout.strip()) if p.returncode==0 and p.stdout.strip().isdigit() else None
crons={}
for user in ['frappe','root','frank']:
 p=subprocess.run(['crontab','-u',user,'-l'],capture_output=True,text=True,timeout=10)
 if p.returncode not in [0,1]:raise RuntimeError('Cron read-only inventory failed')
 lines=[line for line in p.stdout.splitlines() if line.strip() and not line.lstrip().startswith('#')]
 crons[user]={'entries':len(lines),'pilot_mentions':sum('dalali' in line.lower() for line in lines),
  'shared_bench_mentions':sum('/home/frappe/bench' in line or 'bench --site' in line for line in lines),
  'sha256':hashlib.sha256(p.stdout.encode()).hexdigest() if p.returncode==0 else None}
w=pathlib.Path('/var/lib/ndf-rootless-migration/rehearsal-v1');private={'checked_utc':datetime.now(timezone.utc).isoformat(),'read_only':True,'control_plane_details':details,'pilot':pilot,'queue_lengths_shared_not_per_site':queue_counts,'cron_summary':crons}
(w/'final-sync-control-plane-audit.json').write_text(json.dumps(private,indent=2))
print(json.dumps({'checked_utc':private['checked_utc'],'read_only':True,'control_plane_match_counts':summaries,'pilot_scheduler':pilot['scheduler'],'pending_email_status_counts':pilot['pending_email'],'shared_queue_lengths':queue_counts,'cron_summary':crons,
 'limits':['Shared queue lengths are not proof of no pilot in-flight jobs','No Redis pickle/description/credential data decoded or displayed','Scoped aliases/cron audit cannot prove absence of external writers','No fencing, source pause, copy, schema sync or cutover executed']},indent=2))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Read-only final-sync ownership/queue/cron audit; no source changes, lock/dump, pickle decode or traffic switch.")
        return
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=60",
                             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "ssh.ndftechlabs.com",
                             "sudo -n nice -n 15 python3 -"], input=REMOTE, text=True, timeout=300)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
