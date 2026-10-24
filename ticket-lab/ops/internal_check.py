# Acceptance and safety assertions must never be disabled.
if not __debug__:
    raise RuntimeError('Optimized Python is unsupported; unset PYTHONOPTIMIZE and do not use -O')

import errno
import json
import queue
import signal
import socket
import threading
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
            # At most 5s per socket I/O. The CLI also has a hard 55s alarm,
            # including DNS/body reads, below Helm's unchanged 60s timeout.
            response = opener.open(request, timeout=min(5, remaining))
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


def wait_readiness(opener, *, budget=25, max_attempts=12, delay=0.5,
                   monotonic=time.monotonic, sleep=time.sleep):
    """Bound the readiness-only GET wait, including slow DNS/transport, to 25s.

    The daemon worker can only issue GET; cancellation forbids further attempts.
    No HTTP error or malformed contract is converted into a retry.
    """
    if not 0 < budget <= 30:
        raise ValueError('Readiness budget must be between 0 and 30 seconds')
    deadline = monotonic() + budget
    stopped = threading.Event()
    result = queue.Queue(maxsize=1)
    request = urllib.request.Request('http://ticket-api:8000/ready')
    def worker():
        try:
            for attempt in range(1, max_attempts + 1):
                remaining = deadline - monotonic()
                if stopped.is_set() or remaining <= 0:
                    break
                print(f'stage=ready event=connect attempt={attempt}', flush=True)
                try:
                    with opener.open(request, timeout=min(3, remaining)) as response:
                        body = json.load(response)
                        require(response.status == 200 and isinstance(body, dict) and body.get('status') == 'ready', 'ready')
                    if stopped.is_set() or monotonic() > deadline:
                        break
                    print(f'stage=ready event=confirmed attempt={attempt}', flush=True)
                    result.put(None)
                    return
                except urllib.error.HTTPError:
                    raise
                except (urllib.error.URLError, OSError) as error:
                    reason = error.reason if isinstance(error, urllib.error.URLError) else error
                    safe_retry = isinstance(reason, TimeoutError) or (isinstance(reason, ConnectionRefusedError) and reason.errno == errno.ECONNREFUSED)
                    if not safe_retry:
                        raise
                    print(f'stage=ready event=transport_retry error_type={type(reason).__name__} attempt={attempt}', flush=True)
                    remaining = deadline - monotonic()
                    if stopped.is_set() or remaining <= 0 or attempt == max_attempts:
                        break
                    sleep(min(delay, remaining))
            raise TimeoutError('Readiness GET budget exhausted; POST not attempted')
        except Exception as error:
            result.put(error)
    threading.Thread(target=worker, daemon=True, name='readiness-get-only').start()
    try:
        outcome = result.get(timeout=budget)
    except queue.Empty:
        raise TimeoutError('Readiness GET wall-clock budget exhausted; POST not attempted') from None
    finally:
        stopped.set()
    if outcome is not None:
        raise outcome


def main(opener=None):
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    wait_readiness(opener)
    title = 'helm-check-' + uuid.uuid4().hex
    request = urllib.request.Request('http://ticket-api:8000/tickets',
        data=json.dumps({'title': title}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    with open_connect_refused_only(opener, request, 'create') as response:
        require(response.status == 201, 'create status')
        created = json.load(response)
        require(type(created.get('id')) is int and created['id'] > 0 and
                created.get('title') == title and created.get('status') == 'open', 'create body')
    print(f'stage=create event=committed_ticket id={created["id"]}', flush=True)
    read_request = urllib.request.Request(f'http://ticket-api:8000/tickets/{created["id"]}')
    with open_connect_refused_only(opener, read_request, 'read') as response:
        require(response.status == 200 and json.load(response) == created, 'read')
    print('Internal readiness and create/read succeeded', flush=True)


def failure_tcp_probe():
    """Diagnostic TCP handshakes only, after failure. No HTTP request is sent."""
    # Numeric resolver result from the configured Service DNS is logged. The
    # watchdog includes this diagnostic; never create/retry a ticket here.
    addresses=socket.getaddrinfo('ticket-api',8000,type=socket.SOCK_STREAM)
    targets=sorted({(family,address[0],address[1]) for family,_,_,_,address in addresses})
    print(json.dumps({'event':'failure_dns','service':'ticket-api','addresses':[host for _,host,_ in targets]}),flush=True)
    for family,host,port in targets[:2]:
        for attempt in range(1,3):
            sock=socket.socket(family,socket.SOCK_STREAM);sock.settimeout(0.5)
            started=time.monotonic()
            outcome='interrupted'
            try:
                sock.connect((host,port))
                outcome='connected'
            except HookDeadlineExceeded:
                raise
            except OSError as error:
                outcome=type(error).__name__
            finally:
                print(json.dumps({'event':'failure_tcp_only','target_ip':host,'port':port,'attempt':attempt,
                    'local':sock.getsockname(),'outcome':outcome,'elapsed_ms':round((time.monotonic()-started)*1000,2)}),flush=True)
                sock.close()


class HookDeadlineExceeded(TimeoutError):
    pass


def run_cli():
    # The hook image is Linux. Importing on Windows remains safe for unit tests.
    if not hasattr(signal, 'SIGALRM'):
        raise RuntimeError('Hook CLI requires Linux SIGALRM for its overall deadline')
    def expired(signum, frame):
        print('stage=check event=overall_budget_exhausted', flush=True)
        raise HookDeadlineExceeded('Internal hook overall 55s budget exhausted')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(55)
    try:
        try:
            main()
        except Exception as original_error:
            # Keep diagnostics inside the same 55s deadline. Skip them if the
            # watchdog has already fired; preserve the original failure.
            if not isinstance(original_error,HookDeadlineExceeded):
                try:failure_tcp_probe()
                except Exception as error:print(f'event=failure_probe_error error_type={type(error).__name__}',flush=True)
            raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    run_cli()
