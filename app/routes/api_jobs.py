"""Status und historische Protokolle expliziter Import- und Analysejobs."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_admin
from ..db import get_db


router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])

@router.get("/list")
def list_jobs(kind: Optional[str] = None, limit: int = 50):
    return get_db().job_list(kind=kind, limit=limit)


@router.post("/cleanup-failed")
def cleanup_failed_jobs():
    """Löscht alle Job-Einträge mit Status='error'. Nur Log-Cleanup —
    es wird nichts in History oder Pending verändert."""
    deleted = get_db().jobs_delete_failed()
    return {"ok": True, "deleted": deleted}


@router.get("/tasks/list")
def list_background_tasks(limit: int = 50):
    return get_db().background_task_list(limit=limit)


@router.get("/tasks/{task_id}")
def background_task_detail(task_id: int):
    task = get_db().background_task_get(task_id)
    if not task:
        raise HTTPException(404, "Task nicht gefunden")
    return task


@router.get("/{job_id}")
def job_detail(job_id: int):
    j = get_db().job_get(job_id)
    if not j:
        raise HTTPException(404, "Nicht gefunden")
    return j


@router.get("/{job_id}/log")
def job_log(job_id: int, tail: int = 500):
    j = get_db().job_get(job_id)
    if not j:
        raise HTTPException(404, "Job nicht gefunden")
    log_file = j.get("log_file")
    if not log_file or not Path(log_file).exists():
        return {"log": ""}
    try:
        with open(log_file, "r", errors="ignore") as f:
            lines = f.readlines()[-tail:]
        return {"log": "".join(lines)}
    except Exception as e:
        return {"log": f"<Fehler: {e}>"}


@router.get("/status/current")
def status_current():
    """Status explizit gestarteter Analysen und der manuellen Prüfung."""
    db = get_db()
    return {
        "reanalyze": db.job_running("reanalyze"),
        "pending_count": db.pending_count(),
    }
