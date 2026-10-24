"""Offline only: parser/sanitizer/command construction. No lab subprocesses."""
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('passive',ROOT/'qa/routing_observer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def observer_fixture(self,d):
  o=m.Observer(ROOT);o.nodes=['dva-course-control-plane'];o.node_pids={o.nodes[0]:123};o.services=[];o.folder=Path(d);o.sink=m.Sink(Path(d))
  o.capture_counters={o.nodes[0]+'-syn':{'captured':0,'received by filter':0,'dropped by kernel':0}}
  return o
 def test_finish_clean_complete_evidence_collected(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'query',side_effect=['1','cpu=0 found=0']):
   o=self.observer_fixture(d);o.finish();self.assertTrue(json.loads((Path(d)/'summary.json').read_text())['status'].startswith('COLLECTED'))
 def test_finish_missing_or_failed_evidence_inconclusive(self):
  for failure in ['leftover','thread','final-query','empty-stats','capture-counters']:
   with self.subTest(failure=failure),tempfile.TemporaryDirectory() as d:
    o=self.observer_fixture(d)
    if failure=='leftover':o.processes=[Mock(pid=123,poll=Mock(return_value=None))]
    if failure=='thread':o.threads=[Mock(is_alive=Mock(return_value=True))]
    if failure=='capture-counters':o.capture_counters={}
    responses=RuntimeError('unavailable') if failure=='final-query' else ['1','' if failure=='empty-stats' else 'cpu=0 found=0']
    with patch.object(m,'query',side_effect=responses):o.finish()
    report=json.loads((Path(d)/'summary.json').read_text());self.assertEqual(report['status'],'INCONCLUSIVE');self.assertTrue(report['errors'])
 def test_baseline_capability_errors_propagate(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'query',side_effect=RuntimeError('unavailable')):
   o=self.observer_fixture(d);o.baseline();self.assertEqual(len(o.errors),7);o.finish();self.assertEqual(json.loads((Path(d)/'summary.json').read_text())['status'],'INCONCLUSIVE')
 def test_export_counts_summary_bytes_before_write(self):
  with patch.object(sys,'path',[str(ROOT/'qa'),*sys.path]):
   spec=importlib.util.spec_from_file_location('bounded_exporter',ROOT/'qa/export_passive_report.py');ex=importlib.util.module_from_spec(spec);spec.loader.exec_module(ex)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);run=root/'evidence/passive-routing'/('a'*32);run.mkdir(parents=True);(run/'events.jsonl').write_bytes(b'x'*128)
   with patch.object(ex,'MAX_EXPORT_BYTES',128),patch.object(sys,'argv',['export','--root',d,'--exit-code','0']):
    with self.assertRaisesRegex(RuntimeError,'including route summary'):ex.main()
   self.assertFalse((run.parent/'route-summary.json').exists())
 def test_pod_excludes_credentials(self):
  x={'metadata':{'uid':'u','name':'ticket-api-check','namespace':'dev','annotations':{'token':'SECRET'}},'spec':{'nodeName':'n','containers':[{'env':[{'value':'SECRET'}]}]},'status':{'podIP':'10.244.0.2','phase':'Running'}}
  data=m.pod_meta(x);self.assertEqual(data['uid'],'u');self.assertNotIn('SECRET',json.dumps(data));self.assertNotIn('spec',data)
 def test_endpoint_uid_conditions(self):
  x={'metadata':{'uid':'slice','resourceVersion':'2'},'endpoints':[{'addresses':['10.244.0.3'],'conditions':{'ready':False,'terminating':True,'secret':'SECRET'},'targetRef':{'uid':'pod'}}]}
  v=m.endpoint_meta(x);self.assertEqual(v['endpoints'][0]['target']['uid'],'pod');self.assertTrue(v['endpoints'][0]['conditions']['terminating']);self.assertNotIn('SECRET',json.dumps(v))
 def test_conntrack_original_reply_and_direct_ip(self):
  line='[NEW] tcp 6 120 SYN_SENT src=10.244.0.45 dst=10.96.28.130 sport=45896 dport=8000 [UNREPLIED] src=10.244.0.33 dst=10.244.0.45 sport=8000 dport=45896'
  v=m.conntrack_meta(line,{'10.96.28.130'});self.assertEqual(v['reply']['src'],'10.244.0.33');self.assertTrue(v['unreplied']);self.assertEqual(v['event'],'NEW')
  self.assertIsNone(m.conntrack_meta(line.replace('dst=10.96.28.130','dst=10.244.0.33'),{'10.96.28.130'}))
 def test_syn_metadata_only(self):
  line='123.456 eth0 Out IP 10.244.0.45.45896 > 10.96.28.130.8000: Flags [S], seq 1234, win 64240, length 0 SECRET'
  v=m.syn_meta(line);self.assertEqual(v['seq'],1234);self.assertEqual(v['sport'],45896);self.assertNotIn('SECRET',json.dumps(v))
  self.assertIsNone(m.syn_meta(line.replace('Flags [S]','Flags [P.]')))
 def test_nat_graph_only_assigned_services(self):
  rules='-A KUBE-SERVICES -d 10.96.0.1/32 -p tcp --dport 8000 -j KUBE-SVC-AA\n-A KUBE-SERVICES -d 10.96.0.2/32 -p tcp --dport 8000 -j KUBE-SVC-BB\n-A KUBE-SVC-AA -j KUBE-SEP-CC\n-A KUBE-SEP-CC -j DNAT --to-destination 10.244.0.3:8000\n-A KUBE-SVC-BB -j KUBE-SEP-DD\n'
  got=m.scoped_nat(rules,{'10.96.0.1'});self.assertEqual(len(got),3);self.assertNotIn('KUBE-SVC-BB','\n'.join(got))
 def test_chunked_json_and_bound(self):
  d=m.JsonObjects();self.assertEqual(d.feed('{"type":'),[]);self.assertEqual(d.feed('"ADDED"}\n{"type":"DELETED"}'),[{'type':'ADDED'},{'type':'DELETED'}])
  with self.assertRaises(ValueError):d.feed('x'*(m.MAX_OBJECT+1))
 def test_phase_does_not_export_extra_text(self):
  v=m.phase_meta('2026-10-24T01:13:37Z stage=create event=committed_ticket id=8 password=SECRET')
  self.assertEqual(v['id'],8);self.assertNotIn('SECRET',json.dumps(v));self.assertIsNone(m.phase_meta('Authorization: SECRET'))
 def test_sink_rows_bound(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'MAX_ROWS',1):
   s=m.Sink(Path(d));s.emit('events.jsonl',{'kind':'a'});s.emit('events.jsonl',{'kind':'b'});self.assertTrue(s.truncated);self.assertEqual(s.rows,1)
   with self.assertRaises(ValueError):s.emit('../env',{})
 def test_host_gate_denies_before_query(self):
  with patch.dict(m.os.environ,{},clear=True),patch.object(m,'query',side_effect=AssertionError('must not run')):
   with self.assertRaises(RuntimeError):m.Observer(ROOT).gate()
 def test_stream_command_allowlist(self):
  with tempfile.TemporaryDirectory() as d:
   o=m.Observer(ROOT);o.nodes=['dva-course-control-plane'];o.node_pids={'dva-course-control-plane':123};o.services=[];o.vips={'10.96.0.1','10.96.0.2'};o.folder=Path(d);o.sink=m.Sink(Path(d));calls=[]
   o.gate=lambda:None;o.baseline=lambda:None;o.stream=lambda name,args,parse,*pos,**kw:calls.append((name,args,parse))
   o.start()
   self.assertEqual(sum('endpointslices' in args for _,args,_ in calls),2)
   packet=next(args for name,args,_ in calls if name.endswith('-syn'))
   self.assertIn('-p',packet);self.assertNotIn('-w',packet);self.assertNotIn('-A',packet);self.assertNotIn('-X',packet)
   self.assertIn('ip[2:2] =',packet[-1]);self.assertIn('--net',packet);self.assertIn('600s',packet)
   watches=[args for _,args,_ in calls if '--watch' in args];self.assertTrue(all('--request-timeout=605s' in a for a in watches))
   callback=next(parse for name,_,parse in calls if name=='dev-pods')
   callback({'type':'ADDED','object':{'metadata':{'name':'ticket-api-check','uid':'hook-uid'},'spec':{'nodeName':'dva-course-control-plane'},'status':{'containerStatuses':[{'containerID':'containerd://'+'a'*64,'state':{'running':{}}}]}}})
   hook=next(args for name,args,_ in calls if name=='dev-hook-hook-uid');self.assertEqual(hook[:3],['docker','exec','dva-course-control-plane']);self.assertIn('--timestamps',hook);self.assertEqual(hook[-1],'a'*64)
   self.assertEqual(hook[3:7],['timeout','--signal=TERM','--kill-after=2s','600s'])
 def test_route_changes_are_only_observer_overlay(self):
  import hashlib
  route=(ROOT/'qa/route.sh').read_text(encoding='utf-8');manifest=json.loads((ROOT/'qa/passive-base-manifest.json').read_text())
  base=route
  for text in manifest['qa_route_insertions']:
   self.assertEqual(base.count(text),1);base=base.replace(text,'',1)
  canonical=base.replace('\r\n','\n').rstrip('\n')+'\n'
  self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(),manifest['qa_route_base_canonical_sha256'])
  self.assertLess(route.index('python3 qa/routing_observer.py'),route.index('./lab deploy --artifact'))
  self.assertEqual(route.count('./lab deploy --artifact'),base.count('./lab deploy --artifact'))
  self.assertEqual(route.count('./lab http '),base.count('./lab http '))
 def test_export_no_raw_logs_and_no_replacement(self):
  with patch.object(sys,'path',[str(ROOT/'qa'),*sys.path]):
   spec=importlib.util.spec_from_file_location('exporter',ROOT/'qa/export_passive_report.py');exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'course-route.log').write_text('Authorization SECRET\nstage=read event=connect attempt=1 password=SECRET\n')
   with patch.object(sys,'argv',['export','--root',d,'--exit-code','7']):exporter.main()
   report=(root/'evidence/passive-routing/route-summary.json').read_text();self.assertNotIn('SECRET',report);self.assertFalse(json.loads(report)['route_pass'])
   with patch.object(sys,'argv',['export','--root',d,'--exit-code','0']):
    with self.assertRaises(RuntimeError):exporter.main()
 def test_workflow_upload_is_allowlisted_and_export_gated(self):
  text=(ROOT/'.github/workflows/routing-passive-experiment.yml').read_text()
  self.assertIn("steps.export_metadata.outcome == 'success'",text)
  path=text.split('          path: |',1)[1].split('          if-no-files-found:',1)[0]
  self.assertNotIn('course-route.log',path);self.assertNotIn('backups',path);self.assertNotIn('.runtime',path)
  self.assertIn('retention-days: 3',text);self.assertIn('arm: [control, passive]',text)
  self.assertIn('fetch-depth: 0',text);self.assertIn('persist-credentials: false',text)
if __name__=='__main__':unittest.main()
