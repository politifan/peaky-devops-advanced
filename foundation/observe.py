"""Observe two loopback API instances; optional synthetic write cycle. No retries."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_base(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port is not None and 1024 <= parsed.port <= 65535
            and parsed.username is None and parsed.password is None
            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("use an HTTP loopback base URL with an explicit training port")
    return value.rstrip("/")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, type=local_base)
    parser.add_argument("--b", required=True, type=local_base)
    parser.add_argument("--ticket-id", type=int)
    parser.add_argument("--write-cycle", action="store_true")
    parser.add_argument("--label", choices=["dev", "stage"], default="stage")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.a == args.b:
        parser.error("A and B must be different endpoints")
    if args.ticket_id is not None and args.ticket_id <= 0:
        parser.error("ticket-id must be positive")
    if args.out.exists():
        parser.error("output already exists; choose a new evidence filename")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Reserve evidence before sending a potentially mutating request.
    with args.out.open("x", encoding="utf-8", newline="\n") as output:
        result = {"started_at_utc": utc_now(), "bases": {"a": args.a, "b": args.b},
                  "timeout_seconds_per_io": 10, "measurements": [], "checks": {}}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        def request(instance, method, path, payload=None):
            row = {"instance": instance, "method": method, "path": path,
                   "at_utc": utc_now(), "status": None, "response": None, "error_type": None}
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            headers = {} if data is None else {"Content-Type": "application/json"}
            req = urllib.request.Request(result["bases"][instance] + path, data=data,
                                         headers=headers, method=method)
            started = time.monotonic()
            try:
                try:
                    response = opener.open(req, timeout=10)
                except urllib.error.HTTPError as error:
                    # HTTP errors still have an HTTP status and may contain JSON.
                    response = error
                with response:
                    row["status"] = response.code
                    raw = response.read(65537)
                if len(raw) > 65536:
                    row["error_type"] = "ResponseTooLarge"
                else:
                    try:
                        row["response"] = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeError):
                        row["error_type"] = "InvalidJSON"
            except (urllib.error.URLError, OSError) as error:
                # Do not include raw exception messages/connection strings.
                row["error_type"] = type(error).__name__
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            result["measurements"].append(row)
            return row

        def is_state(row, state):
            return (row["status"] == 200 and row["error_type"] is None
                    and isinstance(row["response"], dict) and row["response"].get("status") == state)

        def ticket(row, expected_id):
            body = row["response"]
            return (row["status"] == 200 and row["error_type"] is None
                    and isinstance(body, dict) and body.get("id") == expected_id
                    and isinstance(body.get("title"), str) and body.get("status") in {"open", "closed"})

        try:
            for instance in ("a", "b"):
                result["checks"][f"{instance}_health"] = is_state(request(instance, "GET", "/health"), "alive")
                result["checks"][f"{instance}_ready"] = is_state(request(instance, "GET", "/ready"), "ready")
            if args.ticket_id is not None:
                first = request("a", "GET", f"/tickets/{args.ticket_id}")
                second = request("b", "GET", f"/tickets/{args.ticket_id}")
                result["checks"]["old_ticket_shared"] = (ticket(first, args.ticket_id)
                    and ticket(second, args.ticket_id) and first["response"] == second["response"])
            if args.write_cycle:
                cycle = {"title": f"dva-{args.label}-{uuid.uuid4().hex}", "created_id": None,
                         "attempted": False}
                result["write_cycle"] = cycle
                result["checks"]["cross_instance_write"] = False
                if result["checks"]["a_ready"] and result["checks"]["b_ready"]:
                    cycle["attempted"] = True
                    created = request("a", "POST", "/tickets", {"title": cycle["title"]})
                    body = created["response"]
                    valid_create = (created["status"] == 201 and created["error_type"] is None
                        and isinstance(body, dict) and type(body.get("id")) is int and body["id"] > 0
                        and body.get("title") == cycle["title"] and body.get("status") == "open")
                    if valid_create:
                        cycle["created_id"] = body["id"]
                        path = f'/tickets/{cycle["created_id"]}'
                        read_b = request("b", "GET", path)
                        changed = request("b", "PATCH", path, {"status": "closed"})
                        read_a = request("a", "GET", path)
                        result["checks"]["cross_instance_write"] = (
                            ticket(read_b, body["id"]) and read_b["response"] == body
                            and ticket(changed, body["id"]) and changed["response"]["title"] == cycle["title"]
                            and changed["response"]["status"] == "closed"
                            and ticket(read_a, body["id"]) and read_a["response"] == changed["response"])
            result["healthy_for_requested_checks"] = all(result["checks"].values())
        finally:
            result["finished_at_utc"] = utc_now()
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
    healthy = result.get("healthy_for_requested_checks", False)
    print(f"Saved {args.out}; requested healthy checks: {healthy}")
    # 1 is expected during deliberate incidents. 0 does not establish a long-term SLO.
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
