"""Persistenter Einzel-Worker für Web-Hintergrundaufgaben."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from ..db import get_db

logger = logging.getLogger(__name__)
_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()
_thread: threading.Thread | None = None


def enqueue(
    kind: str,
    payload: Dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> int:
    task_id = get_db().background_task_enqueue(
        kind,
        payload,
        dedupe_key=dedupe_key,
    )
    _wake.set()
    return task_id


def start_worker() -> None:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        recovered = get_db().background_tasks_recover()
        if recovered:
            logger.warning("%s Background-Task(s) nach Neustart erneut eingereiht", recovered)
        _stop.clear()
        _thread = threading.Thread(target=_worker_loop, name="background-task-worker", daemon=True)
        _thread.start()


def stop_worker(timeout: float = 5.0) -> None:
    global _thread
    _stop.set()
    _wake.set()
    thread = _thread
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout))
    if thread and not thread.is_alive():
        _thread = None


def _worker_loop() -> None:
    while not _stop.is_set():
        task = get_db().background_task_claim_next()
        if not task:
            _wake.wait(timeout=5.0)
            _wake.clear()
            continue
        task_id = int(task["id"])
        try:
            result = _dispatch(task["kind"], task.get("payload") or {})
            ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
            get_db().background_task_finish(
                task_id,
                ok=ok,
                result=result if isinstance(result, dict) else {"result": result},
                error=None if ok else str((result or {}).get("error") or "Task fehlgeschlagen"),
            )
        except Exception as exc:
            logger.exception("Background-Task #%s (%s) fehlgeschlagen", task_id, task["kind"])
            get_db().background_task_finish(
                task_id, ok=False, result={}, error=f"{type(exc).__name__}: {exc}"
            )


def _dispatch(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if kind == "share_ingest":
        from ..routes.api_share import run_share_ingest_task
        return run_share_ingest_task(payload)
    raise ValueError(f"Unbekannter Background-Task: {kind}")
