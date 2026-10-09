# Verify that the calling Ubuntu operates its own bound, local kind cluster.
import argparse
import json
from pathlib import Path
import subprocess
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "kind-dva-course"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def k(*args, **kwargs):
    return run("kubectl", "--context", CONTEXT, *args, **kwargs)


def identity():
    node_names = run("kind", "get", "nodes", "--name", "dva-course", capture_output=True, text=True).stdout.split()
    if node_names != ["dva-course-control-plane"]:
        raise RuntimeError("Expected the single-node training cluster")
    node = json.loads(run("docker", "inspect", node_names[0], capture_output=True, text=True).stdout)[0]
    if node["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "dva-course":
        raise RuntimeError("Docker node label mismatch")
    config = json.loads(k("config", "view", "--minify", "-o", "json", capture_output=True, text=True).stdout)
    server = config["clusters"][0]["cluster"]["server"]
    url = urllib.parse.urlsplit(server)
    port = int(node["NetworkSettings"]["Ports"]["6443/tcp"][0]["HostPort"])
    if url.scheme != "https" or url.hostname not in {"127.0.0.1", "localhost"} or url.port != port:
        raise RuntimeError("Kubeconfig does not point to this local Docker node")
    ns = json.loads(k("get", "namespace", "kube-system", "-o", "json", capture_output=True, text=True).stdout)
    return {"context": CONTEXT, "server": server, "kube_system_uid": ns["metadata"]["uid"]}


def guard():
    stored = json.loads((ROOT / ".runtime" / "cluster-id.json").read_text())
    if identity() != stored:
        raise RuntimeError("Training cluster changed; inspect before binding a new identity")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", action="store_true")
    args = parser.parse_args()
    if args.bind:
        value = identity()
        path = ROOT / ".runtime" / "cluster-id.json"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
        print("Bound this local training cluster")
    else:
        guard()
        print("Training cluster identity matches")
