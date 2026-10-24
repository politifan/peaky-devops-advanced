"""Not dispatched. Exactly three guarded config rollouts with immutable image."""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from convergence import guarded_gate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--namespace', choices=['dev', 'stage'], required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    # Check identity and actual initial convergence before any mutation.
    initial = guarded_gate(root, args.namespace)
    image = json.loads((root / 'evidence/deployed-image.json').read_text())['image_ref']
    if not re.fullmatch(r'localhost:5001/dva-ticket@sha256:[0-9a-f]{64}', image):
        raise RuntimeError('Expected already verified immutable local registry image')
    source = json.loads((root / 'evidence/deployed-image.json').read_text())['commit']
    original_label = 'ci-' + source
    if not re.fullmatch(r'ci-[0-9a-f]{40}', original_label):
        raise RuntimeError('Expected original immutable source commit')
    folder = root / 'evidence' / ('routing-stress-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    folder.mkdir(exist_ok=False)
    (folder / 'initial.json').write_text(json.dumps(initial, indent=2))
    for attempt in range(1, 4):
        label = original_label + '-routing-diag-' + str(attempt)
        evidence = {'attempt': attempt, 'image': image, 'namespace': args.namespace,
                    'source_commit': source, 'original_release_label': original_label,
                    'diagnostic_release_label': label,
                    'disposable_cluster_only': True}
        try:
            # releaseLabel changes ConfigMap checksum -> pod template revision.
            command = ['helm', '--kube-context', 'kind-dva-course', 'upgrade', 'ticket-api', str(root / 'chart'), '-n', args.namespace, '--reuse-values', '--set-string', 'image.ref=' + image, '--set-string', 'releaseLabel=' + label, '--atomic', '--wait', '--timeout', '180s']
            subprocess.run(command, check=True, timeout=190)
            evidence['gate'] = guarded_gate(root, args.namespace, folder / ('gate-' + str(attempt) + '.jsonl'))
            # No hook POST runs until gate passes. Existing hook's POST policy unchanged.
            subprocess.run(['helm', '--kube-context', 'kind-dva-course', 'test', 'ticket-api', '-n', args.namespace, '--timeout', '60s', '--logs'], check=True, timeout=70)
            evidence['passed'] = True
        except BaseException as error:
            evidence['passed'] = False
            evidence['error_type'] = type(error).__name__
            raise
        finally:
            (folder / ('attempt-' + str(attempt) + '.json')).write_text(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
