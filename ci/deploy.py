# Acceptance and safety assertions must never be disabled.
if not __debug__:
    raise RuntimeError('Optimized Python is unsupported; unset PYTHONOPTIMIZE and do not use -O')

import argparse,hashlib,json,re,sys,uuid
import contextlib,subprocess,threading
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from guard import guard,k,run
ROOT=Path(__file__).resolve().parents[1]

def network_snapshot(folder, phase, ns):
 commands={
  'service':['kubectl','--context','kind-dva-course','--request-timeout=5s','-n',ns,'get','service','ticket-api','-o','json'],
  'endpoints':['kubectl','--context','kind-dva-course','--request-timeout=5s','-n',ns,'get','endpointslices','-l','kubernetes.io/service-name=ticket-api','-o','json'],
  'proxy-log':['kubectl','--context','kind-dva-course','--request-timeout=5s','-n','kube-system','logs','-l','k8s-app=kube-proxy','--since=3m','--tail=150','--timestamps','--prefix'],
  'iptables-nat':['docker','exec','dva-course-control-plane','iptables-save','-t','nat'],
  'nft-rules':['docker','exec','dva-course-control-plane','nft','list','ruleset'],
  'ipvs':['docker','exec','dva-course-control-plane','ipvsadm','-Ln'],
  'conntrack':['docker','exec','dva-course-control-plane','conntrack','-L','-p','tcp','--dport','8000'],
 }
 for name,command in commands.items():
  with (folder/(phase+'-'+name+'.txt')).open('w') as output:
   output.write('captured_utc='+datetime.now(timezone.utc).isoformat()+'\n');output.flush()
   try:
    result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,timeout=6,check=False)
    output.write('\nexit_code='+str(result.returncode)+'\n')
   except (OSError,subprocess.TimeoutExpired) as error:output.write('\ncapture_error='+type(error).__name__+'\n')

@contextlib.contextmanager
def network_watch(ns):
 # Called only after guard() has bound the isolated single-node training cluster.
 folder=ROOT/'evidence'/('network-'+ns+'-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
 processes=[];threads=[]
 watches={
  'endpoints':['endpointslices','-l','kubernetes.io/service-name=ticket-api','--watch','--output-watch-events','-o','json'],
  'pods':['pods','-l','app=ticket-api','--watch','-o','custom-columns=NAME:.metadata.name,IP:.status.podIP,READY:.status.containerStatuses[*].ready,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,REVISION:.metadata.labels.pod-template-hash'],
 }
 def record(process,path):
  with path.open('w') as output:
   for index,line in enumerate(process.stdout):
    if index>=5000:output.write('capture_truncated\n');break
    output.write(datetime.now(timezone.utc).isoformat()+' '+line);output.flush()
 try:
  for name,args in watches.items():
   try:
    process=subprocess.Popen(['kubectl','--context','kind-dva-course','--request-timeout=260s','-n',ns,'get',*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    processes.append(process)
    thread=threading.Thread(target=record,args=(process,folder/(name+'-watch.txt')),daemon=True);thread.start();threads.append(thread)
   except OSError as error:(folder/(name+'-watch-error.txt')).write_text(type(error).__name__)
  network_snapshot(folder,'before-upgrade',ns)
  yield
 finally:
  # Capture failed state without changing the original Helm outcome.
  network_snapshot(folder,'after-test',ns)
  for process in processes:
   try:
    if process.poll() is None:
     process.terminate()
     try:process.wait(timeout=2)
     except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
   except (OSError,subprocess.TimeoutExpired):pass
  for thread in threads:thread.join(timeout=2)
def main():
 p=argparse.ArgumentParser();p.add_argument('--artifact',type=Path,required=True);p.add_argument('--commit',required=True);a=p.parse_args()
 guard();assert re.fullmatch('[0-9a-f]{40}',a.commit)
 m=json.loads((a.artifact/'manifest.json').read_text());assert m['commit']==a.commit and m['http_check_passed'] is True
 h=hashlib.sha256()
 with (a.artifact/'image.tar').open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 assert h.hexdigest()==m['tar_sha256'],'Image checksum mismatch'
 run('docker','load','-i',str(a.artifact/'image.tar'))
 info=json.loads(run('docker','image','inspect',m['tag'],capture_output=True,text=True).stdout)[0]
 assert info['Id']==m['image_id'];assert info['Config']['Labels']['org.opencontainers.image.revision']==a.commit
 arch=run('docker','info','--format','{{.Architecture}}',capture_output=True,text=True).stdout.strip()
 assert info['Architecture']=={'x86_64':'amd64','aarch64':'arm64'}.get(arch,arch)
 tag='localhost:5001/dva-ticket:ci-'+a.commit
 run('docker','tag',m['tag'],tag);run('docker','push',tag)
 obj=json.loads(run('docker','image','inspect',tag,capture_output=True,text=True).stdout)[0]
 refs=[r for r in obj['RepoDigests'] if r.startswith('localhost:5001/dva-ticket@sha256:')];assert len(refs)==1;ref=refs[0]
 node=run('kind','get','nodes','--name','dva-course',capture_output=True,text=True).stdout.split()[0]
 run('docker','exec',node,'crictl','pull',ref)
 for ns in ['dev','stage']:
  with network_watch(ns):
   run('helm','--kube-context','kind-dva-course','upgrade','ticket-api',str(ROOT/'chart'),'-n',ns,'-f',str(ROOT/'chart'/('values-'+ns+'.yaml')),'--set-string','image.ref='+ref,'--set-string','releaseLabel=ci-'+a.commit,'--atomic','--wait','--timeout','180s')
   run('helm','--kube-context','kind-dva-course','test','ticket-api','-n',ns,'--timeout','60s','--logs')
  run('python3',str(ROOT/'ops/http_check.py'),'--base','http://127.0.0.1:'+('18220' if ns=='dev' else '18230'),'--out','evidence/deploy-'+ns+'-'+uuid.uuid4().hex[:8]+'.json')
 (ROOT/'evidence/deployed-image.json').write_text(json.dumps({'commit':a.commit,'image_ref':ref,'source':m},indent=2))
 print('One checked registry digest passed dev then stage: '+ref)
if __name__=='__main__':main()
