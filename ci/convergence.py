"""Candidate only: read-only iptables-mode gate for the bound single-node kind lab."""
import ipaddress
import json
import re
import shlex
import subprocess
import time


def ipv4(value):
    parsed = ipaddress.ip_address(value)
    if parsed.version != 4:
        raise ValueError('Only IPv4 iptables mode is supported')
    return str(parsed)


def backend_ips(nat, cluster_ip, namespace):
    """Follow actual Service -> endpoint chain jumps, not comments alone."""
    if '*nat' not in nat or 'COMMIT' not in nat:
        raise ValueError('Unsupported or incomplete iptables-save output')
    rules = {}
    for line in nat.splitlines():
        if line.startswith('-A '):
            tokens = shlex.split(line)
            rules.setdefault(tokens[1], []).append(tokens[2:])
    def option(tokens, key):
        return tokens[tokens.index(key) + 1] if key in tokens else None
    service = []
    for rule in rules.get('KUBE-SERVICES', []):
        if option(rule, '-d') == cluster_ip + '/32' and option(rule, '--dport') == '8000' and option(rule, '-p') == 'tcp':
            if option(rule, '--comment') != namespace + '/ticket-api:http cluster IP':
                raise ValueError('Unexpected Service identity')
            service.append(option(rule, '-j'))
    if len(service) != 1 or not str(service[0]).startswith('KUBE-SVC-'):
        raise ValueError('Missing or ambiguous iptables Service chain; unsupported mode')
    endpoints = {option(rule, '-j') for rule in rules.get(service[0], [])
                 if str(option(rule, '-j')).startswith('KUBE-SEP-')}
    if not endpoints:
        raise ValueError('No Service endpoint chains')
    result = set()
    for chain in endpoints:
        destinations = [option(rule, '--to-destination') for rule in rules.get(chain, [])
                        if option(rule, '-j') == 'DNAT']
        if len(destinations) != 1:
            raise ValueError('Malformed endpoint DNAT chain')
        address, port = destinations[0].rsplit(':', 1)
        if port != '8000':
            raise ValueError('Unexpected backend port')
        result.add(ipv4(address))
    return result


def inspect(snapshot, namespace):
    service, deployment, replicasets, pods, slices, nat, proxy_config, proxy_log = snapshot
    mode = re.search(r'^mode:\s*[\"\']?(\w*)[\"\']?\s*$', proxy_config, re.MULTILINE)
    if mode is None or mode[1] not in {'', 'iptables'}:
        raise ValueError('Unsupported or malformed kube-proxy mode')
    if mode[1] == '' and not re.search(r'Using iptables Proxier|Using iptables proxy', proxy_log, re.IGNORECASE):
        raise ValueError('Default proxy mode needs affirmative iptables startup log evidence')
    if service['spec']['selector'] != {'app': 'ticket-api'} or service['spec'].get('publishNotReadyAddresses', False):
        raise ValueError('Unsupported Service selector/readiness policy')
    ports = service['spec']['ports']
    if len(ports) != 1 or ports[0]['port'] != 8000 or ports[0]['targetPort'] != 'http' or ports[0].get('protocol', 'TCP') != 'TCP':
        raise ValueError('Unexpected Service ports')
    cluster_ip = ipv4(service['spec']['clusterIP'])
    revision = deployment['metadata']['annotations']['deployment.kubernetes.io/revision']
    uid = deployment['metadata']['uid']
    current = [rs['metadata']['uid'] for rs in replicasets['items']
               if rs['metadata'].get('annotations', {}).get('deployment.kubernetes.io/revision') == revision
               and any(owner['uid'] == uid for owner in rs['metadata'].get('ownerReferences', []))]
    if len(current) != 1:
        raise ValueError('Cannot establish current ReplicaSet')
    desired = deployment['spec']['replicas']
    status = deployment['status']
    if desired != 2:
        raise ValueError('Candidate supports exactly two replicas')
    ready = {}
    for pod in pods['items']:
        metadata = pod['metadata']
        if metadata.get('deletionTimestamp'):
            continue
        if not any(owner['uid'] == current[0] for owner in metadata.get('ownerReferences', [])):
            continue
        if any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', [])):
            ready[ipv4(pod['status']['podIP'])] = metadata['uid']
    endpoints = {}
    for item in slices['items']:
        if item['metadata']['labels']['kubernetes.io/service-name'] != 'ticket-api' or item['addressType'] != 'IPv4':
            raise ValueError('Unexpected EndpointSlice identity/type')
        if not any(p['port'] == 8000 and p.get('protocol', 'TCP') == 'TCP' for p in item['ports']):
            raise ValueError('Unexpected EndpointSlice port')
        for endpoint in item['endpoints']:
            conditions = endpoint['conditions']
            if conditions.get('ready') is True and conditions.get('terminating') is not True and conditions.get('serving', True) is True:
                for address in endpoint['addresses']:
                    endpoints[ipv4(address)] = endpoint['targetRef']['uid']
    routed = backend_ips(nat, cluster_ip, namespace)
    passed = (status.get('observedGeneration') == deployment['metadata']['generation']
              and status.get('updatedReplicas') == desired and status.get('readyReplicas') == desired
              and len(ready) == desired and ready == endpoints and set(ready) == routed)
    return {'passed': passed, 'pod_ips': sorted(ready), 'endpoint_ips': sorted(endpoints),
            'dnat_ips': sorted(routed), 'deployment_revision': revision,
            'service_ip': cluster_ip}


def wait_convergence(collect, namespace, budget=25, clock=time.monotonic, sleep=time.sleep, record=lambda state: None):
    """collect receives remaining wall budget and MUST bound every subprocess by it.

    Malformed/unsupported data and collection errors remain terminal. Only a valid
    but unconverged observation is polled. No hook/HTTP/POST is invoked here.
    """
    if not 0 < budget <= 30:
        raise ValueError('Budget must be in (0,30] seconds')
    deadline = clock() + budget
    history = []
    while clock() < deadline:
        snapshot = collect(deadline - clock())
        if clock() >= deadline:
            raise TimeoutError('Convergence collection exhausted budget')
        state = inspect(snapshot, namespace)
        history.append(state)
        record(state)
        if state['passed']:
            return history
        sleep(min(.2, max(0, deadline - clock())))
    raise TimeoutError('Service/EndpointSlice/current-pod convergence budget exhausted')


def collect_bound(namespace, remaining):
    deadline = time.monotonic() + remaining
    def read(command):
        timeout = min(3, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError('Snapshot deadline')
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout).stdout
    base = ['kubectl', '--context', 'kind-dva-course', '--request-timeout=2s', '-n', namespace, 'get']
    values = [json.loads(read(base + args + ['-o', 'json'])) for args in [
        ['service', 'ticket-api'], ['deployment', 'ticket-api'],
        ['replicasets', '-l', 'app=ticket-api'], ['pods', '-l', 'app=ticket-api'],
        ['endpointslices', '-l', 'kubernetes.io/service-name=ticket-api']]]
    values.append(read(['docker', 'exec', 'dva-course-control-plane', 'iptables-save', '-t', 'nat']))
    config = json.loads(read(['kubectl', '--context', 'kind-dva-course', '--request-timeout=2s', '-n', 'kube-system', 'get', 'configmap', 'kube-proxy', '-o', 'json']))
    values.append(config['data']['config.conf'])
    values.append(read(['kubectl', '--context', 'kind-dva-course', '--request-timeout=2s', '-n', 'kube-system', 'logs', '-l', 'k8s-app=kube-proxy', '--tail=200', '--limit-bytes=32768']))
    return values


def guarded_gate(root, namespace, state_path=None):
    """root is the reviewed ticket-lab root supplied by the CI integration."""
    import os
    import sys
    if namespace not in {'dev', 'stage'} or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Only isolated hosted-runner dev/stage experiment is supported')
    if any(os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_CONTEXT')):
        raise RuntimeError('Remote/overridden Docker is unsupported')
    sys.path.insert(0, str(root / 'ops'))
    from guard import guard
    guard()  # Existing persisted cluster UID, loopback API, kind node label binding.
    context = subprocess.run(['docker', 'context', 'show'], check=True, capture_output=True, text=True, timeout=3).stdout.strip()
    if context != 'default':
        raise RuntimeError('Expected default local Docker context')
    def record(state):
        if state_path is not None:
            with state_path.open('a') as output:
                output.write(json.dumps({'monotonic': time.monotonic(), **state}) + '\n')
    return wait_convergence(lambda remaining: collect_bound(namespace, remaining), namespace, record=record)
