"""Persistenter Einzel-Worker für Web-Hintergrundaufgaben."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from ..db import get_db

logger = logging.getLogger(__name__)
_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()
_thread: threading.Thread | None = None
_last_heartbeat = 0.0
_last_error: str | None = None
_started_at = 0.0


def enqueue(
    kind: str,
    payload: Dict[str, Any],
    *,
    dedupe_key: str | None = None,
    max_active: int | None = None,
) -> int:
    task_id = get_db().background_task_enqueue(
        kind,
        payload,
        dedupe_key=dedupe_key,
        max_active=max_active,
    )
    _wake.set()
    return task_id


def start_worker() -> None:
    global _thread, _last_error, _last_heartbeat, _started_at
    with _lock:
        if _thread and _thread.is_alive():
            return
        recovered = get_db().background_tasks_recover()
        if recovered:
            logger.warning("%s Background-Task(s) nach Neustart erneut eingereiht", recovered)
        _stop.clear()
        _last_error = None
        _last_heartbeat = time.time()
        _started_at = _last_heartbeat
        _thread = threading.Thread(target=_worker_loop, name="background-task-worker", daemon=True)
        _thread.start()


def stop_worker(timeout: float = 5.0) -> bool:
    global _thread
    _stop.set()
    _wake.set()
    thread = _thread
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    if thread and not thread.is_alive():
        _thread = None
    return not (thread and thread.is_alive())


def _worker_loop() -> None:
    global _last_error, _last_heartbeat
    while not _stop.is_set():
        _last_heartbeat = time.time()
        try:
            task = get_db().background_task_claim_next()
            _last_error = None
        except Exception as exc:
            _last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Background-Task-Queue konnte keinen Task claimen")
            _wake.wait(timeout=1.0)
            _wake.clear()
            continue
        if not task:
            _wake.wait(timeout=5.0)
            _wake.clear()
            continue
        task_id = int(task["id"])
        try:
            result = _dispatch(task["kind"], task.get("payload") or {})
            if isinstance(result, dict) and result.get("retry"):
                attempts = int(task.get("attempts") or 1)
                if attempts < 12:
                    delay = min(300, 5 * (2 ** min(attempts, 6)))
                    get_db().background_task_retry(
                        task_id,
                        delay_seconds=delay,
                        error=str(result.get("error") or "Vorübergehend nicht verfügbar"),
                        result=result,
                    )
                    logger.info(
                        "Background-Task #%s in %ss erneut (Versuch %s/12)",
                        task_id, delay, attempts,
                    )
                    continue
                result = {
                    **result,
                    "retry": False,
                    "error": str(result.get("error") or "Vorübergehend nicht verfügbar")
                    + " — maximale Wiederholungen erreicht",
                }
            ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
            get_db().background_task_finish(
                task_id,
                ok=ok,
                result=result if isinstance(result, dict) else {"result": result},
                error=None if ok else str((result or {}).get("error") or "Task fehlgeschlagen"),
            )
        except Exception as exc:
            logger.exception("Background-Task #%s (%s) fehlgeschlagen", task_id, task["kind"])
            _last_error = f"{type(exc).__name__}: {exc}"
            try:
                get_db().background_task_finish(
                    task_id, ok=False, result={}, error=_last_error
                )
            except Exception:
                logger.exception("Fehlerstatus für Background-Task #%s nicht speicherbar", task_id)


def worker_status() -> Dict[str, Any]:
    thread = _thread
    return {
        "running": bool(thread and thread.is_alive()),
        "started_at": _started_at or None,
        "last_heartbeat": _last_heartbeat or None,
        "last_error": _last_error,
    }


def _dispatch(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if kind == "share_ingest":
        from ..routes.api_share import run_share_ingest_task
        return run_share_ingest_task(payload)
    raise ValueError(f"Unbekannter Background-Task: {kind}")
