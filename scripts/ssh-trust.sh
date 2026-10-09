#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime
test ! -s .runtime/known_hosts || { echo 'known_hosts already contains observations; compare keys before replacing it'; exit 1; }
for pair in a:22220 b:22221; do
 name=${pair%:*};port=${pair#*:}
 test "$(docker inspect "dva-node-$name" --format '{{index .Config.Labels "course"}}')" = dva
 success=0
 for attempt in $(seq 1 30); do
  if ssh-keyscan -T 3 -t ed25519 -p "$port" 127.0.0.1 > .runtime/scan-key 2>/dev/null; then success=1;break;fi
  sleep 1
 done
 test "$success" = 1 || { echo 'SSH is not ready';exit 1; }
 trusted=$(docker exec "dva-node-$name" ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print $2}')
 observed=$(ssh-keygen -lf .runtime/scan-key | awk '{print $2}')
 test "$trusted" = "$observed" || { echo 'Fingerprint mismatch';exit 1; }
 cat .runtime/scan-key >> .runtime/known_hosts
 echo "Node $name fingerprint independently matched: $trusted"
done
rm -f .runtime/scan-key
chmod 600 .runtime/known_hosts
