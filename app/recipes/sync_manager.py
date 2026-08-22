"""Nicht-blockierende Koordination des Dateisystem→DB-Syncs.

Der eigentliche Scan kann auf NAS/HDD mehrere Sekunden dauern. Deshalb darf er
nicht im Request-Thread von ``GET /api/recipes`` laufen. Dieses Modul kapselt
einen einzelnen Daemon-Worker, dedupliziert parallele Requests und stellt einen
kleinen Status für UI/Monitoring bereit.
"""
from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Any, Dict, Optional

from ..db import Database, get_db
from .indexer import sync_filesystem

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_state: Dict[str, Any] = {
    "running": False,
    "queued": False,
    "reason": None,
    "requested_at": None,
    "started_at": None,
    "finished_at": None,
    "last_success_at": None,
    "result": None,
    "error": None,
    "run_id": 0,
}


def sync_status() -> Dict[str, Any]:
    """Thread-sicherer Snapshot des aktuellen/letzten Sync-Laufs."""
    with _lock:
        state = deepcopy(_state)
    if state["running"] and state["started_at"]:
        state["elapsed_seconds"] = round(max(0.0, time.time() - state["started_at"]), 1)
    else:
        state["elapsed_seconds"] = None
    return state


def request_sync(
    *,
    reason: str = "manual",
    force: bool = False,
    min_interval: float = 0.0,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Plant einen Sync ein und kehrt sofort zurück.

    ``min_interval`` verhindert wiederholte automatische Scans. Manuelle
    Aufrufe setzen ``force=True`` und umgehen dieses Zeitfenster. Läuft bereits
    ein Scan, wird kein zweiter Thread gestartet; der Status meldet
    ``accepted=False`` und ``already_running=True``.
    """
    global _thread
    now = time.time()
    with _lock:
        if _state["running"]:
            state = deepcopy(_state)
            state.update({"accepted": False, "already_running": True, "skipped": False})
            return state

        last_finished = _state.get("finished_at") or 0.0
        if not force and min_interval > 0 and last_finished and now - last_finished < min_interval:
            state = deepcopy(_state)
            state.update({
                "accepted": False,
                "already_running": False,
                "skipped": True,
                "retry_after_seconds": round(min_interval - (now - last_finished), 1),
            })
            return state

        _state.update({
            "running": True,
            "queued": True,
            "reason": reason,
            "requested_at": now,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "run_id": int(_state.get("run_id") or 0) + 1,
        })
        run_id = int(_state["run_id"])
        try:
            _thread = threading.Thread(
                target=_run_sync,
                kwargs={"run_id": run_id, "db": db},
                name=f"recipe-fs-sync-{run_id}",
                daemon=True,
            )
            _thread.start()
        except Exception as exc:
            _thread = None
            _state.update({
                "running": False,
                "queued": False,
                "finished_at": time.time(),
                "result": None,
                "error": f"thread start failed: {type(exc).__name__}: {exc}",
            })
            logger.exception("recipe filesystem sync thread could not start")
            raise
        state = deepcopy(_state)
        state.update({"accepted": True, "already_running": False, "skipped": False})
        return state


def _run_sync(*, run_id: int, db: Optional[Database]) -> None:
    started = time.time()
    with _lock:
        if run_id != _state.get("run_id"):
            return
        _state.update({"queued": False, "started_at": started})
    try:
        result = sync_filesystem(db or get_db())
    except Exception as exc:  # letzter Schutz: Status darf nie auf running hängen bleiben
        logger.exception("recipe filesystem sync failed")
        with _lock:
            if run_id == _state.get("run_id"):
                _state.update({
                    "running": False,
                    "queued": False,
                    "finished_at": time.time(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "result": None,
                })
        return

    finished = time.time()
    with _lock:
        if run_id == _state.get("run_id"):
            _state.update({
                "running": False,
                "queued": False,
                "finished_at": finished,
                "last_success_at": finished,
                "result": result,
                "error": None,
            })
    logger.info("recipe filesystem sync finished in %.2fs: %s", finished - started, result)


def wait_for_sync(timeout: float = 30.0) -> Dict[str, Any]:
    """Hilfsfunktion für Tests/CLI; Webrequests sollen sie nicht verwenden."""
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=max(0.0, timeout))
    return sync_status()


def reset_sync_state_for_tests() -> None:
    """Test-Hook ohne Produktionsverwendung."""
    global _thread
    thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=2)
    with _lock:
        _state.clear()
        _state.update({
            "running": False,
            "queued": False,
            "reason": None,
            "requested_at": None,
            "started_at": None,
            "finished_at": None,
            "last_success_at": None,
            "result": None,
            "error": None,
            "run_id": 0,
        })
    _thread = None
