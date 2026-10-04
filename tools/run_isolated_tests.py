"""Run local pytest against temporary paths and disabled external integrations.

Usage: .venv/Scripts/python.exe tools/run_isolated_tests.py -q
Additional pytest arguments select tests or enable coverage as usual.
"""
from pathlib import Path
import os
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    from tools.test_sandbox import install_test_environment
    sandbox = install_test_environment(root)

    # Even code tested outside the DB fixture must not open the configured archive.
    from app.db import Database
    Database.__init__.__defaults__ = (sandbox / "fallback.db",)
    import pytest
    print(f"Isolated test artifacts: {sandbox}", flush=True)
    return pytest.main([
        *sys.argv[1:], "-p", "no:cacheprovider", "--basetemp", str(sandbox / "pytest"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
