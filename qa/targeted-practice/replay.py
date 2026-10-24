"""Dry-run default. Execute only reviewed scripts in a fresh GitHub-hosted VM."""
import argparse,datetime,hashlib,json,os,platform,re,subprocess
from pathlib import Path

BLOCKED=('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','COMPOSE_FILE','KUBECONFIG','PEAKY_AUTHOR_PROBE_SSH')
BRANCH='refs/heads/codex/devops-quality-20261023'
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def call(args,cwd):return subprocess.check_output(args,cwd=cwd,text=True,timeout=30).strip()
def gate(root,mode,confirm,env=None,system=None):
 env=os.environ if env is None else env
 if confirm!='targeted-'+mode:raise ValueError('matching explicit confirmation required')
 if (system or platform.system())!='Linux' or env.get('GITHUB_ACTIONS')!='true' or env.get('RUNNER_ENVIRONMENT')!='github-hosted':raise ValueError('hosted disposable Linux only')
 if env.get('GITHUB_REF')!=BRANCH:raise ValueError('quality branch only')
 if any(env.get(k) for k in BLOCKED):raise ValueError('overridden external target refused')
 if Path(root).resolve()!=Path(env['GITHUB_WORKSPACE']).resolve():raise ValueError('exact disposable workspace required')
 if any((Path(root)/p).exists() for p in ['.runtime','evidence','backups']):raise ValueError('existing runtime/evidence refused; use a new VM, no automatic reset')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def execute(mode,root,report,manifest):
 root=Path(root);report=Path(report);script=manifest[mode]['script']
 if digest(root/script)!=manifest[mode]['sha256']:raise ValueError('reviewed QA script hash mismatch')
 if call(['git','rev-parse','HEAD'],root)!=os.environ['GITHUB_SHA']:raise ValueError('checkout SHA mismatch')
 if call(['git','status','--porcelain'],root):raise ValueError('dirty initial checkout refused')
 if call(['docker','context','show'],root)!='default':raise ValueError('nondefault Docker context')
 context=json.loads(call(['docker','context','inspect','default'],root))[0]
 if context['Endpoints']['docker']['Host']!='unix:///var/run/docker.sock':raise ValueError('nonlocal Docker endpoint')
 if call(['docker','container','ls','-aq'],root) or call(['docker','volume','ls','-q'],root):raise ValueError('preexisting Docker objects refused; never cleanup unknown objects')
 temp=Path(os.environ['RUNNER_TEMP']).resolve();resolved=report.resolve()
 if resolved==temp or temp not in resolved.parents:raise ValueError('report must be fresh RUNNER_TEMP child')
 report.mkdir(mode=0o700)
 summary=dict(mode=mode,commit=os.environ['GITHUB_SHA'],script=script,script_sha256=manifest[mode]['sha256'],started=utc(),executed=False,scope='isolated author acceptance; not learner completion')
 def save():
  (report/'summary.json').write_text(json.dumps(summary,indent=2),'utf-8')
 save()
 # Tools bootstrap is the existing reviewed course entrypoint, after local-target gate.
 with (report/'private-tools.log').open('x') as output:
  tools=subprocess.run(['bash','-c','chmod +x lab scripts/*.sh && ./lab tools'],cwd=root,stdout=output,stderr=subprocess.STDOUT,timeout=1200)
 if tools.returncode:
  summary.update(phase='tools',returncode=tools.returncode,ended=utc());save();return tools.returncode
 env=dict(os.environ)
 if mode=='ansible':env['QA_SCOPE']='ansible'
 else:env.pop('QA_SCOPE',None)
 raw=report/'private-run.log'
 with raw.open('x') as output:
  result=subprocess.run(['bash',script],cwd=root,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=2100)
 summary.update(executed=True,returncode=result.returncode,ended=utc())
 data=raw.read_text('utf-8',errors='replace')
 allowed=[l for l in data.splitlines() if re.match(r'^(?:=== |CONFIGURATION PASS:|LOAD PASS:|node_[ab]\s*:.*ok=\d+.*changed=\d+.*failed=\d+)',l)]
 (report/'sanitized.log').write_text('\n'.join(allowed)+'\n','utf-8')
 if result.returncode==0 and mode=='ansible':
  if 'CONFIGURATION PASS:' not in data:raise ValueError('missing actual configuration acceptance marker')
  summary['vault_encrypted_header']=(root/'.runtime/vault.yml').read_bytes().startswith(b'$ANSIBLE_VAULT;')
  metadata={}
  for node in ['dva-node-a','dva-node-b']:
   metadata[node]=call(['docker','exec',node,'stat','-c','%U %G %a','/etc/dva/token'],root)
  summary['token_metadata']=metadata
  if not summary['vault_encrypted_header'] or any(v!='root root 600' for v in metadata.values()):raise ValueError('Vault metadata failed')
 if result.returncode==0 and mode=='load':
  if 'LOAD PASS:' not in data:raise ValueError('missing actual causal load acceptance marker')
  baseline=root/'evidence/dataset-before.json';before=json.loads(baseline.read_text());summary['dataset_rows']=len(before)
  snapshots=[root/f'evidence/dataset-{phase}{n}.json' for phase in ['a','b'] for n in [1,2,3]]
  summary['six_dataset_hashes']=[digest(p) for p in snapshots]
  summary['baseline_dataset_sha256']=digest(baseline)
  summary['full_rows_preserved']=all(p.read_bytes()==baseline.read_bytes() for p in snapshots)
  summary['measurement_csv_sha256']={p.name:digest(p) for p in (root/'evidence').glob('read-*_stats.csv')}
  summary['returned_http_report_sha256']=digest(root/'evidence/load-returned-http.json')
  if len(before)<=20 or not summary['full_rows_preserved'] or len(summary['measurement_csv_sha256'])!=6:raise ValueError('causal series evidence incomplete')
 save();return result.returncode
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['ansible','load']);p.add_argument('--root',required=True);p.add_argument('--report',required=True);p.add_argument('--execute',action='store_true');p.add_argument('--confirm');a=p.parse_args(argv)
 manifest=json.loads(Path(__file__).with_name('script-manifest.json').read_text('utf-8'))
 if not a.execute:print(json.dumps(dict(dry_run=True,mode=a.mode,reviewed_script=manifest[a.mode],private_files_never_uploaded=['.runtime/','backups/','private-run.log','private-tools.log'])));return 0
 gate(a.root,a.mode,a.confirm)
 return execute(a.mode,a.root,a.report,manifest)
if __name__=='__main__':raise SystemExit(main())
