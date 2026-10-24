# Acceptance and safety assertions must never be disabled.
if not __debug__:
    raise RuntimeError('Optimized Python is unsupported; unset PYTHONOPTIMIZE and do not use -O')

import errno
import json
import time
import urllib.error
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_connect_refused_only(opener, request, stage, *, budget=10, max_attempts=6,
                              delay=1, monotonic=time.monotonic, sleep=time.sleep):
    """Retry TCP establishment refusal only; never replay an ambiguous POST."""
    deadline = monotonic() + budget
    last_refusal = None
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        print(f'stage={stage} event=connect attempt={attempt}', flush=True)
        try:
            response = opener.open(request, timeout=min(10, remaining))
            print(f'stage={stage} event=connected attempt={attempt}', flush=True)
            return response
        except urllib.error.HTTPError:
            print(f'stage={stage} event=failed error_type=HTTPError', flush=True)
            raise
        except (urllib.error.URLError, OSError) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            if not isinstance(reason, ConnectionRefusedError) or reason.errno != errno.ECONNREFUSED:
                print(f'stage={stage} event=failed error_type={type(reason).__name__}', flush=True)
                raise
            last_refusal = error
            print(f'stage={stage} event=tcp_refused attempt={attempt}', flush=True)
            remaining = deadline - monotonic()
            if attempt == max_attempts or remaining <= 0:
                break
            sleep(min(delay, remaining))
    print(f'stage={stage} event=refusal_budget_exhausted', flush=True)
    if last_refusal is not None:
        raise last_refusal
    raise TimeoutError('TCP establishment retry budget exhausted')


def require(condition, stage):
    if not condition:
        raise RuntimeError(f'Internal {stage} contract failed')


def main(opener=None):
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    ready_request = urllib.request.Request('http://ticket-api:8000/ready')
    with open_connect_refused_only(opener, ready_request, 'ready') as response:
        require(response.status == 200 and json.load(response).get('status') == 'ready', 'ready')
    title = 'helm-check-' + uuid.uuid4().hex
    request = urllib.request.Request('http://ticket-api:8000/tickets',
        data=json.dumps({'title': title}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with open_connect_refused_only(opener, request, 'create') as response:
        require(response.status == 201, 'create status')
        created = json.load(response)
        require(type(created.get('id')) is int and created['id'] > 0 and
                created.get('title') == title and created.get('status') == 'open', 'create body')
    read_request = urllib.request.Request(f'http://ticket-api:8000/tickets/{created["id"]}')
    with open_connect_refused_only(opener, read_request, 'read') as response:
        require(response.status == 200 and json.load(response) == created, 'read')
    print('Internal readiness and create/read succeeded', flush=True)


if __name__ == '__main__':
    main()
