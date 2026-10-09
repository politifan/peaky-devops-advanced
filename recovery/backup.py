import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from guard import ROOT, guard, k

SNAPSHOT = "SELECT json_build_object('revision',(SELECT version_num FROM alembic_version),'tickets',COALESCE((SELECT json_agg(t ORDER BY id) FROM (SELECT id,title,status FROM tickets) t),'[]'::json));"


def snapshot(ns):
    return json.loads(k('-n', ns, 'exec', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'ticket_lab',
        '-At', '-v', 'ON_ERROR_STOP=1', '-c', SNAPSHOT, capture_output=True, text=True).stdout)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--namespace', choices=['dev', 'stage'], required=True)
    parser.add_argument('--other-writers-stopped', action='store_true', required=True)
    args = parser.parse_args()
    guard()
    deployment = json.loads(k('-n', args.namespace, 'get', 'deployment', 'ticket-api', '-o', 'json', capture_output=True, text=True).stdout)
    if deployment['spec']['replicas'] != 0 or deployment.get('status', {}).get('replicas', 0) != 0:
        raise RuntimeError('API must be scaled to zero and fully stopped')
    # Operator confirms Locust, migration jobs and other writers are also stopped.
    folder = ROOT / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    data = snapshot(args.namespace)
    (folder / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    partial = folder / 'ticket.dump.partial'
    with partial.open('xb') as output:
        k('-n', args.namespace, 'exec', 'postgres-0', '--', 'pg_dump', '-U', 'postgres', '-d', 'ticket_lab', '-Fc', stdout=output)
    if partial.stat().st_size == 0:
        raise RuntimeError('Empty backup')
    with partial.open('rb') as source:
        k('-n', args.namespace, 'exec', '-i', 'postgres-0', '--', 'pg_restore', '--list', stdin=source, stdout=sys.stdout)
    final = partial.with_suffix('')
    partial.rename(final)
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'source_namespace': args.namespace,
                'sha256': digest(final), 'bytes': final.stat().st_size, 'ticket_count': len(data['tickets']),
                'revision': data['revision'], 'image_ref': deployment['spec']['template']['spec']['containers'][0]['image'],
                'restore_verified': False, 'writers_stopped_confirmed_by_operator': True}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(folder)
    print('Dump saved outside the cluster working PVC. Restore has not been verified.')


if __name__ == '__main__':
    main()
