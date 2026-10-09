import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from guard import guard, k

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    guard()
    assert os.environ['GITHUB_REF'] == 'refs/heads/main'
    folder = ROOT / 'evidence'
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest['commit'] == os.environ['GITHUB_SHA'] and manifest['http_check_passed'] is True
    checksum = hashlib.sha256()
    with (folder / 'image.tar').open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    assert checksum.hexdigest() == manifest['tar_sha256']
    run('docker', 'load', '-i', str(folder / 'image.tar'))
    info = json.loads(run('docker', 'image', 'inspect', manifest['tag'], capture_output=True, text=True).stdout)[0]
    assert info['Id'] == manifest['image_id']
    arch = run('docker', 'info', '--format', '{{.Architecture}}', capture_output=True, text=True).stdout.strip()
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(arch, arch)
    assert info['Architecture'] == arch, 'Build for the same architecture as the deployment node'
    repo = 'localhost:5001/dva-ticket'
    tag = repo + ':ci-' + manifest['commit']
    run('docker', 'tag', manifest['tag'], tag)
    run('docker', 'push', tag)
    pushed = json.loads(run('docker', 'image', 'inspect', tag, capture_output=True, text=True).stdout)[0]
    candidates = [ref for ref in pushed['RepoDigests'] if ref.startswith(repo + '@sha256:')]
    assert len(candidates) == 1
    ref = candidates[0]
    (folder / 'deployed-image.json').write_text(json.dumps({'image_ref': ref, 'source': manifest}, indent=2))
    for ns, port in (('dev', 18220), ('stage', 18230)):
        run('helm', 'upgrade', '--install', 'ticket-' + ns, str(ROOT / 'chart'), '--kube-context', 'kind-dva-course',
            '--namespace', ns, '-f', str(ROOT / 'chart' / ('values-' + ns + '.yaml')),
            '--set-string', 'image.ref=' + ref, '--set-string', 'releaseLabel=ci-' + manifest['commit'],
            '--wait', '--atomic', '--timeout', '180s')
        run('helm', 'test', 'ticket-' + ns, '--kube-context', 'kind-dva-course', '--namespace', ns, '--timeout', '60s')
        run('python3', 'ops/http_check.py', '--base', f'http://127.0.0.1:{port}', '--out', f'evidence/deploy-{ns}.json', cwd=ROOT)
        k('-n', ns, 'get', 'pods', '-l', 'app=ticket-api', '-o', 'wide')
    print('The same checked digest passed dev and stage useful-operation checks')


if __name__ == '__main__':
    main()
