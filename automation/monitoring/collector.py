#!/usr/bin/env python3
"""Deterministic, read-only Nix OS/Hermes metadata inventory collector."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
VALID={'open','todo','in_progress','running','blocked','done','completed','cancelled','failed','paused'}
def safe_id(x): return x if isinstance(x,str) and ID.fullmatch(x) else None
def safe_time(x):
    if not isinstance(x,str) or len(x)>40:return None
    try: datetime.fromisoformat(x.replace('Z','+00:00')); return x
    except ValueError:return None
def safe_text(x, fallback): return x if isinstance(x,str) and len(x)<=120 and all(ord(c)>=32 for c in x) else fallback
def valid_repo(x): return x if isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}',x) else None
def load_json(p): return json.loads(Path(p).read_text())
def _rows(text):
    try:x=json.loads(text)
    except (ValueError,TypeError):return []
    if isinstance(x,list):return x
    if isinstance(x,dict):
        for k in ('items','boards','tasks','profiles','jobs'):
            if isinstance(x.get(k),list):return x[k]
    return []
def sanitize_task(t,board=None):
    tid=safe_id(t.get('id') or t.get('task_id')); s=t.get('status')
    if not tid or not isinstance(s,str) or s.lower() not in VALID:return None
    out={'id':tid,'status':s.lower(),'board_id':safe_id(t.get('board_id')) or board}
    for k in ('project_id','assignee'):
        v=safe_id(t.get(k))
        if v:out[k]=v
    for k in ('created_at','updated_at','completed_at'):
        v=safe_time(t.get(k))
        if v:out[k]=v
    return out
def run_readonly_live():
    """Only invokes Hermes list/status commands; failures yield unknown/empty evidence."""
    def run(args):
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=20,check=False)
            return p.stdout if p.returncode==0 else ''
        except (OSError,subprocess.TimeoutExpired):return ''
    profiles=[]
    for line in run(['hermes','profile','list']).splitlines():
        token=line.strip().split()[0] if line.strip() else ''
        token=re.sub(r'^[^A-Za-z0-9]+','',token)
        if safe_id(token) and token.lower() not in {'profile','name','id'}:profiles.append({'name':token,'kind':'unknown'})
    rawboards=[b for b in _rows(run(['hermes','kanban','boards','list','--json'])) if isinstance(b,dict)]
    boards=[]; tasks=[]
    for b in rawboards:
        bid=safe_id(b.get('slug') or b.get('id'))
        if not bid:continue
        boards.append({'id':bid,'name':safe_text(b.get('name'),bid)})
        for t in _rows(run(['hermes','kanban','--board',bid,'list','--json'])):
            if isinstance(t,dict):
                v=sanitize_task(t,bid)
                if v:tasks.append(v)
    cron=run(['hermes','cron','list']); gateway=run(['hermes','gateway','status'])
    return {'profiles':profiles,'boards':boards,'tasks':tasks,'status':{'scheduler':'available' if cron.strip() else 'unknown','gateway':'available' if gateway.strip() else 'unknown'}}
def discover(manifest,runtime,now=None):
    now=now or datetime.now(timezone.utc); now=now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now; projects={}; roles={}
    for p in manifest.get('projects',[]):
        if not isinstance(p,dict):continue
        pid=safe_id(p.get('id'))
        if pid:projects[pid]={'id':pid,'name':safe_text(p.get('name'),pid),'board':safe_id(p.get('board')),'paused':p.get('paused') is True,'scope':'governed'}
    role_aliases=set()
    for r in manifest.get('profiles',[]):
        if isinstance(r,dict) and safe_id(r.get('id')):
            pid=r['id'];roles[pid]={'kind':safe_text(r.get('kind'),'unknown'),'scope':'governed'}
            alias=safe_id(r.get('hermes_profile'))
            if alias:role_aliases.add(alias)
    boards=set(p['board'] for p in projects.values() if p['board'])
    for b in runtime.get('boards',[]):
        if not isinstance(b,dict):continue
        bid=safe_id(b.get('id'))
        if not bid:continue
        boards.add(bid)
        if bid not in projects:projects[bid]={'id':bid,'name':safe_text(b.get('name'),bid),'board':bid,'paused':False,'scope':'unclassified'}
    for p in runtime.get('profiles',[]):
        name=safe_id(p.get('name')) if isinstance(p,dict) else None
        if name and name not in roles and name not in role_aliases:roles[name]={'kind':safe_text(p.get('kind'),'unknown'),'scope':'unclassified'}
    tasks=[]
    for t in runtime.get('tasks',[]):
        if isinstance(t,dict):
            clean=sanitize_task(t)
            if clean:tasks.append(clean)
    result=[]
    for pid,p in sorted(projects.items()):
        if p['paused']:continue
        rows=[t for t in tasks if t.get('project_id')==pid or p['board'] and t.get('board_id')==p['board']]; enriched=[]
        for t in sorted(rows,key=lambda x:x['id']):
            age=None; stamp=t.get('created_at') or t.get('updated_at')
            if stamp:
                try:
                    created=datetime.fromisoformat(stamp.replace('Z','+00:00')); created=created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
                    age=max(0,int((now-created).total_seconds()//3600))
                except ValueError:pass
            enriched.append({**t,'age_hours':age,'stale':bool(age is not None and age>=72 and t['status'] not in {'done','completed','cancelled'})})
        kpis={}
        for m in manifest.get('metrics',[]):
            if isinstance(m,dict) and m.get('project')==pid and safe_id(m.get('name')):
                src=safe_id(m.get('source')); val=runtime.get('metrics',{}).get(src) if src else None
                valid=isinstance(val,(str,int,float,bool))
                kpis[m['name']]={'value':val if valid else None,'status':'observed' if valid else 'unknown'}
        result.append({'project':{k:v for k,v in p.items() if k!='paused'},'tasks':enriched,'task_count':len(enriched),'throughput':sum(t['status'] in {'done','completed'} for t in enriched),'blocked_count':sum(t['status']=='blocked' for t in enriched),'running_count':sum(t['status'] in {'running','in_progress'} for t in enriched),'kpis':kpis})
    status=runtime.get('status',{}); cleanstatus={}
    for k in ('gateway','scheduler','heartbeat','workers'):
        v=status.get(k) if isinstance(status,dict) else None
        cleanstatus[k]=v if isinstance(v,(str,int,float,bool)) and (not isinstance(v,str) or v in {'available','unknown','healthy','running','stopped','degraded','ok','error','idle','active','paused'}) else 'unknown'
    governed=sorted(p['id'] for p in manifest.get('projects',[]) if isinstance(p,dict) and safe_id(p.get('id')))
    return {'schema_version':1,'scope':{'governed_projects':governed,'unclassified_projects':sorted(k for k,v in projects.items() if v['scope']=='unclassified'),'governed_roles':sorted(k for k,v in roles.items() if v['scope']=='governed'),'unclassified_roles':sorted(k for k,v in roles.items() if v['scope']=='unclassified'),'boards':sorted(boards)},'projects':result,'profiles':sorted([{'id':k,'kind':v['kind'],'scope':v['scope']} for k,v in roles.items()],key=lambda x:x['id']),'runtime_status':cleanstatus,'collection':'read-only'}
def stable_events(report):
    out=[]
    for p in report['projects']:
        for t in p['tasks']:
            e={'project':p['project']['id'],'task':t}; key=hashlib.sha256(json.dumps(e,sort_keys=True,separators=(',',':')).encode()).hexdigest();out.append({'key':key,**e})
    return out
def collect(manifest_path,runtime_path,output_path):
    r=discover(load_json(manifest_path),load_json(runtime_path));r['events']=stable_events(r);Path(output_path).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');return r
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--runtime');ap.add_argument('--live',action='store_true');ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.live and a.runtime:ap.error('choose --live or --runtime')
    runtime=run_readonly_live() if a.live else load_json(a.runtime) if a.runtime else ap.error('provide --runtime or --live')
    report=discover(load_json(a.manifest),runtime);report['events']=stable_events(report);Path(a.output).write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(f"projects={len(report['projects'])} tasks={sum(p['task_count'] for p in report['projects'])} scope=read-only")
