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

 def test_adversarial_runtime_is_allowlisted(self):
  root=Path(__file__).parent/'fixtures'/'monitoring'
  m=json.loads((root/'manifest.json').read_text()); r=json.loads((root/'runtime.json').read_text())
  r['tasks']=[{'id':'safe-task','project_id':'base','board_id':'base-board','status':'blocked','body':'SECRET BODY','path':'/private/path','session_id':'SESSION','error':'TRACE SECRET','created_at':'2020-01-01T00:00:00Z'}]
  r['profiles'].append({'name':'private-profile','kind':'worker','path':'/private','session_id':'SESSION'})
  r['status']={'gateway':'healthy','scheduler':'TOKEN_SECRET','workers':'${HOME}/private','error':'secret'}
  out=json.dumps(discover(m,r,now=__import__('datetime').datetime(2026,1,1,tzinfo=__import__('datetime').timezone.utc)))
  for secret in ('SECRET BODY','/private/path','SESSION','TRACE SECRET','TOKEN_SECRET','${HOME}/private','secret'):
   self.assertNotIn(secret,out)
  self.assertIn('age_hours',out); self.assertIn('blocked_count',out)

 def test_live_adapter_exact_readonly_commands_and_shape(self):
  import unittest.mock as mock
  import subprocess
  responses={
   ('hermes','profile','list'):'Profile Model Gateway Alias Distribution\n───────────────\n◆default gpt-6-luna running — —\nextra-profile gpt-6-luna running — —\n',
   ('hermes','kanban','boards','list','--json','--all'):json.dumps([{'slug':'new-board','name':'Board','db_path':'/SECRET'}]),
   ('hermes','kanban','--board','new-board','list','--json'):json.dumps([{'id':'t1','status':'running','body':'PRIVATE','workspace':'/SECRET','session_id':'SECRET','error':'PRIVATE'}]),
   ('hermes','cron','status'):'Scheduler host: host (PID 1)\nTicker heartbeat: 31s ago',('hermes','gateway','status'):'✓ User gateway service is running'}
  calls=[]
  def fake(args,**kwargs):
   calls.append(tuple(args));return subprocess.CompletedProcess(args,0,responses.get(tuple(args),''),'')
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   snap=run_readonly_live()
  self.assertEqual(calls.count(('hermes','profile','list')),1)
  self.assertIn({'name':'default','kind':'unknown'},snap['profiles'])
  self.assertIn({'name':'extra-profile','kind':'unknown'},snap['profiles'])
  self.assertEqual(snap['tasks'],[{'id':'t1','status':'running','board_id':'new-board'}])
  self.assertEqual(snap['status'],{'scheduler':'available','gateway':'available','heartbeat':'unknown','workers':'unknown'})
  encoded=json.dumps(snap)
  for x in ('PRIVATE','SECRET','/SECRET','db_path','workspace','session_id','error'):self.assertNotIn(x,encoded)

 def test_epoch_stale_boundary_states_period_and_archive_pause(self):
  from datetime import datetime,timezone
  m={'projects':[{'id':'p','board':'b'},{'id':'archived','board':'ab','archived':True},{'id':'paused','board':'pb','paused':True}], 'profiles':[{'id':'lnx','hermes_profile':'default','kind':'orchestrator'}], 'metrics':[]}
  now=datetime(2026,1,10,tzinfo=timezone.utc); base=int(now.timestamp())
  r={'boards':[{'id':'b'},{'id':'ab','archived':True},{'id':'pb','paused':True}], 'profiles':[{'name':'default'}], 'tasks':[
   {'id':'exact','board_id':'b','status':'triage','created_at':base-72*3600},
   {'id':'young','board_id':'b','status':'scheduled','created_at':base-72*3600+1},
   {'id':'ready','board_id':'b','status':'ready','created_at':base-1},
   {'id':'review','board_id':'b','status':'review','created_at':base-80*3600},
   {'id':'freshdone','board_id':'b','status':'completed','created_at':base-3600,'completed_at':now.isoformat()},
   {'id':'olddone','board_id':'b','status':'done','completed_at':int(base-8*86400)},
   {'id':'notincluded','board_id':'ab','status':'open'}, {'id':'notincluded2','board_id':'pb','status':'open'}], 'status':{}}
  from automation.monitoring.collector import stable_events
  report=discover(m,r,now); project=report['projects'][0]
  later=discover(m,r,datetime(2026,1,10,1,tzinfo=timezone.utc))
  self.assertEqual([x['key'] for x in stable_events(report)],[x['key'] for x in stable_events(later)])
  self.assertEqual({t['id']:t['stale'] for t in project['tasks']}['exact'],True)
  self.assertFalse({t['id']:t['stale'] for t in project['tasks']}['young'])
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
  payload=json.dumps([{'state':'OPEN','title':'SECRET TITLE','url':'PRIVATE URL','statusCheckRollup':[{'conclusion':'FAILURE','name':'SECRET CHECK'}]}])
  seen=[]
  def fake(args,**kwargs):
   seen.append(tuple(args))
   if args[0]=='gh':return subprocess.CompletedProcess(args,0,payload,'')
   if tuple(args)==('hermes','kanban','boards','list','--json','--all'):return subprocess.CompletedProcess(args,0,'[]','')
   return subprocess.CompletedProcess(args,0,'','')
  try:
   policy_path.write_text(json.dumps(policy))
   with patch.object(collector.subprocess,'run',side_effect=fake):snap=run_readonly_live()
   self.assertIn(('gh','pr','list','--repo','owner/repo','--state','open','--limit','100','--json','state,statusCheckRollup'),seen)
   self.assertEqual(snap['repos']['base']['open'],1);self.assertEqual(snap['repos']['base']['checks_failing'],1)
   runtime={'repos':{'base':snap['repos']['base'],'unmapped':{'title':'SECRET TITLE'}}}
   report=discover({'projects':[{'id':'base'}],'profiles':[],'metrics':[]},runtime)
   encoded=json.dumps(report);self.assertIn('base',report['repos']);self.assertNotIn('unmapped',report['repos']);self.assertNotIn('SECRET TITLE',encoded);self.assertNotIn('SECRET CHECK',json.dumps(snap))
   seen.clear()
   policy['repos']={};policy_path.write_text(json.dumps(policy))
   with patch.object(collector.subprocess,'run',side_effect=fake):unmapped=run_readonly_live()
   self.assertFalse(any(x[0]=='gh' for x in seen));self.assertEqual(unmapped['repos'],{})
  finally:policy_path.write_text(json.dumps(original_policy,indent=2)+'\n')

if __name__=='__main__': unittest.main()
