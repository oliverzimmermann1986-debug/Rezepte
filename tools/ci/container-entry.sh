#!/usr/bin/env bash
# Same snapshot boundary as the central service; no credentials or host mounts.
set -euo pipefail
set +x
umask 077
export HOME=/work/home TMPDIR=/work/tmp CI=true HUSKY=0
mkdir -p "$HOME" "$TMPDIR"
[[ "${PRUEFDIENST_PROFILE}" == rezepte && "${PRUEFDIENST_POSTGRES}" == 0 ]]
git config --global --add safe.directory /snapshot
git clone --no-hardlinks /snapshot /work/repo
cd /work/repo
git -c advice.detachedHead=false checkout --detach "$PRUEFDIENST_SHA"
git remote set-url origin /snapshot
rm -rf reports/ci
rm -f reports/unified-quality-report.json
exec bash -c "$1"
