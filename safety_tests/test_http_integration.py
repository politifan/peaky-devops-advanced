"""Real urllib against disposable loopback HTTP servers; no Docker or external network."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
spec = importlib.util.spec_from_file_location('integration_http_check', ROOT / 'ops/http_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@contextmanager
def server(handler):
    instance = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(instance.server_port)
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=3)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, location=None):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class RealHTTPTests(unittest.TestCase):
    def invoke(self, base, directory, extra=()):
        output = Path(directory) / 'http.json'
        with patch.object(sys, 'argv', ['http', '--base', base, '--out', str(output), *extra]):
            result = check.main()
        return result, json.loads(output.read_text())

    def test_redirect_does_not_reach_other_server(self):
        reached = []
        class Destination(Handler):
            def do_GET(self):
                reached.append(self.path)
                self.reply(200, {'status': 'alive'})
        with server(Destination) as destination:
            class Redirect(Handler):
                def do_GET(self):
                    self.reply(302, {'redirect': True}, destination + '/health')
            with server(Redirect) as base, tempfile.TemporaryDirectory() as folder:
                status, report = self.invoke(base, folder)
        self.assertEqual(status, 1)
        self.assertFalse(report['passed'])
        self.assertEqual(report['requests'][0]['status'], 302)
        self.assertEqual(reached, [])

    def test_wrong_health_stops_before_post(self):
        posted = []
        class WrongHealth(Handler):
            def do_GET(self):
                self.reply(200, {'status': 'wrong'})
            def do_POST(self):
                posted.append(self.path)
                self.reply(201, {})
        with server(WrongHealth) as base, tempfile.TemporaryDirectory() as folder:
            status, report = self.invoke(base, folder)
        self.assertEqual(status, 1)
        self.assertFalse(report['passed'])
        self.assertEqual(posted, [])

    def test_real_positive_and_negative_contract_cycle(self):
        ticket = {}
        class API(Handler):
            def do_GET(self):
                if self.path == '/health':
                    self.reply(200, {'status': 'alive'})
                elif self.path == '/ready':
                    self.reply(200, {'status': 'ready'})
                elif self.path == '/tickets/1':
                    self.reply(200, ticket)
                else:
                    self.reply(404, {'error': 'not found'})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not body.get('title', '').strip():
                    self.reply(422, {'error': 'invalid title'})
                    return
                ticket.update(id=1, title=body['title'], status='open')
                self.reply(201, ticket)
            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if body.get('status') != 'closed':
                    self.reply(422, {'error': 'invalid status'})
                    return
                ticket['status'] = 'closed'
                self.reply(200, ticket)
        with server(API) as base, tempfile.TemporaryDirectory() as folder:
            status, report = self.invoke(base, folder)
        self.assertEqual(status, 0)
        self.assertTrue(report['passed'])
        self.assertEqual([item['method'] for item in report['requests']],
                         ['GET', 'GET', 'POST', 'GET', 'PATCH', 'GET', 'POST', 'PATCH', 'GET'])
        self.assertEqual(report['created_ticket']['status'], 'closed')


if __name__ == '__main__':
    unittest.main()

