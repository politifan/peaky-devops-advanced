"""Fail before the route if reviewed base/runtime/topology drifted."""
from pathlib import Path
import hashlib,json,sys
def canonical(data):return data.decode('utf-8').replace('\r\n','\n').rstrip('\n')+'\n'
def main():
 if sys.flags.optimize:raise RuntimeError('optimized Python unsupported')
 root=Path(__file__).resolve().parents[1]
 manifest=json.loads((root/'qa/passive-base-manifest.json').read_text())
 for item in manifest['unchanged_base_files']:
  actual=hashlib.sha256(canonical((root/item['path']).read_bytes()).encode()).hexdigest()
  if actual!=item['canonical_text_sha256']:raise RuntimeError('Reviewed base drift: '+item['path'])
 actual=hashlib.sha256(canonical((root/'qa/route.sh').read_bytes()).encode()).hexdigest()
 if actual!=manifest['instrumented_route_canonical_sha256']:raise RuntimeError('Unexpected QA route overlay')
 print('Reviewed application/hook/deploy/topology unchanged; QA-only overlay matched')
if __name__=='__main__':main()
