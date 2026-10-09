#!/usr/bin/env bash
set -euo pipefail
test -s /run/training/authorized_key
mkdir -p /run/sshd
install -d -m 0700 -o lab -g lab /home/lab/.ssh
install -m 0600 -o lab -g lab /run/training/authorized_key /home/lab/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e
