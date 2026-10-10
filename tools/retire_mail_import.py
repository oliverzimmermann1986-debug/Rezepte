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
TIMER_WRITE_OVERRIDE = Path("scrapper-web.service.d/timer-write.conf")
TIMER_WRITE_CONTENT = b"[Service]\nReadWritePaths=/etc/systemd/system/scrapper-job.timer\n"


def _validated_timer_write_override(unit_root: Path) -> Path | None:
    """Recognize only the old, single-purpose sandbox exception, never local edits."""
    path = unit_root / TIMER_WRITE_OVERRIDE
    if not path.exists() and not path.is_symlink():
        return None
    if path.parent.is_symlink() or path.parent.resolve() != path.parent:
        raise RuntimeError("Timer-write override parent requires manual review")
    info = path.lstat()
    if info.st_uid != ROOT_UID:
        raise RuntimeError("Timer-write override ownership requires manual review")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("Timer-write override file type requires manual review")
    content = path.read_bytes().replace(b"\r\n", b"\n")
    if content not in (TIMER_WRITE_CONTENT, TIMER_WRITE_CONTENT.rstrip(b"\n")):
        raise RuntimeError("Unknown timer-write override contents require manual review")
    return path


def prepare_web_service(*, unit_root=Path("/etc/systemd/system"), run=subprocess.run,
                        only_if_timer_missing=False):
    """Remove the verified legacy drop-in before the first new web-service start.

    Unit files and their enablement stay untouched until the post-start gates.
    The updater must capture this exact relative path before calling this helper.
    """
    if only_if_timer_missing and (unit_root / "scrapper-job.timer").exists():
        # Rollback to a mail-capable version still needs its sandbox exception.
        return {"ok": True, "removed_files": 0}
    target = _validated_timer_write_override(unit_root)
    if target is not None:
        target.unlink()
        run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=30, check=True)
    return {"ok": True, "removed_files": int(target is not None)}


def retire_mail_import(*, unit_root=Path("/etc/systemd/system"),
                       polkit_root=Path("/etc/polkit-1/rules.d"), run=subprocess.run):
    """Stop old entry points and unlink the exact obsolete files after checks."""
    override_dir = unit_root / "scrapper-job.timer.d"
    timer_write = _validated_timer_write_override(unit_root)
    paths = [*(unit_root / unit for unit in RETIRED_UNITS),
             override_dir / "override.conf", polkit_root / "49-scrapper-systemctl.rules"]
    if timer_write is not None:
        paths.append(timer_write)
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
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true")
    action.add_argument("--prepare-web-service", action="store_true")
    action.add_argument("--prepare-rollback", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("Linux root is required")
    try:
        if args.prepare_rollback:
            result = prepare_web_service(only_if_timer_missing=True)
        else:
            cleanup = prepare_web_service if args.prepare_web_service else retire_mail_import
            result = cleanup()
        print(json.dumps(result))
    except Exception:
        print(json.dumps({"ok": False, "error": "Mail-unit cleanup requires review"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
