import copy
import subprocess
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ci'))
from convergence import backend_ips, inspect, wait_convergence


def fixture():
    service = {'spec': {'selector': {'app': 'ticket-api'}, 'clusterIP': '10.96.28.130', 'ports': [{'port': 8000, 'targetPort': 'http'}]}}
    deployment = {'metadata': {'uid': 'deploy', 'generation': 2, 'annotations': {'deployment.kubernetes.io/revision': '2'}}, 'spec': {'replicas': 2}, 'status': {'observedGeneration': 2, 'updatedReplicas': 2, 'readyReplicas': 2}}
    sets = {'items': [{'metadata': {'uid': 'rs2', 'annotations': {'deployment.kubernetes.io/revision': '2'}, 'ownerReferences': [{'uid': 'deploy'}]}}]}
    pods = {'items': []}
    endpoints = []
    for suffix in (43, 44):
        ip = '10.244.0.' + str(suffix)
        pods['items'].append({'metadata': {'uid': ip, 'ownerReferences': [{'uid': 'rs2'}]}, 'status': {'podIP': ip, 'conditions': [{'type': 'Ready', 'status': 'True'}]}})
        endpoints.append({'addresses': [ip], 'conditions': {'ready': True, 'serving': True, 'terminating': False}, 'targetRef': {'uid': ip}})
    slices = {'items': [{'metadata': {'labels': {'kubernetes.io/service-name': 'ticket-api'}}, 'addressType': 'IPv4', 'ports': [{'port': 8000}], 'endpoints': endpoints}]}
    nat = '''*nat
-A KUBE-SERVICES -d 10.96.28.130/32 -p tcp -m comment --comment "stage/ticket-api:http cluster IP" --dport 8000 -j KUBE-SVC-ABC
-A KUBE-SVC-ABC -j KUBE-SEP-ONE
-A KUBE-SVC-ABC -j KUBE-SEP-TWO
-A KUBE-SEP-ONE -p tcp -j DNAT --to-destination 10.244.0.43:8000
-A KUBE-SEP-TWO -p tcp -j DNAT --to-destination 10.244.0.44:8000
COMMIT
'''
    return [service, deployment, sets, pods, slices, nat, 'mode: "iptables"\n', 'Using iptables Proxier']


class ConvergenceTests(unittest.TestCase):
    def test_current_two_ips(self):
        self.assertTrue(inspect(fixture(), 'stage')['passed'])

    def test_stale_endpoint(self):
        data = fixture()
        data[4]['items'][0]['endpoints'][0]['addresses'] = ['10.244.0.33']
        self.assertFalse(inspect(data, 'stage')['passed'])

    def test_old_dnat_backend(self):
        data = fixture()
        data[5] = data[5].replace('10.244.0.43:8000', '10.244.0.33:8000')
        self.assertFalse(inspect(data, 'stage')['passed'])

    def test_missing_service_chain(self):
        with self.assertRaises(ValueError):
            backend_ips(fixture()[5], '10.96.28.131', 'stage')

    def test_unsupported_mode(self):
        with self.assertRaises(ValueError):
            backend_ips('table ip kube-proxy {}', '10.96.28.130', 'stage')

    def test_backend_port(self):
        data = fixture()
        data[5] = data[5].replace(':8000', ':8080')
        with self.assertRaises(ValueError):
            inspect(data, 'stage')

    def test_current_pod_uid_must_match(self):
        data = fixture()
        data[4]['items'][0]['endpoints'][0]['targetRef']['uid'] = 'old-pod'
        self.assertFalse(inspect(data, 'stage')['passed'])

    def test_terminating_pod_is_not_current_ready(self):
        data = fixture()
        data[3]['items'][0]['metadata']['deletionTimestamp'] = 'now'
        self.assertFalse(inspect(data, 'stage')['passed'])

    def test_deadline_with_valid_unconverged_state(self):
        now = [0.0]
        data = fixture()
        data[5] = data[5].replace('10.244.0.43:8000', '10.244.0.33:8000')
        def sleep(seconds):
            now[0] += seconds
        with self.assertRaises(TimeoutError):
            wait_convergence(lambda remaining: data, 'stage', budget=.5, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(now[0], .5)

    def test_collector_overrun_cannot_pass(self):
        now = [0.0]
        def collect(remaining):
            now[0] += 2
            return fixture()
        with self.assertRaises(TimeoutError):
            wait_convergence(collect, 'stage', budget=1, clock=lambda: now[0])

    def test_original_command_error_preserved(self):
        error = subprocess.CalledProcessError(7, ['read-only-command'])
        def collect(remaining):
            raise error
        with self.assertRaises(subprocess.CalledProcessError) as context:
            wait_convergence(collect, 'stage')
        self.assertIs(context.exception, error)

    def test_valid_unconverged_then_success(self):
        stale = copy.deepcopy(fixture())
        stale[5] = stale[5].replace('10.244.0.43:8000', '10.244.0.33:8000')
        states = iter([stale, fixture()])
        history = wait_convergence(lambda remaining: next(states), 'stage', sleep=lambda seconds: None)
        self.assertEqual([state['passed'] for state in history], [False, True])

    def test_explicit_proxy_mode_required(self):
        for mode in ('mode: ipvs', 'mode: nftables', 'invalid'):
            data = fixture()
            data[6] = mode
            with self.assertRaises(ValueError):
                inspect(data, 'stage')

    def test_default_mode_requires_actual_proxy_evidence(self):
        data = fixture()
        data[6] = 'mode: ""'
        self.assertTrue(inspect(data, 'stage')['passed'])
        data[7] = ''
        with self.assertRaises(ValueError):
            inspect(data, 'stage')

    def test_records_unconverged_state_before_deadline(self):
        seen = []
        data = fixture()
        data[5] = data[5].replace('10.244.0.43:8000', '10.244.0.33:8000')
        now = [0.0]
        with self.assertRaises(TimeoutError):
            wait_convergence(lambda remaining: data, 'stage', budget=.1,
                             clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds), record=seen.append)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0]['passed'])


if __name__ == '__main__':
    unittest.main()
