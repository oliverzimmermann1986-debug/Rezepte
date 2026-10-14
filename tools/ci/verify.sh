#!/usr/bin/env bash
# Executed only inside the disposable Pruefdienst container.
set -euo pipefail
python3 -m venv /work/venv
exec /work/venv/bin/python tools/ci_verify.py --install-deps
