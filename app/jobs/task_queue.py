"""Persistente Worker-Spuren für Importe und schrittweise Bildarbeit."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from ..db import get_db
from ..ai_consent import CURRENT_AI_CONSENT, validate_ai_consent

logger = logging.getLogger(__name__)
_stop = threading.Event()
_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}
_wakes = {lane: threading.Event() for lane in ("imports", "images")}
_states: dict[str, dict[str, Any]] = {}
_started_at = 0.0


def enqueue(
    kind: str,
    payload: Dict[str, Any],
    *,
    dedupe_key: str | None = None,
    max_active: int | None = None,
    reserve_budget: bool = False,
) -> int:
    if CURRENT_AI_CONSENT.get():
        payload = {**payload, "ai_processing_consent": CURRENT_AI_CONSENT.get()}
    if kind == "recipe_image_generate" and payload.get("recipe_id") and "account_id" not in payload:
        recipe = get_db().recipe_get(int(payload["recipe_id"]))
        if recipe and recipe.get("owner_account_id") is not None:
            payload = {**payload, "account_id": int(recipe["owner_account_id"])}
    task_id = get_db().background_task_enqueue(
        kind,
        payload,
        dedupe_key=dedupe_key,
        max_active=max_active,
        reserve_budget=reserve_budget,
    )
    lane = "images" if kind in {"recipe_image_generate", "recipe_image_backfill"} else "imports"
    _wakes[lane].set()
    return task_id


def start_worker() -> None:
    global _started_at
    with _lock:
        alive = [thread for thread in _threads.values() if thread.is_alive()]
        if alive:
            if len(alive) == len(_wakes) and not _stop.is_set():
                return
            raise RuntimeError("Background-Worker sind noch nicht vollständig beendet")
        recovered = get_db().background_tasks_recover()
        if recovered:
            logger.warning("%s Background-Task(s) nach Neustart erneut eingereiht", recovered)
        _stop.clear()
        _started_at = time.time()
        _threads.clear()
        _states.clear()
        try:
            for lane in _wakes:
                _states[lane] = {"last_heartbeat": _started_at, "last_error": None}
                thread = threading.Thread(
                    target=_worker_loop, args=(lane,),
                    name=f"background-task-{lane}", daemon=True,
                )
                _threads[lane] = thread
                thread.start()
        except Exception:
            _stop.set()
            for wake in _wakes.values():
                wake.set()
            for thread in _threads.values():
                if thread.is_alive():
                    thread.join(timeout=1.0)
            raise


def stop_worker(timeout: float = 5.0) -> bool:
    _stop.set()
    for wake in _wakes.values():
        wake.set()
    deadline = time.monotonic() + max(0.0, timeout)
    with _lock:
        threads = list(_threads.values())
    for thread in threads:
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
    with _lock:
        for lane, thread in list(_threads.items()):
            if not thread.is_alive():
                del _threads[lane]
        return not _threads


def _worker_loop(lane: str) -> None:
    state = _states[lane]
    wake = _wakes[lane]
    while not _stop.is_set():
        state["last_heartbeat"] = time.time()
        try:
            task = get_db().background_task_claim_next(lane=lane)
            state["last_error"] = None
        except Exception as exc:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("Background-Task-Queue konnte keinen Task claimen")
            wake.wait(timeout=1.0)
            wake.clear()
            continue
        if not task:
            wake.wait(timeout=5.0)
            wake.clear()
            continue
        task_id = int(task["id"])
        try:
            result = _dispatch(task["kind"], task.get("payload") or {})
            if isinstance(result, dict) and result.get("continue"):
                get_db().background_task_continue(task_id)
                continue
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
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            try:
                get_db().background_task_finish(
                    task_id, ok=False, result={}, error=state["last_error"]
                )
            except Exception:
                logger.exception("Fehlerstatus für Background-Task #%s nicht speicherbar", task_id)


def worker_status() -> Dict[str, Any]:
    with _lock:
        lanes = {
            lane: {**_states.get(lane, {}), "running": bool(
                _threads.get(lane) and _threads[lane].is_alive()
            )}
            for lane in _wakes
        }
    return {
        "running": all(state["running"] for state in lanes.values()) and not _stop.is_set(),
        "started_at": _started_at or None,
        "last_heartbeat": max((state.get("last_heartbeat", 0) for state in lanes.values()), default=0) or None,
        "last_error": next((state["last_error"] for state in lanes.values() if state.get("last_error")), None),
        "lanes": lanes,
    }


def _dispatch(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Persisted user jobs must retain permission across restarts. Old jobs need
    # a fresh explicit action; they must not silently inherit another request.
    validate_ai_consent(payload.get("ai_processing_consent"))
    token = CURRENT_AI_CONSENT.set(payload["ai_processing_consent"])
    try:
        return _dispatch_with_consent(kind, payload)
    finally:
        CURRENT_AI_CONSENT.reset(token)


def _dispatch_with_consent(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from ..tenancy import HouseholdScope, household_context
    account_id = payload.get("account_id")
    if kind in {"recipe_image_generate", "recipe_image_backfill"} and account_id is not None:
        with get_db().conn() as c:
            members = c.execute("SELECT u.role FROM account_members m JOIN users u ON u.id=m.user_id WHERE m.account_id=? AND u.disabled=0", (account_id,)).fetchall()
        if not members:
            return {"ok": False, "error": "Haushalt ist nicht mehr aktiv"}
        with household_context(HouseholdScope(int(account_id), is_admin=any(row[0] == "admin" for row in members))):
            return _dispatch_unscoped(kind, payload)
    return _dispatch_unscoped(kind, payload)


def _dispatch_unscoped(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if kind == "share_ingest":
        from ..routes.api_share import run_share_ingest_task
        return run_share_ingest_task(payload)
    if kind == "recipe_image_generate":
        from ..recipes.image_generation import generate_recipe_image
        return generate_recipe_image(
            int(payload["recipe_id"]), batch_id=payload.get("batch_id"), queued=True,
            replace_existing=payload.get("replace_existing") is True,
        )
    if kind == "recipe_image_backfill":
        from ..recipes.image_generation import run_image_backfill
        return run_image_backfill(payload, chunk_size=1)
    raise ValueError(f"Unbekannter Background-Task: {kind}")
