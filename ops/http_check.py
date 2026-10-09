# Short useful-operation check, not a long-term SLO measurement.
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--read-id', type=int)
    parser.add_argument('--expected', type=Path)
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.base)
    if (url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1'} or url.port is None
        or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
        parser.error('use an HTTP loopback URL with an explicit training port')
    if args.expected and args.read_id is None:
        parser.error('--expected requires --read-id')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'requests': [], 'passed': False}
    with args.out.open('x', encoding='utf-8') as output:
        def request(method, path, data=None, expected_status=200):
            started = time.monotonic()
            row = {'method': method, 'path': path, 'status': None, 'response': None}
            report['requests'].append(row)
            req = urllib.request.Request(args.base.rstrip('/') + path,
                data=None if data is None else json.dumps(data).encode(),
                headers={} if data is None else {'Content-Type': 'application/json'}, method=method)
            try:
                try:
                    response = opener.open(req, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    row['status'] = response.status
                    row['response'] = json.load(response)
                if row['status'] != expected_status:
                    raise RuntimeError('Unexpected HTTP status')
                return row['response']
            finally:
                row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        try:
            assert request('GET', '/health')['status'] == 'alive'
            assert request('GET', '/ready')['status'] == 'ready'
            if args.read_id is not None:
                old = request('GET', f'/tickets/{args.read_id}')
                assert old['id'] == args.read_id
                report['old_ticket'] = old
                if args.expected:
                    assert old == json.loads(args.expected.read_text(encoding='utf-8'))
            title = 'dva-http-' + uuid.uuid4().hex
            created = request('POST', '/tickets', {'title': title}, 201)
            assert created['title'] == title and created['status'] == 'open'
            path = f'/tickets/{created["id"]}'
            assert request('GET', path) == created
            updated = request('PATCH', path, {'status': 'closed'})
            assert updated == {**created, 'status': 'closed'}
            assert request('GET', path) == updated
            request('POST', '/tickets', {'title': '   '}, 422)
            request('PATCH', path, {'status': 'unknown'}, 422)
            request('GET', '/tickets/0', expected_status=404)
            report['created_ticket'] = updated
            report['passed'] = True
        except Exception as error:
            report['error_type'] = type(error).__name__
        finally:
            report['finished_utc'] = datetime.now(timezone.utc).isoformat()
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write('\n')
    print(f'Saved {args.out}; passed={report["passed"]}')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
