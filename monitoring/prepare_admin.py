import getpass
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from guard import guard, k
guard()
password = getpass.getpass('New training Grafana admin password (16+ characters): ')
if len(password) < 16:
    raise SystemExit('Choose a longer new password')
value = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'grafana-admin', 'namespace': 'monitoring'},
         'type': 'Opaque', 'stringData': {'password': password}}
k('create', '-f', '-', input=json.dumps(value), text=True)
