#!/usr/bin/env python3
"""Deterministic, read-only Nix OS/Hermes metadata inventory collector."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sqlite3, stat, subprocess, time
from pathlib import Path
from datetime import datetime, timezone

ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
VALID={'open','todo','triage','scheduled','ready','in_progress','running','blocked','review','done','completed','cancelled','failed','paused','archived'}
MAX_TASK_ROWS=20_000
MAX_RECOVERY_HISTORY_ROWS=50
MAX_BOARD_SOURCE_BYTES=64*1024*1024
BOARD_READ_SECONDS=10
HEX64=re.compile(r'^[0-9a-f]{64}$')
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


def _identity(path):
    """Identity/size metadata for one non-symlink regular SQLite file."""
    try:
        st=os.lstat(path)
    except FileNotFoundError:
        return None
    uid=getattr(os,'getuid',None)
    if (not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_nlink!=1
            or (uid is not None and st.st_uid!=uid())):
        raise ValueError('unsafe SQLite source')
    return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns,stat.S_IMODE(st.st_mode))


def _check_no_symlink_ancestry(path):
    absolute=Path(os.path.abspath(path))
    current=Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current=current/part
        st=os.lstat(current)
        if stat.S_ISLNK(st.st_mode): raise ValueError('symlinked SQLite source path')
        if current!=absolute and not stat.S_ISDIR(st.st_mode): raise ValueError('invalid SQLite source ancestry')


def read_board_tasks(board_id, db_path):
    """Read checkpointed board rows without SQLite WAL/SHM coordination writes.

    Immutable reads are accepted only while the WAL is absent or empty and all
    DB/sidecar identities stay stable. Otherwise the board is explicitly
    unavailable rather than returning a stale or inconsistent snapshot.
    """
    unavailable={'status':'unavailable','row_count':0,'included_count':0,'invalid_row_count':0,
                 'limit':MAX_TASK_ROWS,'cap_reached':False,'read_mode':'immutable_checkpoint_only',
                 'recovery_history':{'status':'unavailable','source':'immutable_checkpoint_sqlite',
                                     'observed_at':None,'complete':False,'records':[],
                                     'limit':MAX_RECOVERY_HISTORY_ROWS,'cap_reached':False}}
    if not safe_id(board_id) or os.environ.get('HERMES_KANBAN_DB'):
        unavailable['unavailable_reason']='invalid_board_or_database_pin'
        return [],unavailable,'unavailable'
    try:
        raw=os.fspath(db_path)
    except TypeError:
        unavailable['unavailable_reason']='invalid_path'
        return [],unavailable,'unavailable'
    if (not isinstance(raw,str) or not raw.startswith('/') or chr(0) in raw or raw.endswith('/')
            or '//' in raw or any(part in {'.','..'} for part in raw.split('/')[1:])):
        unavailable['unavailable_reason']='invalid_path'
        return [],unavailable,'unavailable'
    path=Path(raw)
    wal=Path(str(path)+'-wal'); shm=Path(str(path)+'-shm')
    db_fd=None; conn=None
    try:
        _check_no_symlink_ancestry(path)
        before=(_identity(path),_identity(wal),_identity(shm))
        if before[0] is None or sum(item[2] for item in before if item) > MAX_BOARD_SOURCE_BYTES:
            unavailable['unavailable_reason']='wal_active_or_db_missing'
            return [],unavailable,'unavailable'
        if before[1] is not None and before[1][2]!=0:
            unavailable['unavailable_reason']='wal_active_or_db_missing'
            return [],unavailable,'unavailable'
        if not hasattr(os,'O_NOFOLLOW') or not Path('/proc/self/fd').is_dir():
            unavailable['unavailable_reason']='safe_descriptor_sqlite_open_unavailable'
            return [],unavailable,'unavailable'
        flags=os.O_RDONLY|os.O_NOFOLLOW|getattr(os,'O_CLOEXEC',0)
        db_fd=os.open(path,flags)
        opened=os.fstat(db_fd)
        opened_identity=(opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,
                         opened.st_ctime_ns,stat.S_IMODE(opened.st_mode))
        if opened_identity!=before[0]:
            unavailable['unavailable_reason']='source_changed_before_open'
            return [],unavailable,'unavailable'
        deadline=time.monotonic()+BOARD_READ_SECONDS
        # Bind SQLite to the validated open descriptor, not a pathname that can be
        # swapped between lstat and sqlite3.connect. Immutable mode avoids sidecar writes.
        uri=Path(f'/proc/self/fd/{db_fd}').as_uri()+'?mode=ro&immutable=1'
        conn=sqlite3.connect(uri,uri=True,timeout=0.2)
        try:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.set_progress_handler(lambda: 1 if time.monotonic()>deadline else 0,10000)
            cols={row['name'] for row in conn.execute('PRAGMA table_info(tasks)')}
            required={'id','status','project_id','assignee','created_at','completed_at','started_at','last_heartbeat_at'}
            if not required.issubset(cols): raise sqlite3.DatabaseError('task schema incomplete')
            sql="""WITH activity AS (
                SELECT task_id, MAX(ts) AS last_activity_at FROM (
                    SELECT task_id, created_at AS ts FROM task_events
                    UNION ALL SELECT task_id, created_at AS ts FROM task_comments
                    UNION ALL SELECT task_id, COALESCE(last_heartbeat_at,ended_at,started_at) AS ts FROM task_runs
                ) GROUP BY task_id
            )
            SELECT t.id,t.status,t.project_id,t.assignee,t.created_at,t.completed_at,
                   MAX(COALESCE(t.started_at,0),COALESCE(t.last_heartbeat_at,0),
                       COALESCE(a.last_activity_at,0),COALESCE(t.created_at,0)) AS updated_at
            FROM tasks AS t LEFT JOIN activity AS a ON a.task_id=t.id
            WHERE LOWER(t.status)!='archived' ORDER BY t.created_at DESC,t.id LIMIT ?"""
            rows=[dict(row) for row in conn.execute(sql,(MAX_TASK_ROWS+1,)).fetchall()]
            history_columns={'body','idempotency_key','max_retries','created_at','completed_at'}
            history_status='available';history_records=[];history_invalid=0
            history_capped=False
            if not history_columns.issubset(cols):
                history_status='unavailable'
            else:
                # Treat bare legacy digest keys as possible recovery attempts even without a marker;
                # an unrecognized candidate taints the history rather than being silently omitted.
                history_sql="""SELECT id,status,idempotency_key,max_retries,created_at,completed_at,
                    substr(body,instr(body,'monitor_recovery event='),256) AS marker
                    FROM tasks
                    WHERE (body IS NOT NULL AND instr(body,'monitor_recovery event=')>0)
                       OR (length(idempotency_key)=64 AND lower(idempotency_key) NOT GLOB '*[^0-9a-f]*')
                       OR (idempotency_key LIKE 'monitor-recovery-v1:%')
                    ORDER BY created_at DESC,id LIMIT ?"""
                try:
                    raw_history=conn.execute(history_sql,(MAX_RECOVERY_HISTORY_ROWS+1,)).fetchall()
                except sqlite3.Error:
                    raw_history=[];history_status='unavailable'
                if history_status=='available':
                    history_capped=len(raw_history)>MAX_RECOVERY_HISTORY_ROWS
                    seen_history=set()
                    for row in raw_history[:MAX_RECOVERY_HISTORY_ROWS]:
                        marker=row['marker'] if isinstance(row['marker'],str) else ''
                        marker_line=marker.splitlines()[0] if marker else ''
                        match=re.match(r'monitor_recovery event=([0-9a-f]{64}) action=([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?) source=.{1,256}$',marker_line)
                        event_key=match.group(1) if match else None
                        action=match.group(2) if match else None
                        key=row['idempotency_key']
                        expected=hashlib.sha256((event_key+chr(0)+action).encode()).hexdigest() if event_key and action else None
                        status=row['status'].lower() if isinstance(row['status'],str) else None
                        task_id=safe_id(row['id'])
                        max_retries=row['max_retries']
                        identity=(event_key,action)
                        if (not task_id or status not in VALID or not event_key or not HEX64.fullmatch(event_key)
                                or not isinstance(key,str) or key!=expected or type(max_retries) is not int
                                or not 1<=max_retries<=20 or identity in seen_history):
                            history_invalid+=1
                            continue
                        seen_history.add(identity)
                        item={'id':task_id,'status':status,'event_key':event_key,'action':action,
                              'idempotency_key':key,'max_retries':max_retries}
                        created=safe_time(row['created_at']);completed=safe_time(row['completed_at'])
                        if created:item['created_at']=created
                        if completed:item['completed_at']=completed
                        history_records.append(item)
                    if history_invalid or history_capped:history_status='partial'
            recovery_history={'status':history_status,'source':'immutable_checkpoint_sqlite',
                              'observed_at':datetime.now(timezone.utc).isoformat(),
                              'complete':history_status=='available','records':history_records,
                              'limit':MAX_RECOVERY_HISTORY_ROWS,'cap_reached':history_capped}
        finally:
            conn.close()
            conn=None
        opened_after=os.fstat(db_fd)
        opened_after_identity=(opened_after.st_dev,opened_after.st_ino,opened_after.st_size,
                               opened_after.st_mtime_ns,opened_after.st_ctime_ns,
                               stat.S_IMODE(opened_after.st_mode))
        after=(_identity(path),_identity(wal),_identity(shm))
        if (before!=after or opened_after_identity!=before[0]
                or (after[1] is not None and after[1][2]!=0)):
            unavailable['unavailable_reason']='source_changed_during_read'
            return [],unavailable,'unavailable'
        cap_reached=len(rows)>MAX_TASK_ROWS
        rows=rows[:MAX_TASK_ROWS]
        included=sum(1 for row in rows if sanitize_task(row,board_id) is not None)
        invalid=len(rows)-included
        record={'status':'partial' if cap_reached or invalid else 'available','row_count':len(rows),
                'included_count':included,'invalid_row_count':invalid,'limit':MAX_TASK_ROWS,
                'cap_reached':cap_reached,'read_mode':'immutable_checkpoint_only','wal_bytes':0,
                'recovery_history':recovery_history}
        return rows,record,'unavailable'
    except (OSError,ValueError,sqlite3.Error) as exc:
        unavailable['unavailable_reason']='source_error_'+type(exc).__name__
        return [],unavailable,'unavailable'
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            if db_fd is not None:
                os.close(db_fd)


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
def _sanitize_worker_diagnostics(items, board):
    """Retain only safe task IDs, statuses, diagnostic kinds, and severities."""
    if not isinstance(items, list):
        return [], 'unknown'
    out=[]; status='available'
    for task in items:
        if not isinstance(task, dict):
            status='partial'; continue
        task_id=safe_id(task.get('task_id') or task.get('id'))
        task_status=task.get('status')
        task_status=task_status.lower() if isinstance(task_status,str) and task_status.lower() in VALID else 'unknown'
        diagnostics=task.get('diagnostics', [])
        if not isinstance(diagnostics,list):
            status='partial'; continue
        for diagnostic in diagnostics:
            if not isinstance(diagnostic,dict):
                status='partial'; continue
            kind=safe_id(diagnostic.get('kind'))
            severity=diagnostic.get('severity')
            count=diagnostic.get('count',1)
            if not task_id or not kind or severity not in {'warning','error','critical'} or not isinstance(count,int) or isinstance(count,bool) or not 0<=count<=1_000_000:
                status='partial'; continue
            out.append({'board_id':board,'task_id':task_id,'status':task_status,'kind':kind,'severity':severity,'count':count})
    return out,status


def run_readonly_live():
    """Only invokes Hermes list/status commands; failures yield explicit unknown evidence."""
    max_output_bytes = 2_000_000
    def run_result(args):
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=20,check=False)
            size=len(p.stdout.encode('utf-8',errors='replace'))+len(p.stderr.encode('utf-8',errors='replace'))
            if size>max_output_bytes:
                return '', 'partial'
            return (p.stdout, 'available') if p.returncode==0 else ('', 'unavailable')
        except (OSError,subprocess.TimeoutExpired):return '', 'unavailable'
    def run(args):
        text,state=run_result(args)
        return text if state=='available' else ''
    profiles=[]
    for line in run(['hermes','profile','list']).splitlines():
        token=line.strip().split()[0] if line.strip() else ''
        token=re.sub(r'^[^A-Za-z0-9]+','',token)
        if safe_id(token) and token.lower() not in {'profile','name','id'}:profiles.append({'name':token,'kind':'unknown'})
    def json_rows(text, keys):
        try: payload=json.loads(text)
        except (ValueError,TypeError): return [], 'unavailable'
        if isinstance(payload,list): return payload, 'available'
        if isinstance(payload,dict):
            for key in keys:
                if isinstance(payload.get(key),list): return payload[key], 'available'
        return [], 'unavailable'
    board_text,board_list_status=run_result(['hermes','kanban','boards','list','--json','--all'])
    rawboards,parsed_status=json_rows(board_text,('items','boards')) if board_list_status=='available' else ([],board_list_status)
    if parsed_status!='available': board_list_status=parsed_status
    else:
        if any(not isinstance(b,dict) or not safe_id(b.get('slug') or b.get('id')) for b in rawboards):
            board_list_status='partial'
    rawboards=[b for b in rawboards if isinstance(b,dict) and safe_id(b.get('slug') or b.get('id'))]
    boards=[]; tasks=[]; task_collection_status={}; worker_diagnostics=[]; worker_diagnostics_status={}
    for b in rawboards:
        bid=safe_id(b.get('slug') or b.get('id'))
        if not bid:continue
        archived=b.get('archived') is True or b.get('status')=='archived'
        boards.append({'id':bid,'name':safe_text(b.get('name'),bid),'archived':archived,'paused':b.get('paused') is True})
        db_path=b.get('db_path')
        if archived or not isinstance(db_path,str):
            task_rows=[]
            record={'status':'unavailable','row_count':0,'included_count':0,'invalid_row_count':0,
                    'limit':MAX_TASK_ROWS,'cap_reached':False,'read_mode':'immutable_checkpoint_only',
                    'unavailable_reason':'archived_or_missing_database'}
            diagnostic_status='unavailable'
        else:
            task_rows,record,diagnostic_status=read_board_tasks(bid,db_path)
        included=invalid=0
        for t in task_rows:
            if not isinstance(t,dict):
                invalid+=1; continue
            v=sanitize_task(t,bid)
            if v:tasks.append(v); included+=1
            else:invalid+=1
        if invalid and record.get('status')=='available':record['status']='partial'
        record['included_count']=included; record['invalid_row_count']=invalid
        task_collection_status[bid]=record
        # The CLI diagnostics command opens the DB through Hermes's writable
        # initialization path; do not invoke it from this observer.
        worker_diagnostics_status[bid]=diagnostic_status
    cron_text=run(['hermes','cron','status']).strip().lower()
    match=re.search(r'ticker heartbeat:\s*(\d+)\s*(s|sec|seconds|m|minutes|h|hours)\s+ago',cron_text)
    scheduler='unknown'
    if match and 'scheduler host:' in cron_text:
        amount=int(match.group(1));unit=match.group(2)
        seconds=amount*(1 if unit in {'s','sec','seconds'} else 60 if unit in {'m','minutes'} else 3600)
        scheduler='available' if seconds<=120 else 'degraded'
    gateway_text=run(['hermes','gateway','status']).strip().lower()
    if 'active: active (running)' in gateway_text or 'user gateway service is running' in gateway_text or 'gateway is running' in gateway_text:gateway='available'
    elif any(x in gateway_text for x in ('active: inactive','gateway is not running','gateway service is stopped')):gateway='degraded'
    else:gateway='unknown'
    status={'scheduler':scheduler,'gateway':gateway,'heartbeat':'unknown','workers':'unknown'}
    if worker_diagnostics_status and all(value=='available' for value in worker_diagnostics_status.values()):
        status['workers']='degraded' if worker_diagnostics else 'healthy'
    # Only declared repositories are queried; retain aggregate counts and bounded sanitized failed-check identities.
    policy=load_json(Path(__file__).with_name('policy.json'))
    repo_map=policy.get('repos',{}) if isinstance(policy,dict) else {}
    prs={};repo_status={};repo_collection={};repo_failures={}
    exact_global={'bytes':0,'check_rows':0,'evidence_rows':0,'calls':0}
    exact_global_byte_cap=8_000
    exact_global_row_cap=300
    exact_global_call_cap=100
    status_values={'queued','in_progress','completed','waiting','requested','pending'}
    conclusion_values={'action_required','cancelled','failure','neutral','success','skipped','stale','timed_out'}
    if isinstance(repo_map,dict):
        for project,repo in repo_map.items():
            if not safe_id(project) or not valid_repo(repo):continue
            repo_status[project]='unavailable'
            args=['gh','pr','list','--repo',repo,'--state','open','--limit','1000','--json','number,state,headRefOid,statusCheckRollup']
            ptext,query_status=run_result(args)
            if query_status!='available':
                repo_status[project]=query_status
                repo_collection[project]={'status':query_status,'rows_observed':0,'limit':1000,'cap_reached':False}
                continue
            try: rows=json.loads(ptext)
            except (ValueError,TypeError):
                repo_collection[project]={'status':'unavailable','rows_observed':0,'limit':1000,'cap_reached':False}
                continue
            if not isinstance(rows,list):
                repo_collection[project]={'status':'unavailable','rows_observed':0,'limit':1000,'cap_reached':False}
                continue
            counts={'open':0,'checks_passing':0,'checks_failing':0,'checks_pending':0,'checks_unknown':0}
            invalid=0;failure_records={};ambiguous=set();failure_cap=False
            exact_heads=[]; exact_cap=False
            exact_incomplete=False; exact_cap=False
            for pr in rows:
                if not isinstance(pr,dict):
                    invalid+=1; continue
                state=pr.get('state')
                if state=='OPEN':counts['open']+=1
                else:invalid+=1
                number=pr.get('number')
                head=pr.get('headRefOid')
                valid_head=isinstance(head,str) and re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})',head)
                if valid_head and type(number) is int and number > 0:
                    if len(exact_heads)<100:
                        evidence={'pr_number':number,'head_sha':head.lower(),'status':'unknown','checks':[]}
                        if (exact_global['calls'] < exact_global_call_cap
                                and exact_global['bytes'] < exact_global_byte_cap
                                and exact_global['evidence_rows'] < exact_global_row_cap
                                and exact_global['check_rows'] < exact_global_row_cap):
                            exact_global['calls']+=1
                            api_args=['gh','api',f'repos/{repo}/commits/{head.lower()}/check-runs?per_page=100']
                            api_text,api_status=run_result(api_args)
                            try: payload=json.loads(api_text) if api_status=='available' else None
                            except (ValueError,TypeError): payload=None
                            runs=payload.get('check_runs') if isinstance(payload,dict) else None
                            total=payload.get('total_count') if isinstance(payload,dict) else None
                            if (api_status!='available' or not isinstance(payload,dict) or not isinstance(runs,list)
                                    or type(total) is not int or total<0 or total!=len(runs) or len(runs)>100 or not runs):
                                exact_incomplete=True
                            else:
                                clean=[]; bad=len(runs)>exact_global_row_cap-exact_global['check_rows']
                                ids=set()
                                for item in runs[:100]:
                                    if not isinstance(item,dict): bad=True;continue
                                    rid=item.get('id'); sha=item.get('head_sha'); status=item.get('status'); conclusion=item.get('conclusion')
                                    created=safe_time(item.get('started_at')); completed=safe_time(item.get('completed_at'))
                                    if (not {'id','head_sha','status','conclusion','started_at','completed_at'}<=item.keys()
                                            or type(rid) is not int or rid<1 or sha!=head.lower() or not isinstance(status,str) or status not in status_values
                                            or (conclusion is not None and (not isinstance(conclusion,str) or conclusion not in conclusion_values))
                                            or (item.get('started_at') is None and status not in {'queued','waiting','requested','pending'})
                                            or (item.get('started_at') is not None and not created) or (item.get('completed_at') is not None and not completed)
                                            or (status=='completed' and (not completed or conclusion is None))
                                            or rid in ids):
                                        bad=True;continue
                                    ids.add(rid)
                                    clean.append({'check_run_id':rid,'status':status,'conclusion':conclusion or 'unknown','started_at':created,'completed_at':completed})
                                if exact_global['check_rows']+len(clean)>exact_global_row_cap:
                                    bad=True; exact_cap=True; clean=[]
                                if bad: clean=[]
                                if not clean: bad=True
                                evidence.update({'status':'partial' if bad else 'available','checks':clean})
                                if bad: exact_incomplete=True
                        else:
                            exact_incomplete=True;exact_cap=True
                        serialized_size=len(json.dumps(evidence,separators=(',',':')).encode('utf-8'))
                        if exact_global['bytes']+serialized_size>exact_global_byte_cap:
                            evidence={'pr_number':number,'head_sha':head.lower(),'status':'partial','checks':[]}
                            exact_incomplete=True;exact_cap=True
                            serialized_size=len(json.dumps(evidence,separators=(',',':')).encode('utf-8'))
                        if (exact_global['bytes']+serialized_size<=exact_global_byte_cap
                                and exact_global['evidence_rows']<exact_global_row_cap):
                            exact_global['bytes']+=serialized_size
                            exact_global['evidence_rows']+=1
                            exact_global['check_rows']+=len(evidence['checks'])
                            exact_heads.append(evidence)
                        else:
                            exact_incomplete=True;exact_cap=True
                            exact_global['bytes']=exact_global_byte_cap
                    else: exact_cap=True;exact_incomplete=True

                checks=pr.get('statusCheckRollup')
                if not isinstance(checks,list) or not checks:
                    counts['checks_unknown']+=1
                    continue
                for c in checks:
                    if not isinstance(c,dict):
                        counts['checks_unknown']+=1; invalid+=1
                        continue
                    conclusion=c.get('conclusion') or c.get('state')
                    if conclusion in ('SUCCESS','NEUTRAL','SKIPPED'):
                        counts['checks_passing']+=1
                    elif conclusion in ('FAILURE','ERROR','TIMED_OUT','ACTION_REQUIRED'):
                        counts['checks_failing']+=1
                        name=c.get('name') or c.get('context')
                        if (type(number) is not int or number<1 or not isinstance(name,str) or not name.strip()
                                or len(name)>200 or any(ord(ch)<32 for ch in name)):
                            invalid+=1;continue
                        identity=(number,name)
                        if identity in failure_records or identity in ambiguous:
                            failure_records.pop(identity,None);ambiguous.add(identity);invalid+=1;continue
                        if len(failure_records)>=100:
                            failure_cap=True;continue
                        failure_records[identity]={'repo':repo,'pr_number':number,'check_name':name,'conclusion':conclusion}
                    elif conclusion in ('PENDING','QUEUED','IN_PROGRESS','EXPECTED'):
                        counts['checks_pending']+=1
                    else:counts['checks_unknown']+=1
            cap_reached=len(rows)>=1000
            repo_status[project]='partial' if cap_reached or invalid or failure_cap else 'available'
            repo_collection[project]={'status':repo_status[project],'rows_observed':len(rows),'limit':1000,
                                      'cap_reached':cap_reached,'invalid_row_count':invalid,
                                      'failure_count':counts['checks_failing'],'failure_records_included':len(failure_records),
                                      'failure_cap_reached':failure_cap,'exact_head_limit':100,'exact_head_cap_reached':exact_cap}
            prs[project]=counts
            repo_collection[project]['exact_head_evidence']=exact_heads
            if exact_cap or exact_incomplete or len(exact_heads)<min(len(rows),100) or any(not isinstance(row,dict) or not isinstance(row.get('headRefOid'),str)
                    or re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})',row.get('headRefOid','')) is None for row in rows):
                repo_status[project]='partial';repo_collection[project]['status']='partial'
            repo_failures[project]=sorted(failure_records.values(),key=lambda item:(item['pr_number'],item['check_name']))
    return {'profiles':profiles,'boards':boards,'board_list_status':board_list_status,'tasks':tasks,'task_collection_status':task_collection_status,
            'task_read_mode':'immutable_checkpoint_only','status':status,'repos':prs,'repo_status':repo_status,'repo_collection':repo_collection,
            'repo_failures':repo_failures,'worker_diagnostics':worker_diagnostics,'worker_diagnostics_status':worker_diagnostics_status,
            'observed_at':datetime.now(timezone.utc).isoformat()}


def _worker_health(project, runtime):
    board=project.get('board')
    statuses=runtime.get('worker_diagnostics_status',{}) if isinstance(runtime.get('worker_diagnostics_status',{}),dict) else {}
    source_status=statuses.get(board,'unknown') if board else 'unknown'
    raw=runtime.get('worker_diagnostics',[]) if isinstance(runtime.get('worker_diagnostics',[]),list) else []
    rows=[]
    for item in raw:
        if not isinstance(item,dict) or item.get('board_id')!=board:continue
        task_id=safe_id(item.get('task_id')); kind=safe_id(item.get('kind')); severity=item.get('severity')
        count=item.get('count',1)
        status=item.get('status')
        if task_id and kind and severity in {'warning','error','critical'} and isinstance(count,int) and not isinstance(count,bool) and 0<=count<=1_000_000:
            rows.append({'task_id':task_id,'kind':kind,'severity':severity,'count':count,'status':status if isinstance(status,str) and status in VALID else 'unknown'})
    rows=sorted(set(tuple(sorted(row.items())) for row in rows))
    rows=[dict(row) for row in rows]
    severity_counts={level:sum(item['count'] for item in rows if item['severity']==level) for level in ('critical','error','warning')}
    kind_counts={}
    for item in rows:kind_counts[item['kind']]=kind_counts.get(item['kind'],0)+item['count']
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    source_status=source_status if source_status in {'available','partial','unavailable'} else 'unknown'
    state='unknown' if source_status!='available' else 'degraded' if rows else 'healthy'
    return {'status':state,'source_status':source_status,'diagnostic_count':len(rows),'severity_counts':severity_counts,
            'diagnostic_kind_counts':dict(sorted(kind_counts.items())[:20]),'diagnostic_kinds_truncated':len(kind_counts)>20,
            'diagnostic_digest':digest,'diagnostics':rows[:100],'sample_truncated':len(rows)>100}


def _sanitize_recovery_history(raw, board):
    raw=raw if isinstance(raw,dict) else {}
    status=raw.get('status') if raw.get('status') in {'available','partial','unavailable'} else 'unknown'
    observed=safe_time(raw.get('observed_at'))
    records=[];invalid=False
    source='immutable_checkpoint_sqlite' if raw.get('source')=='immutable_checkpoint_sqlite' else None
    rows=raw.get('records',[])
    if not isinstance(rows,list):rows=[];invalid=True
    for row in rows[:MAX_RECOVERY_HISTORY_ROWS]:
        if not isinstance(row,dict):invalid=True;continue
        task_id=safe_id(row.get('id'));event_key=row.get('event_key');action=row.get('action')
        key=row.get('idempotency_key');task_status=row.get('status')
        max_retries=row.get('max_retries')
        expected=(hashlib.sha256((event_key+chr(0)+action).encode()).hexdigest()
                  if isinstance(event_key,str) and isinstance(action,str) else None)
        if (not task_id or not isinstance(event_key,str) or not HEX64.fullmatch(event_key)
                or not isinstance(action,str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',action)
                or key!=expected or not isinstance(task_status,str) or task_status.lower() not in VALID
                or type(max_retries) is not int or not 1<=max_retries<=20):
            invalid=True;continue
        item={'id':task_id,'status':task_status.lower(),'event_key':event_key,'action':action,
              'idempotency_key':key,'max_retries':max_retries}
        for field in ('created_at','completed_at'):
            stamp=safe_time(row.get(field))
            if stamp:item[field]=stamp
        records.append(item)
    cap=raw.get('cap_reached') is True
    if invalid or cap:
        status='partial' if status=='available' else status
    complete=(status=='available' and source is not None and observed is not None
              and raw.get('complete') is True and not cap and not invalid)
    return {'board_id':board,'status':status,'source':source or 'unknown','observed_at':observed,
            'complete':complete,'records':records,'limit':MAX_RECOVERY_HISTORY_ROWS,'cap_reached':cap}


def discover(manifest,runtime,now=None):
    status_values={'queued','in_progress','completed','waiting','requested','pending'}
    conclusion_values={'action_required','cancelled','failure','neutral','success','skipped','stale','timed_out'}
    sanitized_global=0
    sanitized_bytes=0
    sanitized_rows=0
    now=now or datetime.now(timezone.utc); now=now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now; projects={}; roles={}
    policy=load_json(Path(__file__).with_name('policy.json'))
    stale_after=policy.get('stale_after_hours',72) if isinstance(policy,dict) else 72
    if not isinstance(stale_after,int) or isinstance(stale_after,bool) or not 1<=stale_after<=8760:stale_after=72
    for p in manifest.get('projects',[]):
        if not isinstance(p,dict):continue
        pid=safe_id(p.get('id'))
        if pid:projects[pid]={'id':pid,'name':safe_text(p.get('name'),pid),'board':safe_id(p.get('board')),'lead':safe_id(p.get('lead')),'paused':p.get('paused') is True or p.get('archived') is True,'scope':'governed'}
    role_aliases=set()
    for r in manifest.get('profiles',[]):
        if isinstance(r,dict) and safe_id(r.get('id')):
            pid=r['id'];roles[pid]={'kind':safe_text(r.get('kind'),'unknown'),'scope':'governed'}
            alias=safe_id(r.get('hermes_profile'))
            if alias:role_aliases.add(alias)
    boards=set(p['board'] for p in projects.values() if p['board'])
    governed_boards=set(p['board'] for p in projects.values() if p.get('scope')=='governed' and p.get('board'))
    for b in runtime.get('boards',[]):
        if not isinstance(b,dict):continue
        bid=safe_id(b.get('id'))
        if not bid:continue
        boards.add(bid)
        if bid not in projects and bid not in governed_boards:projects[bid]={'id':bid,'name':safe_text(b.get('name'),bid),'board':bid,'paused':b.get('archived') is True or b.get('paused') is True,'scope':'unclassified'}
    for p in runtime.get('profiles',[]):
        name=safe_id(p.get('name')) if isinstance(p,dict) else None
        if name and name not in roles and name not in role_aliases:roles[name]={'kind':safe_text(p.get('kind'),'unknown'),'scope':'unclassified'}
    tasks=[]
    for t in runtime.get('tasks',[]):
        if isinstance(t,dict):
            clean=sanitize_task(t)
            if clean:tasks.append(clean)
    result=[]
    task_collection_status=runtime.get('task_collection_status',{}) if isinstance(runtime.get('task_collection_status',{}),dict) else {}
    board_list_status=runtime.get('board_list_status') if runtime.get('board_list_status') in {'available','partial','unavailable'} else 'unknown'
    for pid,p in sorted(projects.items()):
        if p['paused'] or any(b.get('id')==p['board'] and (b.get('archived') or b.get('paused')) for b in runtime.get('boards',[]) if isinstance(b,dict)):continue
        collection=task_collection_status.get(p.get('board'),{}) if p.get('board') else {}
        collection_status=collection.get('status') if isinstance(collection,dict) and collection.get('status') in {'available','partial','unavailable'} else board_list_status if board_list_status!='available' else 'unknown'
        rows=[t for t in tasks if t.get('project_id')==pid or p['board'] and t.get('board_id')==p['board']]; enriched=[]
        for t in sorted(rows,key=lambda x:x['id']):
            age=None; stamp=t.get('updated_at') or t.get('created_at')
            if stamp:
                try:
                    created=datetime.fromisoformat(stamp.replace('Z','+00:00')); created=created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
                    age=max(0,int((now-created).total_seconds()//3600))
                except ValueError:pass
            enriched.append({**t,'age_hours':age,'stale':bool(age is not None and age>=stale_after and t['status'] not in {'done','completed','cancelled'})})
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
        worker_health=_worker_health(p,runtime)
        recovery_history=_sanitize_recovery_history(collection.get('recovery_history') if isinstance(collection,dict) else None,p.get('board'))
        result.append({'project':{k:v for k,v in p.items() if k!='paused'},'tasks':enriched,'task_count':len(enriched),'task_collection_status':collection_status,'task_collection':collection if isinstance(collection,dict) else {},'recovery_history':recovery_history,'completed_count_observed':sum(t['status'] in {'done','completed'} for t in enriched),'completed_count_7d_observed':len(completed),'blocked_count':sum(t['status']=='blocked' for t in enriched),'stale_count':sum(t['stale'] for t in enriched),'running_count':sum(t['status'] in {'running','in_progress'} for t in enriched),'worker_health':worker_health,'kpis':kpis})
    status=runtime.get('status',{}); cleanstatus={}
    for k in ('gateway','scheduler','heartbeat','workers'):
        v=status.get(k) if isinstance(status,dict) else None
        cleanstatus[k]=v if isinstance(v,(str,int,float,bool)) and (not isinstance(v,str) or v in {'available','unknown','healthy','running','stopped','degraded','ok','error','idle','active','paused'}) else 'unknown'
    worker_states=[item.get('worker_health',{}).get('status') for item in result if isinstance(item.get('worker_health'),dict)]
    if worker_states and all(value in {'healthy','degraded'} for value in worker_states):
        cleanstatus['workers']='degraded' if 'degraded' in worker_states else 'healthy'
    governed=sorted(p['id'] for p in manifest.get('projects',[]) if isinstance(p,dict) and safe_id(p.get('id')))
    policy=load_json(Path(__file__).with_name('policy.json')); mapping=policy.get('repos',{})
    raw_repos=runtime.get('repos',{}) if isinstance(runtime.get('repos',{}),dict) else {}; repos={}
    raw_repo_status=runtime.get('repo_status',{}) if isinstance(runtime.get('repo_status',{}),dict) else {}; repo_status={}
    raw_repo_collection=runtime.get('repo_collection',{}) if isinstance(runtime.get('repo_collection',{}),dict) else {}; repo_collection={}
    allowed=set(mapping) if isinstance(mapping,dict) else set()
    for project in allowed:
        if not safe_id(project):continue
        counts=raw_repos.get(project); status=raw_repo_status.get(project)
        if status not in {'available','unavailable','unknown','partial'}:status='available' if isinstance(counts,dict) else 'unknown'
        repo_status[project]=status
        record=raw_repo_collection.get(project,{})
        if isinstance(record,dict):
            clean_record={'status':status}
            for key in ('rows_observed','limit','invalid_row_count','failure_count','failure_records_included','exact_head_limit'):
                value=record.get(key)
                if isinstance(value,int) and not isinstance(value,bool) and value>=0:clean_record[key]=value
            if isinstance(record.get('cap_reached'),bool):clean_record['cap_reached']=record['cap_reached']
            if isinstance(record.get('failure_cap_reached'),bool):clean_record['failure_cap_reached']=record['failure_cap_reached']
            exact=record.get('exact_head_evidence',[]); exact_clean=[]; exact_bad=not isinstance(exact,list) or len(exact)>100
            for row in exact[:100] if isinstance(exact,list) else []:
                if not isinstance(row,dict) or type(row.get('pr_number')) is not int or row['pr_number']<1 or not isinstance(row.get('head_sha'),str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',row['head_sha']):
                    exact_bad=True;continue
                checks=row.get('checks'); clean_checks=[]; row_bad=False
                if not isinstance(checks,list) or len(checks)>100: exact_bad=True; checks=[]
                for check in checks[:100]:
                    if (sanitized_global>=300 or not isinstance(check,dict) or type(check.get('check_run_id')) is not int or check['check_run_id']<1 or not isinstance(check.get('status'),str) or check.get('status') not in status_values or not isinstance(check.get('conclusion'),str) or check.get('conclusion') not in conclusion_values|{'unknown'}):
                        exact_bad=True;continue
                    started=safe_time(check.get('started_at')); completed=safe_time(check.get('completed_at'))
                    if (not {'check_run_id','status','conclusion','started_at','completed_at'}<=check.keys()
                            or (check.get('started_at') is None and check.get('status') not in {'queued','waiting','requested','pending'})
                            or (check.get('started_at') is not None and not started) or (check.get('completed_at') is not None and not completed)
                            or (check.get('status')=='completed' and (not completed or check.get('conclusion') in (None,'unknown')))):
                        exact_bad=True;continue
                    if any(c['check_run_id']==check['check_run_id'] for c in clean_checks): exact_bad=True;row_bad=True;continue
                    sanitized_global+=1
                    clean_checks.append({'check_run_id':check['check_run_id'],'status':check['status'],'conclusion':check['conclusion'],'started_at':started,'completed_at':completed})
                row_status=row.get('status') if isinstance(row.get('status'),str) and row.get('status') in {'available','partial','unknown'} else 'unknown'
                if row_bad:
                    clean_checks=[]
                    row_status='partial'
                if not clean_checks: row_status='unknown' if not row_bad else 'partial'
                exact_row={'pr_number':row['pr_number'],'head_sha':row['head_sha'],'status':row_status,'checks':clean_checks}
                row_bytes=len(json.dumps(exact_row,separators=(',',':')).encode('utf-8'))
                if sanitized_global+len(clean_checks)<=300 and sanitized_rows<300 and row_bytes<=8_000 and sanitized_bytes+row_bytes<=8_000:
                    exact_clean.append(exact_row)
                    sanitized_rows+=1
                    sanitized_bytes+=row_bytes
                else: exact_bad=True
            clean_record['exact_head_evidence']=exact_clean
            if exact_bad or any(x['status']!='available' for x in exact_clean):
                repo_status[project]='partial' if exact_clean else 'unknown';clean_record['status']=repo_status[project]
            repo_collection[project]=clean_record
        if isinstance(counts,dict):
            clean={k:counts[k] for k in ('open','checks_passing','checks_failing','checks_pending','checks_unknown') if isinstance(counts.get(k),int) and not isinstance(counts.get(k),bool) and counts[k]>=0}
            if clean:repos[project]=clean
    raw_repo_failures=runtime.get('repo_failures',{}) if isinstance(runtime.get('repo_failures',{}),dict) else {}
    repo_failures={}
    for project in allowed:
        expected_repo=mapping.get(project) if isinstance(mapping,dict) else None
        rows=raw_repo_failures.get(project,[])
        clean_failures=[];seen=set();invalid_failure=False
        if not isinstance(rows,list):rows=[];invalid_failure=True
        for row in rows[:100]:
            if not isinstance(row,dict):invalid_failure=True;continue
            repo=row.get('repo');number=row.get('pr_number');name=row.get('check_name');conclusion=row.get('conclusion')
            if (repo!=expected_repo or not valid_repo(repo) or type(number) is not int or number<1
                    or not isinstance(name,str) or not name.strip() or len(name)>200 or any(ord(ch)<32 for ch in name)
                    or conclusion not in {'FAILURE','ERROR','TIMED_OUT','ACTION_REQUIRED'}
                    or (number,name) in seen):
                invalid_failure=True;continue
            seen.add((number,name))
            clean_failures.append({'repo':repo,'pr_number':number,'check_name':name,'conclusion':conclusion})
        counts=raw_repos.get(project,{}) if isinstance(raw_repos.get(project,{}),dict) else {}
        failing_count=counts.get('checks_failing')
        if (len(rows)>100 or invalid_failure or (repo_status.get(project)=='available'
                and isinstance(failing_count,int) and not isinstance(failing_count,bool)
                and failing_count!=len(clean_failures))):
            repo_status[project]='partial'
        repo_failures[project]=clean_failures
    collection_status=runtime.get('board_list_status') if runtime.get('board_list_status') in {'available','partial','unavailable'} else 'unknown'
    observed_at=safe_time(runtime.get('observed_at'))
    return {'schema_version':1,'observed_at':observed_at,'scope':{'governed_projects':governed,'unclassified_projects':sorted(k for k,v in projects.items() if v['scope']=='unclassified'),'governed_roles':sorted(k for k,v in roles.items() if v['scope']=='governed'),'unclassified_roles':sorted(k for k,v in roles.items() if v['scope']=='unclassified'),'boards':sorted(boards)},'projects':result,'profiles':sorted([{'id':k,'kind':v['kind'],'scope':v['scope']} for k,v in roles.items()],key=lambda x:x['id']),'runtime_status':cleanstatus,'board_list_status':collection_status,'task_read_mode':runtime.get('task_read_mode') if runtime.get('task_read_mode') in {'immutable_checkpoint_only'} else 'unknown','repos':repos,'repo_status':repo_status,'repo_collection':repo_collection,'repo_failures':repo_failures,'collection':'read-only'}
def stable_events(report):
    out=[]
    for p in report['projects']:
        for t in p['tasks']:
            identity={'project':p['project']['id'],'task_id':t['id'],'status':t['status']}
            key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            out.append({'key':key,'project':identity['project'],'task':t})
        health=p.get('worker_health',{})
        diagnostics=health.get('diagnostics',[]) if isinstance(health,dict) else []
        for d in diagnostics if isinstance(diagnostics,list) else []:
            if not isinstance(d,dict):continue
            identity={'project':p['project']['id'],'task_id':d.get('task_id'),'diagnostic_kind':d.get('kind'),'severity':d.get('severity')}
            key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            out.append({'key':key,'project':identity['project'],'task_id':identity['task_id'],'kind':'worker_diagnostic','diagnostic_kind':identity['diagnostic_kind'],'severity':identity['severity'],'count':d.get('count',1)})
    repos=report.get('repos',{}) if isinstance(report.get('repos',{}),dict) else {}
    statuses=report.get('repo_status',{}) if isinstance(report.get('repo_status',{}),dict) else {}
    for project,counts in repos.items():
        failures=counts.get('checks_failing',0) if isinstance(counts,dict) else 0
        if statuses.get(project) in {'available','partial'} and isinstance(failures,int) and not isinstance(failures,bool) and failures>0:
            identity={'project':project,'kind':'repo_checks','status':'failing','count':failures}
            key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            out.append({'key':key,**identity})
    repo_failures=report.get('repo_failures',{}) if isinstance(report.get('repo_failures',{}),dict) else {}
    for project,records in repo_failures.items():
        if statuses.get(project)!='available' or not isinstance(records,list):continue
        for failure in records:
            if not isinstance(failure,dict):continue
            repo=failure.get('repo');number=failure.get('pr_number');name=failure.get('check_name');conclusion=failure.get('conclusion')
            if not valid_repo(repo) or type(number) is not int or not isinstance(name,str) or not isinstance(conclusion,str):continue
            identity={'project':project,'repo':repo,'pr_number':number,'check_name':name,'conclusion':conclusion}
            event_identity={key:identity[key] for key in ('project','repo','pr_number','check_name')}
            key=hashlib.sha256(json.dumps(event_identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            out.append({'key':key,'kind':'repo_check','status':'failing',**identity})
    return out
def collect(manifest_path,runtime_path,output_path):
    r=discover(load_json(manifest_path),load_json(runtime_path));r['events']=stable_events(r);Path(output_path).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');return r
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--runtime');ap.add_argument('--live',action='store_true');ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.live and a.runtime:ap.error('choose --live or --runtime')
    runtime=run_readonly_live() if a.live else load_json(a.runtime) if a.runtime else ap.error('provide --runtime or --live')
    report=discover(load_json(a.manifest),runtime);report['events']=stable_events(report);Path(a.output).write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(f"projects={len(report['projects'])} tasks={sum(p['task_count'] for p in report['projects'])} scope=read-only")
