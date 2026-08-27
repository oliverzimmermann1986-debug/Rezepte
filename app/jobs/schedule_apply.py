"""Privilegierter, eng begrenzter Helper für den systemd-Scraper-Timer.

Der Webdienst schreibt nur einen JSON-Wunsch in sein Datenverzeichnis. Dieser
root-eigene Helper validiert ihn erneut und erzeugt ausschließlich das bekannte
Timer-Drop-in. Anwendungscode und Service-User erhalten kein Schreibrecht auf
``/etc/systemd`` und keine allgemeine daemon-reload-Berechtigung.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path


_ONCALENDAR_RE = re.compile(r"^[A-Za-z0-9:*/,\.\- ]{1,200}$")
REQUEST = Path(os.getenv(
    "SCRAPPER_SCHEDULE_REQUEST", "/opt/scrapper/data/schedule-request.json"
))
RESULT = REQUEST.with_name("schedule-result.json")
DROP_IN = Path("/etc/systemd/system/scrapper-job.timer.d/override.conf")


def _read_request() -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(REQUEST, flags)
    except OSError as exc:
        raise RuntimeError("Schedule-Request fehlt oder ist unsicher") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Schedule-Request ist kein reguläres File")
        if info.st_size > 4096:
            raise RuntimeError("Schedule-Request ist zu groß")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return json.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)


def _validate(value: object) -> str:
    text = str(value or "").strip()
    if not _ONCALENDAR_RE.fullmatch(text):
        raise ValueError("Ungültiger OnCalendar-Ausdruck")
    checked = subprocess.run(
        ["systemd-analyze", "calendar", text],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if checked.returncode != 0:
        raise ValueError((checked.stderr or checked.stdout).strip()[:300])
    return text


def _atomic_result(payload: dict) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".schedule-result-", dir=RESULT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, RESULT)
    finally:
        Path(name).unlink(missing_ok=True)


def _atomic_drop_in(content: bytes) -> None:
    DROP_IN.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".override-", dir=DROP_IN.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, DROP_IN)
    finally:
        Path(name).unlink(missing_ok=True)


def main() -> int:
    previous = DROP_IN.read_bytes() if DROP_IN.is_file() else None
    changed = False
    try:
        value = _validate(_read_request().get("scraper"))
        _atomic_drop_in(
            ("[Timer]\nOnCalendar=\n" f"OnCalendar={value}\n").encode("utf-8"),
        )
        changed = True
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
        subprocess.run(
            ["systemctl", "restart", "scrapper-job.timer"], check=True, timeout=30
        )
        _atomic_result({"ok": True, "scraper": value})
        return 0
    except Exception as exc:
        rollback_error = None
        if changed:
            try:
                if previous is None:
                    DROP_IN.unlink(missing_ok=True)
                else:
                    _atomic_drop_in(previous)
                subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
                subprocess.run(
                    ["systemctl", "restart", "scrapper-job.timer"],
                    check=True,
                    timeout=30,
                )
            except Exception as rollback_exc:
                rollback_error = f"; Rollback fehlgeschlagen: {rollback_exc}"
        _atomic_result({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}{rollback_error or ''}",
        })
        return 1
    finally:
        REQUEST.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
