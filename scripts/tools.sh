#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test "$(uname -m)" = x86_64 || { echo 'Этот комплект проверен на Linux amd64'; exit 1; }
mkdir -p .tools/bin .tools/downloads
cd .tools/downloads
curl -fsSLo tf.zip https://releases.hashicorp.com/terraform/1.13.3/terraform_1.13.3_linux_amd64.zip
curl -fsSLo tf.sums https://releases.hashicorp.com/terraform/1.13.3/terraform_1.13.3_SHA256SUMS
grep 'terraform_1.13.3_linux_amd64.zip$' tf.sums | sed 's/terraform_1.13.3_linux_amd64.zip/tf.zip/' | sha256sum -c -
unzip -o -q tf.zip -d ../bin
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64
curl -fsSLo kind.sha https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64.sha256sum
sed 's/kind-linux-amd64/kind/' kind.sha | sha256sum -c -
install -m 755 kind ../bin/kind
curl -fsSLo kubectl https://dl.k8s.io/release/v1.34.0/bin/linux/amd64/kubectl
curl -fsSLo kubectl.sha https://dl.k8s.io/release/v1.34.0/bin/linux/amd64/kubectl.sha256
printf '%s  kubectl\n' "$(cat kubectl.sha)" | sha256sum -c -
install -m 755 kubectl ../bin/kubectl
curl -fsSLo helm.tgz https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz
printf '%s  helm.tgz\n' a7f81ce08007091b86d8bd696eb4d86b8d0f2e1b9f6c714be62f82f96a594496 | sha256sum -c -
tar -xzf helm.tgz linux-amd64/helm
install -m 755 linux-amd64/helm ../bin/helm
echo 'Проверенные инструменты готовы. В каждом новом терминале: export PATH="$PWD/.tools/bin:$PATH"'
