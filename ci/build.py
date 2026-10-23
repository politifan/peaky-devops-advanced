# Acceptance and safety assertions must never be disabled.
if not __debug__:
    raise RuntimeError('Optimized Python is unsupported; unset PYTHONOPTIMIZE and do not use -O')

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def clean_source(sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise RuntimeError('Expected a full commit SHA')
    if run('git', 'rev-parse', 'HEAD', capture_output=True, text=True, cwd=ROOT).stdout.strip() != sha:
        raise RuntimeError('Commit differs from HEAD')
    # Only inputs read by this build; unrelated lab evidence/infra exercises are allowed.
    inputs = ('ticket-lab', 'ci', 'ops/http_check.py', 'ops/safety.py', 'scripts/build-check.sh')
    status = run('git', 'status', '--porcelain', '--untracked-files=all', '--', *inputs,
                 cwd=ROOT, capture_output=True, text=True).stdout
    if status.strip():
        raise RuntimeError('Commit build inputs before creating a commit-labelled image')


def new_evidence():
    folder = ROOT / 'evidence' / ('build-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    sha = os.environ['GITHUB_SHA']
    clean_source(sha)
    for name in ('requirements.lock', 'requirements-dev.lock'):
        assert (ROOT / 'ticket-lab' / name).is_file(), 'Create and commit Linux/Python3.12 locks first'
    run('python', '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-dev.lock', cwd=ROOT / 'ticket-lab')
    run('python', '-m', 'pip', 'check')
    evidence = new_evidence()
    print('Build artifact directory: ' + str(evidence), flush=True)
    run('python', '-m', 'pytest', '-q', f'--junitxml={evidence / "pytest.xml"}', cwd=ROOT / 'ticket-lab')
    tag = f'dva-ticket:ci-{sha}'
    run('docker', 'build', '-f', str(ROOT / 'ci' / 'Dockerfile.lock'),
        '--build-arg', f'VCS_REF={sha}', '--build-arg', f'APP_VERSION=ci-{sha}',
        '-t', tag, str(ROOT / 'ticket-lab'))
    nonce = uuid.uuid4().hex[:10]
    network, db, api = f'dva-ci-net-{nonce}', f'dva-ci-db-{nonce}', f'dva-ci-api-{nonce}'
    run('docker', 'network', 'create', network)
    try:
        run('docker', 'run', '-d', '--name', db, '--network', network, '--network-alias', 'postgres',
            '-e', 'POSTGRES_PASSWORD=ci-only-password', 'postgres:16-bookworm')
        for attempt in range(45):
            ready = subprocess.run(['docker', 'exec', db, 'pg_isready', '-U', 'postgres'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('CI PostgreSQL not ready')
        run('docker', 'run', '--rm', '--network', network, '-e', 'ADMIN_PASSWORD=ci-only-password',
            '-e', 'APP_PASSWORD=ci-app-only', tag, 'python', 'ops/init_db.py')
        url = 'postgresql+psycopg://ticket_app:ci-app-only@postgres:5432/ticket_lab'
        run('docker', 'run', '--rm', '--network', network, '-e', f'DATABASE_URL={url}', tag,
            'python', '-m', 'alembic', 'upgrade', 'head')
        run('docker', 'run', '-d', '--name', api, '--network', network, '-e', f'DATABASE_URL={url}',
            '-p', '127.0.0.1:18300:8000', tag)
        for attempt in range(45):
            state = subprocess.run(['docker', 'exec', api, 'python', '-c',
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=8).read()"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if state.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('CI API not ready')
        run('python', 'ops/http_check.py', '--base', 'http://127.0.0.1:18300', '--out', str(evidence / 'http.json'), cwd=ROOT)
        info = json.loads(run('docker', 'image', 'inspect', tag, capture_output=True, text=True).stdout)[0]
        assert info['Config']['Labels']['org.opencontainers.image.revision'] == sha
        tar = evidence / 'image.tar'
        run('docker', 'save', '-o', str(tar), tag)
        manifest = {'commit': sha, 'tag': tag, 'image_id': info['Id'], 'os': info['Os'], 'architecture': info['Architecture'],
                    'tar_sha256': digest(tar), 'http_check_passed': True}
        (evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        # Pointer is metadata only; previous attempt evidence remains untouched.
        pointer = ROOT / 'evidence' / ('latest-build-' + nonce + '.tmp')
        pointer.write_text(str(evidence) + '\n', encoding='utf-8')
        pointer.replace(ROOT / 'evidence' / 'latest-build.txt')
    finally:
        for name in (api, db):
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()

