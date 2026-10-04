"""Installer integration uses real Linux venvs and synthetic service/download boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="isolated Linux installer ownership test requires root")
def test_installer_rollbacks_launcher_and_sigterm_without_real_downloads():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(project / "tools/probe_video_archiver_install.py")],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    checks = json.loads(result.stdout)
    assert checks and all(checks.values()), checks
