"""File-basierte Locks zwischen Web-Trigger und systemd-CLI.

Der In-Process ``threading.Lock`` in api_jobs schützt nur den Web-Prozess.
Wenn der ``scrapper-job.timer`` feuert während ein Web-Trigger schon läuft,
würden ZWEI Scraper-Prozesse gleichzeitig E-Mails lesen und Videos laden -
plus die History/Pending-DB beschreiben. Dieses Modul schließt die Lücke
per Betriebssystem-Dateisperre, die auch über Prozessgrenzen hinweg greift.

Verwendung:
    with file_lock_or_none("scraper") as fh:
        if fh is None:
            # anderer Prozess hält den Lock - sauber rausgehen
            return
        # ... Job-Arbeit ...

Der Lock wird beim Verlassen des with-Blocks automatisch freigegeben,
auch bei Exception oder ``os._exit``.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

LOCK_DIR = Path("/opt/scrapper/data/locks")


def _try_lock(fh) -> bool:
    """Non-blocking Lock für POSIX und Windows."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock_path_or_none(
    lock_path: Path,
    *,
    wait_seconds: float = 0.0,
) -> Iterator[Optional[object]]:
    """Sperrt einen expliziten Pfad, optional mit begrenzter Wartezeit."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = None
    acquired = False
    try:
        fh = open(lock_path, "a+")
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        # msvcrt.locking benötigt ein vorhandenes Byte.
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write("\n")
            fh.flush()
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while True:
            if _try_lock(fh):
                acquired = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        if acquired:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n")
            fh.flush()
            yield fh
        else:
            try:
                other_pid = lock_path.read_text().strip()
            except Exception:
                other_pid = "?"
            logger.info("file_lock '%s': gehalten von PID %s", lock_path, other_pid)
            yield None
    finally:
        if fh is not None:
            if acquired:
                try:
                    _unlock(fh)
                except Exception:
                    pass
            try:
                fh.close()
            except Exception:
                pass


@contextmanager
def file_lock_or_none(name: str) -> Iterator[Optional[object]]:
    """Versucht non-blocking eine Datei zu locken.

    Yields:
        Das geöffnete File-Handle wenn der Lock erworben wurde,
        sonst ``None`` (Caller MUSS checken).

    Der Lock-File-Pfad ist ``{LOCK_DIR}/{name}.lock``. Das File bleibt
    zwischen Runs liegen (nur die Betriebssystem-Sperre ist transient).
    """
    lock_path = LOCK_DIR / f"{name}.lock"
    with file_lock_path_or_none(lock_path) as fh:
        yield fh


def is_locked(name: str) -> bool:
    """Probiert non-blocking, ob der Lock frei wäre. Gibt ihn sofort wieder ab.
    Achtung: zwischen Probe und Action-Aufruf kann sich der Status ändern.
    """
    with file_lock_or_none(name) as fh:
        return fh is None


def request_cancel(name: str) -> None:
    """Prozessübergreifendes Abbruchsignal im gemeinsamen Datenverzeichnis."""
    marker = LOCK_DIR / f"{name}.cancel"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{os.getpid()}\n", encoding="ascii")


def clear_cancel(name: str) -> None:
    (LOCK_DIR / f"{name}.cancel").unlink(missing_ok=True)


def cancel_requested(name: str) -> bool:
    return (LOCK_DIR / f"{name}.cancel").is_file()
