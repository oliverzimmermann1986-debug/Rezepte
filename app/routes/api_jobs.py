"""API für Jobs: starten, Status abrufen, Logs."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs import rclone_sync as rclone_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])

# Globale Locks damit nicht 2x parallel laufen
_locks: Dict[str, threading.Lock] = {
    "scraper": threading.Lock(),
    "backup": threading.Lock(),
}


def _run_scraper_thread(job_id: int):
    db = get_db()
    try:
        summary = scraper_job.run_job()
        db.job_finish(job_id, "ok", summary)
    except Exception as e:
        logger.exception(f"Scraper-Job {job_id} fehlgeschlagen")
        db.job_finish(job_id, "error", {"error": str(e)})
    finally:
        _locks["scraper"].release()


def _run_backup_thread(job_id: int, dry_run: bool):
    db = get_db()
    try:
        summary = rclone_job.run_job(dry_run=dry_run)
        db.job_finish(job_id, "ok", summary)
    except Exception as e:
        logger.exception(f"Backup-Job {job_id} fehlgeschlagen")
        db.job_finish(job_id, "error", {"error": str(e)})
    finally:
        _locks["backup"].release()


@router.post("/scraper/run")
def run_scraper():
    if not _locks["scraper"].acquire(blocking=False):
        raise HTTPException(409, "Scraper läuft bereits")
    job_id = get_db().job_start("scraper")
    t = threading.Thread(target=_run_scraper_thread, args=(job_id,), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id}


@router.post("/backup/run")
def run_backup(dry_run: bool = Query(False)):
    if not _locks["backup"].acquire(blocking=False):
        raise HTTPException(409, "Backup läuft bereits")
    job_id = get_db().job_start("backup")
    t = threading.Thread(target=_run_backup_thread, args=(job_id, dry_run), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id}


@router.get("/list")
def list_jobs(kind: Optional[str] = None, limit: int = 50):
    return get_db().job_list(kind=kind, limit=limit)


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
    """Was läuft gerade?"""
    db = get_db()
    return {
        "scraper": db.job_running("scraper"),
        "backup": db.job_running("backup"),
        "pending_count": db.pending_count(),
    }
