"""Export an explicit metadata allowlist, never raw route output or runtime files."""
import argparse,hashlib,json,re
from pathlib import Path
from routing_observer import phase_meta
ALLOWED={'events.jsonl','rules.jsonl','hook-phases.jsonl','metadata.json','summary.json'}
MAX_EXPORT_BYTES=24*1024*1024
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--exit-code',type=int,required=True);a=p.parse_args()
 root=Path(a.root).resolve();folder=root/'evidence/passive-routing'
 if (root/'evidence').is_symlink() or folder.is_symlink():raise RuntimeError('Artifact directory symlink rejected')
 folder.mkdir(parents=True,exist_ok=True)
 entries=[];total=0
 for run in folder.iterdir():
  if not run.is_dir() or not re.fullmatch('[0-9a-f]{32}',run.name) or run.is_symlink():raise RuntimeError('Unexpected artifact entry')
  for f in run.iterdir():
   if f.is_symlink() or not f.is_file() or f.name not in ALLOWED:raise RuntimeError('Artifact allowlist violation')
   total+=f.stat().st_size
   if total>MAX_EXPORT_BYTES:raise RuntimeError('Artifact size limit')
   entries.append({'file':str(f.relative_to(folder)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
 phases=[];log_bytes=0;truncated=False
 log=root/'course-route.log'
 if log.exists():
  with log.open(encoding='utf-8',errors='replace') as f:
   for line in f:
    log_bytes+=len(line)
    if log_bytes>10*1024*1024 or len(phases)>=200:truncated=True;break
    value=phase_meta(line)
    if value:phases.append(value)
 summary={'route_exit_code':a.exit_code,'route_pass':a.exit_code==0,'observer_files':entries,'sanitized_route_phases':phases,'phase_extract_truncated':truncated,
 'observer_scope':'metadata only; raw course-route.log/.runtime/backups/env not uploaded',
 'root_cause_status':'NOT PROVEN; successes are non-reproduction, not a fix',
 'observer_status':'NOT ENABLED OR CAPABILITY MISSING' if not entries else 'Review per-run summary and dropped counters; no automatic acceptance inference'}
 encoded=(json.dumps(summary,indent=2)+'\n').encode('utf-8')
 if total+len(encoded)>MAX_EXPORT_BYTES:raise RuntimeError('Artifact size limit including route summary')
 with (folder/'route-summary.json').open('xb') as output:output.write(encoded)
if __name__=='__main__':main()
