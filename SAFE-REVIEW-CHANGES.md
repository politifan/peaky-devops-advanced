# Safe review changes (pending isolated execution)

This copy contains proposed fixes. The original `source-advanced` is unchanged.

- Operational Python entry points fail before side effects under `-O`/`PYTHONOPTIMIZE`; HTTP contract checks additionally use explicit exceptions. Pytest test assertions remain ordinary pytest assertions.
- Supplied load profiles require `http://127.0.0.1:18220` or `:18230`, verify the resolved session host before each request, and reject redirects. Wrappers validate CLI/env host before setup, call the bound-cluster guard, and bind the Locust UI to loopback. Start the UI with an explicit allowed `--host`; changing its host does not bypass the profile check. Custom user-provided locustfiles are outside this protection.
- Run `./lab guard` immediately before the mutable command blocks in Helm/security/monitoring/recovery runbooks. Stop on failure; do not automatically rebind. This protects sequential exercises, not concurrent malicious kubeconfig changes.
- Every build gets `evidence/build-<uuid>/` with its own manifest, HTTP check, image and pytest report. The printed directory is the artifact input for deploy. `evidence/latest-build.txt` is only a replaceable local pointer to the last successful build, not proof of provenance. A failed build does not update it; never deploy from that pointer after a failed command.
- Local builds reject tracked or untracked changes to the build inputs. Commit those changes first. Unrelated infra/evidence work is allowed. Run from a normal Git checkout and avoid concurrent edits during a build; this check is not a cryptographic attestation or an atomic source snapshot.
- Downloaded `checked-image` artifacts now contain `evidence/build-<uuid>/manifest.json` and `image.tar`. Locate the one attempt directory from the selected successful run and pass that directory to `./lab deploy --artifact DIR --commit SHA`. Old instructions that use `evidence/ci/evidence/manifest.json` must use this attempt directory instead. No workflow was dispatched by this patch.

## Restore boundary

`backup`/`restore` only demonstrate restoring SQL into a **new namespace in the same still-bound single-node cluster**. The source namespace and its three Secrets must still exist, carry the course ownership label, and the recorded image must already be present in the node CRI store. Preflight now checks these before creating the destination namespace. Keep writers stopped during backup and retain the original stage deployment recovery steps.

The backup directory alone is insufficient after loss of the namespace, Secrets, registry/image, or cluster. This patch does not implement full disaster recovery, regenerate credentials, rebind a replacement cluster, or promise cluster-loss RPO/RTO. No credentials are exported into evidence. An operational restore failure after creation can leave a partial destination; inspect it and choose a fresh destination for a later attempt, without deleting data automatically.

## Isolated checks

No local Python executable was available during preparation. Tests have **not** been executed here. On an isolated GitHub-hosted Ubuntu runner with Python 3.12, run:

```sh
python -m unittest discover -s safety_tests -v
```

These tests use only stdlib, temporary directories, and mock HTTP/subprocess boundaries. They do not install dependencies, start Docker, access production, or use the network. Full laboratory execution still requires a separate authorized disposable environment.
