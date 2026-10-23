"""Stdlib-only regression checks. No subprocess, network, Docker or cluster calls."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
sys.path.insert(0, str(ROOT / 'recovery'))
sys.path.insert(0, str(ROOT / 'load'))


def module(path):
    spec = importlib.util.spec_from_file_location('review_' + path.replace('/', '_').replace('.', '_'), ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode())
        self.status = status


class OptimizationTests(unittest.TestCase):
    def test_optimized_entrypoints_fail_before_imports_or_side_effects(self):
        for path in ('ops/http_check.py', 'ci/build.py', 'ci/deploy.py',
                     'recovery/restore.py', 'ops/registry.py', 'monitoring/prepare_admin.py',
                     'ops/foundation_prepare.py', 'ticket-lab/ops/internal_check.py', 'qa/capture_ui.py'):
            with self.subTest(path=path):
                code = compile((ROOT / path).read_text(encoding='utf-8'), path, 'exec', optimize=2)
                with self.assertRaisesRegex(RuntimeError, 'Optimized Python'):
                    exec(code, {'__name__': '__main__'})


class HTTPTests(unittest.TestCase):
    def test_old_data_mismatch_fails_before_mutation(self):
        check = module('ops/http_check.py')
        with tempfile.TemporaryDirectory() as folder:
            expected = Path(folder) / 'expected.json'
            expected.write_text(json.dumps({'id': 7, 'title': 'preserved', 'status': 'open'}))
            output = Path(folder) / 'result.json'
            opener = Mock()
            opener.open.side_effect = [Response({'status': 'alive'}), Response({'status': 'ready'}),
                                      Response({'id': 7, 'title': 'changed', 'status': 'open'})]
            with patch.object(sys, 'argv', ['check', '--base', 'http://127.0.0.1:18220',
                                          '--out', str(output), '--read-id', '7', '--expected', str(expected)]), \
                 patch.object(check.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(check.main(), 1)
            self.assertFalse(json.loads(output.read_text())['passed'])
            self.assertEqual([call.args[0].get_method() for call in opener.open.call_args_list], ['GET'] * 3)

    def test_redirect_is_rejected_and_not_reported_as_success(self):
        check = module('ops/http_check.py')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'result.json'
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError('http://127.0.0.1:18220/health', 302,
                                                            'Found', {}, io.BytesIO(b'{}'))
            with patch.object(sys, 'argv', ['check', '--base', 'http://127.0.0.1:18220', '--out', str(output)]), \
                 patch.object(check.urllib.request, 'build_opener', return_value=opener) as factory:
                self.assertEqual(check.main(), 1)
            from safety import NoRedirect
            handler = next(arg for arg in factory.call_args.args if isinstance(arg, NoRedirect))
            self.assertIsNone(handler.redirect_request(None, None, 302, 'Found', {}, 'https://outside.invalid'))
            self.assertFalse(json.loads(output.read_text())['passed'])


class LoadTests(unittest.TestCase):
    def test_allowed_ports_and_bad_hosts(self):
        from target_safety import training_host
        self.assertEqual(training_host('http://127.0.0.1:18220/'), 'http://127.0.0.1:18220')
        self.assertEqual(training_host('http://127.0.0.1:18230'), 'http://127.0.0.1:18230')
        for value in (None, 'https://outside.invalid', 'http://localhost:18220',
                      'http://127.0.0.1:80', 'http://user@127.0.0.1:18220',
                      'http://127.0.0.1:18220/tickets', 'http://127.0.0.1:18220?x=1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                training_host(value)

    def test_actual_session_cannot_override_allowed_environment(self):
        from target_safety import check_user
        user = SimpleNamespace(environment=SimpleNamespace(host='http://127.0.0.1:18220'),
                               host=None, client=SimpleNamespace(base_url='https://outside.invalid'))
        with self.assertRaises(ValueError):
            check_user(user)

    def test_supplied_tasks_reject_target_before_any_request(self):
        # Stub only Locust's class/decorators; requests are mocks and never sent.
        def task(weight):
            return weight if callable(weight) else (lambda func: func)
        locust = SimpleNamespace(HttpUser=object, task=task, constant=lambda value: value)
        with patch.dict(sys.modules, {'locust': locust}):
            for path, cls_name, tasks in (
                ('load/locustfile.py', 'TicketUser', ('list_tickets', 'create_ticket')),
                ('load/read_only.py', 'ReadOnlyTicketUser', ('list_tickets',))):
                loaded = module(path)
                user = getattr(loaded, cls_name)()
                user.environment = SimpleNamespace(host='https://outside.invalid')
                user.host = None
                user.client = Mock(base_url='https://outside.invalid')
                for name in tasks:
                    with self.subTest(path=path, task=name), self.assertRaises(ValueError):
                        getattr(user, name)()
                user.client.get.assert_not_called()
                user.client.post.assert_not_called()

    def test_profiles_disable_redirects_and_fail_3xx(self):
        def task(weight):
            return weight if callable(weight) else (lambda func: func)
        locust = SimpleNamespace(HttpUser=object, task=task, constant=lambda value: value)
        with patch.dict(sys.modules, {'locust': locust}):
            for path, cls_name, method in (
                ('load/locustfile.py', 'TicketUser', 'list_tickets'),
                ('load/locustfile.py', 'TicketUser', 'create_ticket'),
                ('load/read_only.py', 'ReadOnlyTicketUser', 'list_tickets')):
                loaded = module(path)
                user = getattr(loaded, cls_name)()
                user.environment = SimpleNamespace(host='http://127.0.0.1:18220')
                user.host = None
                user.client = MagicMock(base_url='http://127.0.0.1:18220')
                request = user.client.post if method == 'create_ticket' else user.client.get
                response = request.return_value.__enter__.return_value
                response.status_code = 302
                response.json.return_value = {}
                getattr(user, method)()
                self.assertIs(request.call_args.kwargs['allow_redirects'], False)
                response.failure.assert_called_once()


class BuildTests(unittest.TestCase):
    def test_dirty_inputs_fail_before_install_or_docker(self):
        build = module('ci/build.py')
        sha = 'a' * 40
        with patch.object(build, 'run', side_effect=[SimpleNamespace(stdout=sha + '\n'),
                                                   SimpleNamespace(stdout=' M ticket-lab/app/main.py\n')]) as run:
            with self.assertRaisesRegex(RuntimeError, 'Commit build inputs'):
                build.clean_source(sha)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[:3], ('git', 'status', '--porcelain'))

    def test_attempt_directories_preserve_previous_evidence(self):
        build = module('ci/build.py')
        with tempfile.TemporaryDirectory() as folder, patch.object(build, 'ROOT', Path(folder)):
            first = build.new_evidence()
            saved = first / 'http.json'
            saved.write_text('old evidence')
            second = build.new_evidence()
            self.assertNotEqual(first, second)
            self.assertTrue(second.is_dir())
            self.assertEqual(saved.read_text(), 'old evidence')


class RestoreTests(unittest.TestCase):
    def test_bad_dump_checksum_stops_before_namespace_or_secret_reads(self):
        restore = module('recovery/restore.py')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'manifest.json').write_text(json.dumps({'source_namespace': 'stage', 'sha256': 'expected'}))
            with patch.object(sys, 'argv', ['restore', folder, '--destination', 'restore-20261023000100']), \
                 patch.object(restore, 'guard'), patch.object(restore, 'digest', return_value='corrupt'), \
                 patch.object(restore, 'k') as k:
                with self.assertRaises(AssertionError):
                    restore.main()
            k.assert_not_called()

    def test_missing_source_secret_does_not_create_destination(self):
        restore = module('recovery/restore.py')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'manifest.json').write_text(json.dumps({'source_namespace': 'stage', 'sha256': 'hash'}))
            (directory / 'snapshot.json').write_text('{}')
            def kube(*args, **kwargs):
                if args[:3] == ('get', 'namespace', 'stage'):
                    return SimpleNamespace(stdout=json.dumps({'metadata': {'labels': {'course': 'dva'}}}))
                raise RuntimeError('source secret missing')
            with patch.object(sys, 'argv', ['restore', folder, '--destination', 'restore-20261023000100']), \
                 patch.object(restore, 'guard'), patch.object(restore, 'digest', return_value='hash'), \
                 patch.object(restore, 'k', side_effect=kube) as k:
                with self.assertRaisesRegex(RuntimeError, 'source secret missing'):
                    restore.main()
            self.assertFalse(any(call.args and call.args[0] == 'create' for call in k.call_args_list))

    def test_missing_image_stops_before_destination_creation(self):
        restore = module('recovery/restore.py')
        import guard
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'manifest.json').write_text(json.dumps({
                'source_namespace': 'stage', 'sha256': 'hash', 'image_ref': 'missing:training'}))
            (directory / 'snapshot.json').write_text('{}')
            def kube(*args, **kwargs):
                if args[:3] == ('get', 'namespace', 'stage'):
                    return SimpleNamespace(stdout=json.dumps({'metadata': {'labels': {'course': 'dva'}}}))
                return SimpleNamespace(stdout=json.dumps({'data': {}}))
            with patch.object(sys, 'argv', ['restore', folder, '--destination', 'restore-20261023000100']), \
                 patch.object(restore, 'guard'), patch.object(restore, 'digest', return_value='hash'), \
                 patch.object(restore, 'k', side_effect=kube) as k, \
                 patch.object(guard, 'run', side_effect=[SimpleNamespace(stdout='dva-course-control-plane\n'),
                                                        RuntimeError('image unavailable')]) as run:
                with self.assertRaisesRegex(RuntimeError, 'image unavailable'):
                    restore.main()
            self.assertFalse(any(call.args and call.args[0] == 'create' for call in k.call_args_list))
            self.assertEqual(run.call_args.args[3:5], ('crictl', 'inspecti'))


if __name__ == '__main__':
    unittest.main()
