import argparse,hashlib,json,re,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from guard import guard,k,run
ROOT=Path(__file__).resolve().parents[1]
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
  run('helm','--kube-context','kind-dva-course','upgrade','ticket-api',str(ROOT/'chart'),'-n',ns,'-f',str(ROOT/'chart'/('values-'+ns+'.yaml')),'--set-string','image.ref='+ref,'--set-string','releaseLabel=ci-'+a.commit,'--atomic','--wait','--timeout','180s')
  run('helm','--kube-context','kind-dva-course','test','ticket-api','-n',ns,'--timeout','60s')
  run('python3',str(ROOT/'ops/http_check.py'),'--base','http://127.0.0.1:'+('18220' if ns=='dev' else '18230'),'--out','evidence/deploy-'+ns+'-'+uuid.uuid4().hex[:8]+'.json')
 (ROOT/'evidence/deployed-image.json').write_text(json.dumps({'commit':a.commit,'image_ref':ref,'source':m},indent=2))
 print('One checked registry digest passed dev then stage: '+ref)
if __name__=='__main__':main()
