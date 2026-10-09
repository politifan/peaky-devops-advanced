import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from guard import ROOT, guard, k
from backup import digest, snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    guard()
    if not re.fullmatch('restore-[0-9]{14}', args.destination):
        parser.error('use a new restore-YYYYMMDDHHMMSS namespace')
    manifest = json.loads((args.folder / 'manifest.json').read_text())
    assert manifest['source_namespace'] in {'dev', 'stage'}
    assert digest(args.folder / 'ticket.dump') == manifest['sha256']
    expected = json.loads((args.folder / 'snapshot.json').read_text())
    started = time.monotonic()
    began = datetime.now(timezone.utc).isoformat()
    ns = args.destination
    # Create fails if a namespace already exists; do not restore over an existing database.
    k('create', '-f', '-', input=json.dumps({'apiVersion': 'v1', 'kind': 'Namespace',
       'metadata': {'name': ns, 'labels': {'course': 'dva'}}}), text=True)
    secrets = {}
    for name in ('dva-db-admin', 'dva-migrate-db', 'dva-app-db'):
        original = json.loads(k('-n', manifest['source_namespace'], 'get', 'secret', name, '-o', 'json', capture_output=True, text=True).stdout)
        secrets[name] = original['data']
        value = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque', 'metadata': {'name': name, 'namespace': ns}, 'data': original['data']}
        k('create', '-f', '-', input=json.dumps(value), text=True)
    k('-n', ns, 'apply', '-f', str(ROOT / 'k8s' / 'db.yaml'))
    k('-n', ns, 'rollout', 'status', 'statefulset/postgres', '--timeout=120s')
    init = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': 'restore-init', 'namespace': ns},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 120, 'template': {'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
            'containers': [{'name': 'init', 'image': manifest['image_ref'], 'command': ['python', 'ops/init_db.py'],
                'env': [{'name': 'ADMIN_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'dva-db-admin', 'key': 'password'}}},
                        {'name': 'APP_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'dva-migrate-db', 'key': 'password'}}}]}]}}}}
    k('create', '-f', '-', input=json.dumps(init), text=True)
    k('-n', ns, 'wait', '--for=condition=complete', 'job/restore-init', '--timeout=150s')
    with (args.folder / 'ticket.dump').open('rb') as source:
        k('-n', ns, 'exec', '-i', 'postgres-0', '--', 'pg_restore', '-U', 'postgres', '-d', 'ticket_lab',
          '--no-owner', '--no-acl', '--role=ticket_app', '--exit-on-error', stdin=source)
    username = base64.b64decode(secrets['dva-app-db']['username']).decode()
    if username == 'ticket_runtime':
        password = base64.b64decode(secrets['dva-app-db']['password']).decode()
        assert re.fullmatch('[A-Za-z0-9]{24,}', password)
        statement = "CREATE ROLE ticket_runtime LOGIN PASSWORD '" + password + "';\n"
        statement += 'GRANT CONNECT ON DATABASE ticket_lab TO ticket_runtime;\nGRANT USAGE ON SCHEMA public TO ticket_runtime;\n'
        statement += 'GRANT SELECT,INSERT,UPDATE ON TABLE tickets TO ticket_runtime;\nGRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO ticket_runtime;\n'
        k('-n', ns, 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'ticket_lab', '-v', 'ON_ERROR_STOP=1',
          input=statement, text=True, capture_output=True)
    elif username != 'ticket_app':
        raise RuntimeError('Unexpected application role')
    restored = snapshot(ns)
    assert restored == expected, 'Restored revision and every id/title/status must match the saved snapshot'
    k('-n', ns, 'apply', '-f', str(ROOT / 'k8s' / 'app.yaml'))
    k('-n', ns, 'set', 'image', 'deployment/ticket-api', 'api=' + manifest['image_ref'])
    k('-n', ns, 'rollout', 'status', 'deployment/ticket-api', '--timeout=120s')
    report = {'started_utc': began, 'finished_utc': datetime.now(timezone.utc).isoformat(),
              'namespace': ns, 'sql_compare_passed': True, 'ticket_count': len(restored['tickets']),
              'seconds_to_sql_and_rollout': round(time.monotonic() - started, 2), 'http_verified': False}
    (args.folder / ('restore-' + ns + '.json')).write_text(json.dumps(report, indent=2))
    print('SQL matches; now port-forward only this restore service and prove the old record via HTTP')
    print('This is not yet a complete RTO/HTTP acceptance result')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Restore stopped: {type(error).__name__}', file=sys.stderr)
        raise SystemExit(1)
