import json, tempfile, unittest
from pathlib import Path
from automation.monitoring.collector import collect, discover, run_readonly_live

class MonitorEndToEnd(unittest.TestCase):
 def test_dynamic_scope_pause_dedupe_unknown_and_policy(self):
  root=Path(__file__).parent/'fixtures'/'monitoring'
  with tempfile.TemporaryDirectory() as tmp:
   m=json.loads((root/'manifest.json').read_text()); r=json.loads((root/'runtime.json').read_text())
   m['projects'].append({'id':'synthetic-new','name':'Synthetic','board':'synthetic-board','paused':False})
   m['projects'].append({'id':'paused-demo','name':'Paused','board':'paused-board','paused':True})
   m['profiles'].append({'id':'synthetic-role','hermes_profile':'synthetic-role','kind':'lead'})
   m['profiles'].append({'id':'lnx','hermes_profile':'default','kind':'orchestrator'})
   r['profiles'].append({'name':'default','kind':'orchestrator'})
   r['boards'].extend([{'id':'synthetic-board','name':'Synthetic'}, {'id':'runtime-extra','name':'extra'}])
   r['profiles'].append({'name':'runtime-extra-profile','kind':'worker'})
   r['tasks'].append({'id':'task-1','project_id':'synthetic-new','board_id':'synthetic-board','status':'done','created_at':'2026-01-01'})
   m['metrics']=[{'project':'synthetic-new','name':'quality','source':'declared-quality'}]
   mp=Path(tmp)/'manifest.json'; rp=Path(tmp)/'runtime.json'; o1=Path(tmp)/'one.json';o2=Path(tmp)/'two.json'
   mp.write_text(json.dumps(m));rp.write_text(json.dumps(r))
   first=collect(mp,rp,o1);second=collect(mp,rp,o2)
   self.assertEqual(o1.read_bytes(),o2.read_bytes())
   self.assertIn('synthetic-new',[p['project']['id'] for p in first['projects']])
   self.assertNotIn('paused-demo',[p['project']['id'] for p in first['projects']])
   self.assertIn('runtime-extra',first['scope']['unclassified_projects'])
   self.assertIn('runtime-extra-profile',first['scope']['unclassified_roles'])
   self.assertNotIn('default',first['scope']['unclassified_roles'])
   self.assertEqual(first['projects'][-1]['kpis']['quality'],{'value':None,'status':'unknown'})
   self.assertEqual(len(first['events']),len({e['key'] for e in first['events']}))
   policy=json.loads((Path(__file__).parents[1]/'automation/monitoring/policy.json').read_text())
   self.assertEqual(policy['max_fix_attempts'],2);self.assertTrue(policy['approvals_required']);self.assertEqual(policy['cron_mode'],'deny')
   self.assertEqual(policy['task_read_mode'],'immutable_checkpoint_only')
   self.assertEqual(policy['recovery_mode'],'approval_required_proposal_only')
   self.assertEqual(policy['max_retries_semantics'],'consecutive_spawn_failures')

 def test_adversarial_runtime_is_allowlisted(self):
  root=Path(__file__).parent/'fixtures'/'monitoring'
  m=json.loads((root/'manifest.json').read_text()); r=json.loads((root/'runtime.json').read_text())
  r['tasks']=[{'id':'safe-task','project_id':'base','board_id':'base-board','status':'blocked','body':'SECRET BODY','path':'/private/path','session_id':'SESSION','error':'TRACE SECRET','created_at':'2020-01-01T00:00:00Z'}]
  r['profiles'].append({'name':'private-profile','kind':'worker','path':'/private','session_id':'SESSION'})
  r['status']={'gateway':'healthy','scheduler':'TOKEN_SECRET','workers':'${HOME}/private','error':'secret'}
  r['worker_diagnostics_status']={'base-board':'available'}
  r['worker_diagnostics']=[{'board_id':'base-board','task_id':'safe-task','status':'blocked','kind':'repeated_failures','severity':'error','detail':'SECRET PRIVATE'}]
  report=discover(m,r,now=__import__('datetime').datetime(2026,1,1,tzinfo=__import__('datetime').timezone.utc))
  out=json.dumps(report)
  project=report['projects'][0]
  self.assertEqual(project['worker_health']['status'],'degraded')
  self.assertEqual(report['runtime_status']['workers'],'degraded')
  self.assertTrue(any(e.get('kind')=='worker_diagnostic' and e.get('diagnostic_kind')=='repeated_failures' for e in __import__('automation.monitoring.collector',fromlist=['stable_events']).stable_events(report)))
  for secret in ('SECRET BODY','/private/path','SESSION','TRACE SECRET','TOKEN_SECRET','${HOME}/private','secret','SECRET PRIVATE'):
   self.assertNotIn(secret,out)
  self.assertIn('age_hours',out); self.assertIn('blocked_count',out)

 def test_live_adapter_uses_checkpoint_only_board_reader(self):
  import unittest.mock as mock
  import subprocess
  responses={
   ('hermes','profile','list'):'Profile Model Gateway Alias Distribution\n───────────────\n◆default gpt-6-luna running — —\nextra-profile gpt-6-luna running — —\n',
   ('hermes','kanban','boards','list','--json','--all'):json.dumps([{'slug':'new-board','name':'Board','db_path':'/SECRET'}]),
   ('hermes','cron','status'):'Scheduler host: host (PID 1)\nTicker heartbeat: 31s ago',('hermes','gateway','status'):'✓ User gateway service is running'}
  calls=[]
  def fake(args,**kwargs):
   calls.append(tuple(args));return subprocess.CompletedProcess(args,0,responses.get(tuple(args),''),'')
  board_read=( [{'id':'t1','status':'running','board_id':'new-board'}],
               {'status':'available','row_count':1,'included_count':1,'invalid_row_count':0,'limit':20000,'cap_reached':False,'read_mode':'immutable_checkpoint_only','wal_bytes':0},
               'unavailable')
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   with mock.patch('automation.monitoring.collector.read_board_tasks',return_value=board_read):
    snap=run_readonly_live()
  self.assertEqual(calls.count(('hermes','profile','list')),1)
  self.assertIn({'name':'default','kind':'unknown'},snap['profiles'])
  self.assertIn({'name':'extra-profile','kind':'unknown'},snap['profiles'])
  self.assertEqual(snap['tasks'],[{'id':'t1','status':'running','board_id':'new-board'}])
  self.assertEqual(snap['task_collection_status']['new-board']['status'],'available')
  self.assertEqual(snap['board_list_status'],'available')
  self.assertEqual(snap['worker_diagnostics_status'],{'new-board':'unavailable'})
  self.assertEqual(snap['status'],{'scheduler':'available','gateway':'available','heartbeat':'unknown','workers':'unknown'})
  self.assertEqual(snap['task_read_mode'],'immutable_checkpoint_only')
  self.assertFalse(any(x[:3]==('hermes','kanban','--board') and ('list' in x or 'diagnostics' in x) for x in calls))
  encoded=json.dumps(snap)
  for x in ('PRIVATE','SECRET','/SECRET','db_path','workspace','session_id','detail','title'):self.assertNotIn(x,encoded)

 def test_worker_diagnostics_stay_unknown_without_safe_diagnostic_export(self):
  import unittest.mock as mock, subprocess
  def fake(args,**kwargs):
   if tuple(args)==('hermes','kanban','boards','list','--json','--all'):
    return subprocess.CompletedProcess(args,0,json.dumps([{'slug':'b','db_path':'/fake'}]),'')
   if tuple(args)==('hermes','cron','status'):
    return subprocess.CompletedProcess(args,0,'Ticker heartbeat: 20s ago','')
   return subprocess.CompletedProcess(args,0,'','')
  record={'status':'available','row_count':0,'included_count':0,'invalid_row_count':0,'limit':20000,'cap_reached':False,'read_mode':'immutable_checkpoint_only','wal_bytes':0}
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   with mock.patch('automation.monitoring.collector.read_board_tasks',return_value=([],record,'unavailable')):
    snapshot=run_readonly_live()
  self.assertEqual(snapshot['worker_diagnostics_status'],{'b':'unavailable'})
  self.assertEqual(snapshot['status']['workers'],'unknown')
  report=discover({'projects':[{'id':'p','board':'b'}],'profiles':[],'metrics':[]},snapshot)
  self.assertEqual(report['projects'][0]['worker_health']['status'],'unknown')

 def test_task_reader_failure_and_diagnostics_unavailable_are_visible(self):
  import unittest.mock as mock, subprocess
  def fake(args,**kwargs):
   key=tuple(args)
   if key==('hermes','kanban','boards','list','--json','--all'):return subprocess.CompletedProcess(args,0,json.dumps([{'slug':'b','db_path':'/fake'}]),'')
   return subprocess.CompletedProcess(args,0,'','')
  record={'status':'unavailable','row_count':0,'included_count':0,'invalid_row_count':0,'limit':20000,'cap_reached':False,'read_mode':'immutable_checkpoint_only'}
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   with mock.patch('automation.monitoring.collector.read_board_tasks',return_value=([],record,'unavailable')):
    snapshot=run_readonly_live()
  self.assertEqual(snapshot['task_collection_status']['b']['status'],'unavailable')
  self.assertEqual(snapshot['worker_diagnostics_status']['b'],'unavailable')
  report=discover({'projects':[{'id':'p','board':'b'}],'profiles':[],'metrics':[]},snapshot)
  self.assertEqual(report['projects'][0]['task_collection_status'],'unavailable')
  self.assertEqual(report['projects'][0]['worker_health']['source_status'],'unavailable')
  self.assertEqual(report['projects'][0]['worker_health']['status'],'unknown')

 def test_epoch_stale_boundary_states_period_and_archive_pause(self):
  from datetime import datetime,timezone
  m={'projects':[{'id':'p','board':'b'},{'id':'archived','board':'ab','archived':True},{'id':'paused','board':'pb','paused':True}], 'profiles':[{'id':'lnx','hermes_profile':'default','kind':'orchestrator'}], 'metrics':[]}
  now=datetime(2026,1,10,tzinfo=timezone.utc); base=int(now.timestamp())
  r={'boards':[{'id':'b'},{'id':'ab','archived':True},{'id':'pb','paused':True}], 'profiles':[{'name':'default'}], 'tasks':[
   {'id':'exact','board_id':'b','status':'triage','created_at':base-72*3600},
   {'id':'young','board_id':'b','status':'scheduled','created_at':base-72*3600+1},
   {'id':'ready','board_id':'b','status':'ready','created_at':base-1},
   {'id':'review','board_id':'b','status':'review','created_at':base-80*3600},
   {'id':'updated','board_id':'b','status':'ready','created_at':base-80*3600,'updated_at':base-1},
   {'id':'freshdone','board_id':'b','status':'completed','created_at':base-3600,'completed_at':now.isoformat()},
   {'id':'olddone','board_id':'b','status':'done','completed_at':int(base-8*86400)},
   {'id':'notincluded','board_id':'ab','status':'open'}, {'id':'notincluded2','board_id':'pb','status':'open'}], 'status':{}}
  from automation.monitoring.collector import stable_events
  report=discover(m,r,now); project=report['projects'][0]
  later=discover(m,r,datetime(2026,1,10,1,tzinfo=timezone.utc))
  self.assertEqual([x['key'] for x in stable_events(report)],[x['key'] for x in stable_events(later)])
  self.assertEqual({t['id']:t['stale'] for t in project['tasks']}['exact'],True)
  self.assertFalse({t['id']:t['stale'] for t in project['tasks']}['young'])
  self.assertFalse({t['id']:t['stale'] for t in project['tasks']}['updated'])
  statuses={t['status'] for t in project['tasks']}
  self.assertIn('triage',statuses);self.assertIn('scheduled',statuses);self.assertIn('ready',statuses);self.assertIn('review',statuses)
  self.assertEqual((project['completed_count_observed'],project['completed_count_7d_observed']),(2,1))
  self.assertNotIn('archived',[p['project']['id'] for p in report['projects']]);self.assertNotIn('paused',[p['project']['id'] for p in report['projects']])
  self.assertEqual(report['scope']['governed_roles'],['lnx']);self.assertNotIn('default',report['scope']['unclassified_roles'])

 def test_health_unknown_for_empty_failure_and_unrecognized(self):
  import unittest.mock as mock, subprocess
  base={('hermes','profile','list'):'',('hermes','kanban','boards','list','--json','--all'):'[]',('hermes','cron','list'):'',('hermes','gateway','status'):'connected to socket'}
  def fake(args,**kwargs):
   key=tuple(args)
   if key==('hermes','gateway','status'):return subprocess.CompletedProcess(args,1,'running','')
   return subprocess.CompletedProcess(args,0,base.get(key,''),'')
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   self.assertEqual(run_readonly_live()['status'],{'scheduler':'unknown','gateway':'unknown','heartbeat':'unknown','workers':'unknown'})

 def test_repo_mapping_readonly_and_no_metadata_leakage(self):
  import unittest.mock as mock, subprocess
  from automation.monitoring import collector
  from unittest.mock import patch
  root=Path(__file__).parents[1]; policy_path=root/'automation/monitoring/policy.json'; policy=json.loads(policy_path.read_text())
  original_policy=json.loads(json.dumps(policy));policy['repos']={'base':'owner/repo'}
  payload=json.dumps([{'number':123,'state':'OPEN','title':'SECRET TITLE','url':'PRIVATE URL','statusCheckRollup':[{'conclusion':'FAILURE','name':'Lint'}]}])
  seen=[]
  def fake(args,**kwargs):
   seen.append(tuple(args))
   if args[0]=='gh':return subprocess.CompletedProcess(args,0,payload,'')
   if tuple(args)==('hermes','kanban','boards','list','--json','--all'):return subprocess.CompletedProcess(args,0,'[]','')
   return subprocess.CompletedProcess(args,0,'','')
  try:
   policy_path.write_text(json.dumps(policy))
   with patch.object(collector.subprocess,'run',side_effect=fake):snap=run_readonly_live()
   self.assertIn(('gh','pr','list','--repo','owner/repo','--state','open','--limit','1000','--json','number,state,statusCheckRollup'),seen)
   self.assertEqual(snap['repos']['base']['open'],1);self.assertEqual(snap['repos']['base']['checks_failing'],1)
   runtime={'repos':{'base':snap['repos']['base'],'unmapped':{'title':'SECRET TITLE'}},'repo_status':snap['repo_status'],'repo_collection':snap['repo_collection'],'repo_failures':snap['repo_failures']}
   report=discover({'projects':[{'id':'base'}],'profiles':[],'metrics':[]},runtime)
   self.assertEqual(report['repo_status']['base'],'available')
   events=collector.stable_events(report)
   self.assertTrue(any(e.get('kind')=='repo_checks' and e['project']=='base' for e in events))
   self.assertTrue(any(e.get('kind')=='repo_check' and e['check_name']=='Lint' and e['pr_number']==123 for e in events))
   encoded=json.dumps(report);self.assertIn('base',report['repos']);self.assertNotIn('unmapped',report['repos']);self.assertNotIn('SECRET TITLE',encoded);self.assertNotIn('PRIVATE URL',json.dumps(snap))
   payload=json.dumps([{'state':'OPEN','statusCheckRollup':[]} for _ in range(1000)])
   with patch.object(collector.subprocess,'run',side_effect=fake):partial=run_readonly_live()
   partial_report=discover({'projects':[{'id':'base'}],'profiles':[],'metrics':[]},partial)
   self.assertEqual(partial_report['repo_status']['base'],'partial')
   self.assertTrue(partial_report['repo_collection']['base']['cap_reached'])
   self.assertEqual(partial_report['repo_collection']['base']['rows_observed'],1000)
   self.assertFalse(any(e.get('kind')=='repo_checks' for e in collector.stable_events(partial_report)))
   partial['repos']['base']['checks_failing']=1
   observed_partial=discover({'projects':[{'id':'base'}],'profiles':[],'metrics':[]},partial)
   self.assertTrue(any(e.get('kind')=='repo_checks' for e in collector.stable_events(observed_partial)))
   seen.clear()
   policy['repos']={};policy_path.write_text(json.dumps(policy))
   with patch.object(collector.subprocess,'run',side_effect=fake):unmapped=run_readonly_live()
   self.assertFalse(any(x[0]=='gh' for x in seen));self.assertEqual(unmapped['repos'],{})
  finally:policy_path.write_text(json.dumps(original_policy,indent=2)+'\n')

if __name__=='__main__': unittest.main()
