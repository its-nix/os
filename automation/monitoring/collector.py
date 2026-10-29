#!/usr/bin/env python3
"""Deterministic, read-only Nix OS/Hermes metadata inventory collector."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
VALID={'open','todo','triage','scheduled','in_progress','running','blocked','review','done','completed','cancelled','failed','paused','archived'}
def safe_id(x): return x if isinstance(x,str) and ID.fullmatch(x) else None
def safe_time(x):
    if isinstance(x,(int,float)) and not isinstance(x,bool):
        try:return datetime.fromtimestamp(x,timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError):return None
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
    rawboards=[b for b in _rows(run(['hermes','kanban','boards','list','--json','--include-archived'])) if isinstance(b,dict)]
    boards=[]; tasks=[]
    for b in rawboards:
        bid=safe_id(b.get('slug') or b.get('id'))
        if not bid:continue
        boards.append({'id':bid,'name':safe_text(b.get('name'),bid),'archived':b.get('archived') is True,'paused':b.get('paused') is True})
        for t in _rows(run(['hermes','kanban','--board',bid,'list','--json','--include-archived'])):
            if isinstance(t,dict):
                v=sanitize_task(t,bid)
                if v:tasks.append(v)
    def health(args,positive):
        text=run(args).strip().lower()
        if not text:return 'unknown'
        if any(x in text for x in ('error','failed','unavailable','stopped','inactive','disabled')):return 'degraded'
        return 'available' if any(x in text for x in positive) else 'unknown'
    cron=health(['hermes','cron','list'],('scheduled','enabled','running','active','jobs'))
    gateway=health(['hermes','gateway','status'],('running','healthy','active','ok'))
    status={'scheduler':cron,'gateway':gateway}
    # Only declared repositories are queried; retain only aggregate counts/status.
    policy=load_json(Path(__file__).with_name('policy.json'))
    repo_map=policy.get('repos',{}) if isinstance(policy,dict) else {}
    prs={}
    if isinstance(repo_map,dict):
        for project,repo in repo_map.items():
            if not safe_id(project) or not valid_repo(repo):continue
            try:
                p=subprocess.run(['gh','pr','list','--repo',repo,'--state','all','--json','state,statusCheckRollup'],capture_output=True,text=True,timeout=20,check=False)
                if p.returncode!=0:continue
                rows=json.loads(p.stdout)
                if not isinstance(rows,list):continue
                counts={'open':0,'merged':0,'closed':0,'checks_passing':0,'checks_failing':0,'checks_pending':0}
                for pr in rows:
                    if not isinstance(pr,dict):continue
                    state=pr.get('state')
                    if state=='OPEN':counts['open']+=1
                    elif state=='MERGED':counts['merged']+=1
                    elif state=='CLOSED':counts['closed']+=1
                    checks=pr.get('statusCheckRollup')
                    if isinstance(checks,list):
                        for c in checks:
                            if isinstance(c,dict):
                                conclusion=c.get('conclusion') or c.get('state')
                                if conclusion in ('SUCCESS','NEUTRAL','SKIPPED'):counts['checks_passing']+=1
                                elif conclusion in ('FAILURE','ERROR','TIMED_OUT','ACTION_REQUIRED'):counts['checks_failing']+=1
                                elif conclusion in ('PENDING','QUEUED','IN_PROGRESS','EXPECTED'):counts['checks_pending']+=1
                prs[project]=counts
            except (OSError,subprocess.TimeoutExpired,ValueError):continue
    return {'profiles':profiles,'boards':boards,'tasks':tasks,'status':status,'repos':prs}
def discover(manifest,runtime,now=None):
    now=now or datetime.now(timezone.utc); now=now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now; projects={}; roles={}
    for p in manifest.get('projects',[]):
        if not isinstance(p,dict):continue
        pid=safe_id(p.get('id'))
        if pid:projects[pid]={'id':pid,'name':safe_text(p.get('name'),pid),'board':safe_id(p.get('board')),'paused':p.get('paused') is True or p.get('archived') is True,'scope':'governed'}
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
        if bid not in projects:projects[bid]={'id':bid,'name':safe_text(b.get('name'),bid),'board':bid,'paused':b.get('archived') is True or b.get('paused') is True,'scope':'unclassified'}
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
        if p['paused'] or any(b.get('id')==p['board'] and (b.get('archived') or b.get('paused')) for b in runtime.get('boards',[]) if isinstance(b,dict)):continue
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
        completed=[]
        for t in enriched:
            stamp=t.get('completed_at')
            if stamp:
                try:
                    dt=datetime.fromisoformat(stamp.replace('Z','+00:00'));dt=dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                    if (now-dt).total_seconds()<=7*86400 and t['status'] in {'done','completed'}:completed.append(t)
                except ValueError:pass
        result.append({'project':{k:v for k,v in p.items() if k!='paused'},'tasks':enriched,'task_count':len(enriched),'throughput':sum(t['status'] in {'done','completed'} for t in enriched),'throughput_7d':len(completed),'blocked_count':sum(t['status']=='blocked' for t in enriched),'running_count':sum(t['status'] in {'running','in_progress'} for t in enriched),'kpis':kpis})
    status=runtime.get('status',{}); cleanstatus={}
    for k in ('gateway','scheduler','heartbeat','workers'):
        v=status.get(k) if isinstance(status,dict) else None
        cleanstatus[k]=v if isinstance(v,(str,int,float,bool)) and (not isinstance(v,str) or v in {'available','unknown','healthy','running','stopped','degraded','ok','error','idle','active','paused'}) else 'unknown'
    governed=sorted(p['id'] for p in manifest.get('projects',[]) if isinstance(p,dict) and safe_id(p.get('id')))
    policy=load_json(Path(__file__).with_name('policy.json')); mapping=policy.get('repos',{})
    raw_repos=runtime.get('repos',{}) if isinstance(runtime.get('repos',{}),dict) else {}; repos={}
    allowed=set(mapping) if isinstance(mapping,dict) else set()
    for project in allowed:
        counts=raw_repos.get(project)
        if safe_id(project) and isinstance(counts,dict):
            clean={k:counts[k] for k in ('open','merged','closed','checks_passing','checks_failing','checks_pending') if isinstance(counts.get(k),int) and not isinstance(counts.get(k),bool) and counts[k]>=0}
            if clean:repos[project]=clean
    return {'schema_version':1,'scope':{'governed_projects':governed,'unclassified_projects':sorted(k for k,v in projects.items() if v['scope']=='unclassified'),'governed_roles':sorted(k for k,v in roles.items() if v['scope']=='governed'),'unclassified_roles':sorted(k for k,v in roles.items() if v['scope']=='unclassified'),'boards':sorted(boards)},'projects':result,'profiles':sorted([{'id':k,'kind':v['kind'],'scope':v['scope']} for k,v in roles.items()],key=lambda x:x['id']),'runtime_status':cleanstatus,'repos':repos,'collection':'read-only'}
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
