"""Retire only the former mail-import units; never inspect application data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess


ROOT_UID = 0

RETIRED_UNITS = (
    "scrapper-job.timer", "scrapper-job.service", "scrapper-schedule-apply.service",
)


def retire_mail_import(*, unit_root=Path("/etc/systemd/system"),
                       polkit_root=Path("/etc/polkit-1/rules.d"), run=subprocess.run):
    """Stop old entry points and unlink the exact obsolete files after checks."""
    override_dir = unit_root / "scrapper-job.timer.d"
    paths = [*(unit_root / unit for unit in RETIRED_UNITS),
             override_dir / "override.conf", polkit_root / "49-scrapper-systemctl.rules"]
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        if path.parent.is_symlink() or path.parent.resolve() != path.parent:
            raise RuntimeError("Retired-unit parent requires manual review")
        info = path.lstat()
        if info.st_uid != ROOT_UID:
            raise RuntimeError("Retired-unit ownership requires manual review")
        if stat.S_ISLNK(info.st_mode):
            if path.name not in RETIRED_UNITS or os.readlink(path) != "/dev/null":
                raise RuntimeError("Unexpected retired-unit symbolic link")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Retired-unit file type requires manual review")
    if (override_dir.exists() or override_dir.is_symlink()) and (
            override_dir.is_symlink() or not override_dir.is_dir()):
        raise RuntimeError("Retired-unit override directory requires manual review")

    for unit in RETIRED_UNITS:
        result = run(["systemctl", "show", unit, "--property=LoadState", "--value"],
                     capture_output=True, text=True, timeout=20, check=False)
        if result.stdout.strip() != "not-found":
            if result.returncode or not result.stdout.strip():
                raise RuntimeError("Cannot determine retired-unit state")
            run(["systemctl", "disable", "--now", unit],
                capture_output=True, text=True, timeout=60, check=True)
        active = run(["systemctl", "is-active", "--quiet", unit],
                     capture_output=True, text=True, timeout=20, check=False)
        if active.returncode == 0:
            raise RuntimeError("A retired mail-import unit is still active")

    removed = 0
    for path in paths:
        if path.exists() or path.is_symlink():
            path.unlink()
            removed += 1
    if override_dir.exists() and not any(override_dir.iterdir()):
        override_dir.rmdir()
    run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=30, check=True)
    return {"ok": True, "mail_units_inactive": True, "removed_files": removed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("Pass --apply to retire the old mail-import units")
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("Linux root is required")
    try:
        print(json.dumps(retire_mail_import()))
    except Exception:
        print(json.dumps({"ok": False, "error": "Mail-unit cleanup requires review"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
