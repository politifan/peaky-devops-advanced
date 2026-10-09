# Create new namespace-scoped training secrets via stdin; never overwrite.
import argparse
import json
import secrets
import string
from guard import guard, k


def password():
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("namespace", choices=["dev", "stage"])
    args = parser.parse_args()
    guard()
    ns = json.loads(k("get", "ns", args.namespace, "-o", "json", capture_output=True, text=True).stdout)
    if ns["metadata"].get("labels", {}).get("course") != "dva":
        raise RuntimeError("Namespace is not labelled for this course")
    existing = json.loads(k("-n", args.namespace, "get", "secrets", "-o", "json", capture_output=True, text=True).stdout)
    names = {item["metadata"]["name"] for item in existing["items"]}
    if names.intersection({"dva-db-admin", "dva-migrate-db", "dva-app-db"}):
        raise RuntimeError("Training secrets already exist; no replacement performed")
    admin, app = password(), password()
    url = f"postgresql+psycopg://ticket_app:{app}@postgres:5432/ticket_lab"
    items = []
    for name, data in (("dva-db-admin", {"password": admin}),
                       ("dva-migrate-db", {"password": app, "username": "ticket_app", "DATABASE_URL": url}),
                       ("dva-app-db", {"password": app, "username": "ticket_app", "DATABASE_URL": url})):
        items.append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": args.namespace},
                      "type": "Opaque", "stringData": data})
    k("create", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}), text=True)
    print("New secrets created; their values are not evidence for submission")


if __name__ == "__main__":
    main()
