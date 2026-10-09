# Maintenance exercise: change SQL credentials, then namespace-scoped API Secret.
import argparse
import base64
import json
from pathlib import Path
import secrets
import string
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from guard import guard, k


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['create', 'rotate'])
    parser.add_argument('--namespace', choices=['dev', 'stage'], required=True)
    args = parser.parse_args()
    guard()
    deployment = json.loads(k('-n', args.namespace, 'get', 'deployment', 'ticket-api', '-o', 'json', capture_output=True, text=True).stdout)
    if deployment['spec']['replicas'] != 0 or deployment.get('status', {}).get('replicas', 0) != 0:
        raise RuntimeError('Stop all API writers and confirm replicas=0 before this maintenance action')
    password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    role_action = 'CREATE ROLE ticket_runtime LOGIN' if args.mode == 'create' else 'ALTER ROLE ticket_runtime'
    statement = role_action + " PASSWORD '" + password + "';\n"
    statement += "GRANT CONNECT ON DATABASE ticket_lab TO ticket_runtime;\nGRANT USAGE ON SCHEMA public TO ticket_runtime;\n"
    statement += "GRANT SELECT,INSERT,UPDATE ON TABLE tickets TO ticket_runtime;\nGRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO ticket_runtime;\n"
    # Capture errors rather than accidentally displaying a SQL line containing the new password.
    result = k('-n', args.namespace, 'exec', '-i', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'ticket_lab',
               '-v', 'ON_ERROR_STOP=1', input='BEGIN;\n' + statement + 'COMMIT;\n', text=True, capture_output=True)
    existing = json.loads(k('-n', args.namespace, 'get', 'secret', 'dva-app-db', '-o', 'json', capture_output=True, text=True).stdout)
    values = {'username': 'ticket_runtime', 'password': password,
              'DATABASE_URL': f'postgresql+psycopg://ticket_runtime:{password}@postgres:5432/ticket_lab'}
    value = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
             'metadata': {'name': 'dva-app-db', 'namespace': args.namespace, 'resourceVersion': existing['metadata']['resourceVersion']},
             'data': {key: base64.b64encode(text.encode()).decode() for key, text in values.items()}}
    k('replace', '-f', '-', input=json.dumps(value), text=True)
    print('SQL role and API Secret updated. Start API, then verify old data and a new write.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Role change stopped: {type(error).__name__}', file=sys.stderr)
        raise SystemExit(1)
