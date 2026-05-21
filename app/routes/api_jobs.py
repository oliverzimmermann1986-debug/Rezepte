"""API für Jobs: starten, Status abrufen, Logs."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
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


def _setup_job_logger(job_id: int, kind: str) -> tuple[Path, logging.Handler]:
    """Liefert einen FileHandler der dieses Job-Lauf-Logs aufzeichnet."""
    log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    return log_file, fh


def _run_scraper_thread(job_id: int):
    db = get_db()
    log_file, fh = _setup_job_logger(job_id, "scraper")
    db.job_set_log_file(job_id, str(log_file))
    try:
        logger.info(f"=== Scraper-Job {job_id} startet (Web-Trigger) ===")
        summary = scraper_job.run_job()
        db.job_finish(job_id, "ok", summary)
        logger.info(f"=== Scraper-Job {job_id} OK: {summary} ===")
    except Exception as e:
        logger.exception(f"Scraper-Job {job_id} fehlgeschlagen")
        db.job_finish(job_id, "error", {"error": str(e)})
    finally:
        logging.getLogger().removeHandler(fh)
        fh.close()
        _locks["scraper"].release()


def _run_backup_thread(job_id: int, dry_run: bool):
    db = get_db()
    log_file, fh = _setup_job_logger(job_id, "backup")
    db.job_set_log_file(job_id, str(log_file))
    try:
        logger.info(f"=== Backup-Job {job_id} startet (Web-Trigger, dry_run={dry_run}) ===")
        summary = rclone_job.run_job(dry_run=dry_run)
        db.job_finish(job_id, "ok", summary)
        logger.info(f"=== Backup-Job {job_id} OK ===")
    except Exception as e:
        logger.exception(f"Backup-Job {job_id} fehlgeschlagen")
        db.job_finish(job_id, "error", {"error": str(e)})
    finally:
        logging.getLogger().removeHandler(fh)
        fh.close()
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
