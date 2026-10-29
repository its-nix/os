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
   ('hermes','kanban','boards','list','--json'):json.dumps([{'slug':'new-board','name':'Board','db_path':'/SECRET'}]),
   ('hermes','kanban','--board','new-board','list','--json'):json.dumps([{'id':'t1','status':'running','body':'PRIVATE','workspace':'/SECRET','session_id':'SECRET','error':'PRIVATE'}]),
   ('hermes','cron','list'):'scheduled jobs',('hermes','gateway','status'):'running'}
  calls=[]
  def fake(args,**kwargs):
   calls.append(tuple(args));return subprocess.CompletedProcess(args,0,responses.get(tuple(args),''),'')
  with mock.patch('automation.monitoring.collector.subprocess.run',side_effect=fake):
   snap=run_readonly_live()
  self.assertEqual(calls.count(('hermes','profile','list')),1)
  self.assertIn({'name':'default','kind':'unknown'},snap['profiles'])
  self.assertIn({'name':'extra-profile','kind':'unknown'},snap['profiles'])
  self.assertEqual(snap['tasks'],[{'id':'t1','status':'running','board_id':'new-board'}])
  encoded=json.dumps(snap)
  for x in ('PRIVATE','SECRET','/SECRET','db_path','workspace','session_id','error'):self.assertNotIn(x,encoded)

if __name__=='__main__': unittest.main()
