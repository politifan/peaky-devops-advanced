"""No real HTTP, sleep, subprocess or Docker; exercise ambiguous-write boundaries."""
import errno
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

PATH = Path(__file__).resolve().parents[1] / 'ticket-lab/ops/internal_check.py'
spec = importlib.util.spec_from_file_location('internal_check_retry_test_module', PATH)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def refused():
    return urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))


class RetryTests(unittest.TestCase):
    def test_refused_then_success_keeps_same_request_and_payload(self):
        request = urllib.request.Request('http://ticket-api:8000/tickets', data=b'{"title":"fixed"}', method='POST')
        response = object()
        opener = Mock()
        opener.open.side_effect = [refused(), response]
        sleep = Mock()
        result = check.open_connect_refused_only(opener, request, 'create', monotonic=lambda: 0, sleep=sleep)
        self.assertIs(result, response)
        self.assertEqual(opener.open.call_count, 2)
        self.assertTrue(all(call.args[0] is request for call in opener.open.call_args_list))
        self.assertEqual(request.data, b'{"title":"fixed"}')
        sleep.assert_called_once_with(1)

    def test_timeout_and_connection_reset_are_never_retried(self):
        for reason in (TimeoutError('ambiguous response'), ConnectionResetError(errno.ECONNRESET, 'reset')):
            for error in (reason, urllib.error.URLError(reason)):
                with self.subTest(error=type(error).__name__, reason=type(reason).__name__):
                    opener = Mock()
                    opener.open.side_effect = error
                    sleep = Mock()
                    with self.assertRaises(type(error)):
                        check.open_connect_refused_only(opener, object(), 'create', monotonic=lambda: 0, sleep=sleep)
                    opener.open.assert_called_once()
                    sleep.assert_not_called()

    def test_http_error_is_never_retried(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('http://ticket-api:8000/tickets', 503, 'unavailable', {}, None)
        sleep = Mock()
        with self.assertRaises(urllib.error.HTTPError):
            check.open_connect_refused_only(opener, object(), 'create', monotonic=lambda: 0, sleep=sleep)
        opener.open.assert_called_once()
        sleep.assert_not_called()

    def test_refusal_attempt_limit_and_elapsed_budget_are_bounded(self):
        opener = Mock()
        opener.open.side_effect = refused()
        sleep = Mock()
        with self.assertRaises(urllib.error.URLError):
            check.open_connect_refused_only(opener, object(), 'create', max_attempts=3, monotonic=lambda: 0, sleep=sleep)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        opener.reset_mock()
        sleep.reset_mock()
        with self.assertRaises(urllib.error.URLError):
            check.open_connect_refused_only(opener, object(), 'create', budget=2,
                monotonic=Mock(side_effect=[0, 0, 2]), sleep=sleep)
        opener.open.assert_called_once()
        sleep.assert_not_called()

    def test_full_check_reuses_one_uuid_on_refused_post(self):
        class Response(io.BytesIO):
            def __init__(self, value, status=200):
                super().__init__(json.dumps(value).encode())
                self.status = status
        calls = []
        created = None
        def open_fake(request, **kwargs):
            nonlocal created
            calls.append(request)
            if request.full_url.endswith('/ready'):
                return Response({'status': 'ready'})
            if request.get_method() == 'POST':
                if created is None:
                    created = {'id': 7, **json.loads(request.data), 'status': 'open'}
                    raise refused()
                return Response(created, 201)
            return Response(created)
        opener = Mock()
        opener.open.side_effect = open_fake
        original = check.open_connect_refused_only
        with patch.object(check, 'open_connect_refused_only', side_effect=lambda *args: original(*args, monotonic=lambda: 0, sleep=lambda _: None)):
            check.main(opener)
        posts = [request for request in calls if request.get_method() == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertIs(posts[0], posts[1])

    def test_post_response_body_timeout_never_replays_write(self):
        class Response(io.BytesIO):
            def __init__(self, body, status):
                super().__init__(body)
                self.status = status
        class AmbiguousBody(Response):
            def read(self, *args):
                raise TimeoutError('response body timeout after request sent')
        opener = Mock()
        opener.open.side_effect = [Response(b'{"status":"ready"}', 200), AmbiguousBody(b'', 201)]
        with self.assertRaises(TimeoutError):
            check.main(opener)
        self.assertEqual(opener.open.call_count, 2)
        posts = [call.args[0] for call in opener.open.call_args_list if call.args[0].get_method() == 'POST']
        self.assertEqual(len(posts), 1)


if __name__ == '__main__':
    unittest.main()
