# Configure only the bound training cluster; adapted from the official kind local registry route.
import json
import subprocess
from guard import guard, run

def inspect(kind, name):
    value = subprocess.run(['docker', kind, 'inspect', name], capture_output=True, text=True)
    if value.returncode:
        return None
    return json.loads(value.stdout)[0]

def main():
    guard()
    volume = inspect('volume', 'dva-registry-data')
    if volume and (volume.get('Labels') or {}).get('course') != 'dva':
        raise RuntimeError('Registry volume is not owned by this course')
    if not volume:
        run('docker', 'volume', 'create', '--label', 'course=dva', 'dva-registry-data')
    info = inspect('container', 'dva-registry')
    if info:
        assert info['Config']['Labels'].get('course') == 'dva'
        assert info['Config']['Image'] == 'registry:3'
        binding = info['HostConfig']['PortBindings'].get('5000/tcp')
        assert binding == [{'HostIp': '127.0.0.1', 'HostPort': '5001'}]
        assert any(m.get('Name') == 'dva-registry-data' and m['Destination'] == '/var/lib/registry' for m in info['Mounts'])
        if not info['State']['Running']:
            run('docker', 'start', 'dva-registry')
    else:
        run('docker', 'run', '-d', '--name', 'dva-registry', '--label', 'course=dva',
            '--restart', 'unless-stopped', '-p', '127.0.0.1:5001:5000',
            '-v', 'dva-registry-data:/var/lib/registry', 'registry:3')
    info = inspect('container', 'dva-registry')
    if 'kind' not in info['NetworkSettings']['Networks']:
        run('docker', 'network', 'connect', 'kind', 'dva-registry')
    node = run('kind', 'get', 'nodes', '--name', 'dva-course', capture_output=True, text=True).stdout.split()[0]
    directory = '/etc/containerd/certs.d/localhost:5001'
    run('docker', 'exec', node, 'mkdir', '-p', directory)
    value = '[host."http://dva-registry:5000"]\n  capabilities = ["pull", "resolve"]\n'
    run('docker', 'exec', '-i', node, 'sh', '-c', 'cat > /etc/containerd/certs.d/localhost:5001/hosts.toml', input=value, text=True)
    print('Training registry configured. Prove push and digest pull with the CI candidate.')

if __name__ == '__main__':
    main()
