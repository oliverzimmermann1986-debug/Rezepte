"""API für Import-Jobs, Status und Logs."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs.locks import file_lock_or_none
from ..path_utils import ensure_within

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])

_scraper_lock = threading.Lock()


def _rotate_old_logs(log_dir: Path, days: int = 30) -> None:
    if not log_dir.exists():
        return
    cutoff = time.time() - max(1, min(int(days), 3650)) * 86400
    deleted = 0
    for pattern in ("scraper-*.log", "reanalyze-*.log"):
        for file in log_dir.rglob(pattern):
            try:
                if file.is_file() and file.stat().st_mtime < cutoff:
                    file.unlink()
                    deleted += 1
            except OSError:
                continue
    if deleted:
        logger.info("Log-Rotation: %s alte Dateien gelöscht", deleted)


def _setup_job_logger(job_id: int, kind: str) -> tuple[Path, logging.Handler]:
    cfg = get_config()
    log_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    _rotate_old_logs(log_dir, int(cfg.get("paths", "log_retention_days", default=30) or 30))
    log_file = log_dir / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return log_file, handler


def _run_scraper_thread(job_id: int) -> None:
    db = get_db()
    handler = None
    try:
        with file_lock_or_none("scraper") as process_lock:
            if process_lock is None:
                db.job_finish(job_id, "skipped", {"error": "Ein anderer Import läuft bereits"})
                return
            log_file, handler = _setup_job_logger(job_id, "scraper")
            db.job_set_log_file(job_id, str(log_file))
            logger.info("=== Import-Job %s startet ===", job_id)
            scraper_job.reset_cancel()
            summary = scraper_job.run_job()
            status = "ok"
            if summary.get("cancelled"):
                status = "error"
                summary.setdefault("error", "Abgebrochen")
            elif int(summary.get("errors", 0) or 0) > 0:
                status = "partial"
            db.job_finish(job_id, status, summary)
            logger.info("=== Import-Job %s %s: %s ===", job_id, status, summary)
    except Exception as exc:
        logger.exception("Import-Job %s fehlgeschlagen", job_id)
        try:
            db.job_finish(job_id, "error", {"error": str(exc)})
        except Exception:
            logger.exception("Jobstatus konnte nicht gespeichert werden")
    finally:
        if handler is not None:
            try:
                logging.getLogger().removeHandler(handler)
                handler.close()
            except Exception:
                pass
        _scraper_lock.release()


@router.post("/scraper/run")
def run_scraper():
    if not _scraper_lock.acquire(blocking=False):
        raise HTTPException(409, "Import läuft bereits")
    job_id = get_db().job_start("scraper")
    threading.Thread(target=_run_scraper_thread, args=(job_id,), daemon=True).start()
    return {"ok": True, "job_id": job_id}


@router.post("/scraper/cancel")
def cancel_scraper():
    if not get_db().job_running("scraper"):
        return {"ok": False, "error": "Kein laufender Import"}
    return scraper_job.cancel_job()


@router.get("/list")
def list_jobs(kind: Optional[str] = None, limit: int = Query(50, ge=1, le=1000)):
    return get_db().job_list(kind=kind, limit=limit)


@router.post("/cleanup-failed")
def cleanup_failed_jobs():
    return {"ok": True, "deleted": get_db().jobs_delete_failed()}


@router.get("/scraper/progress")
def scraper_progress():
    db = get_db()
    running = db.job_running("scraper")
    if not running:
        latest = db.job_list(kind="scraper", limit=1)
        return {"running": False, "last": latest[0] if latest else None}

    started_at = float(running["started_at"])
    info = {
        "running": True,
        "job_id": running["id"],
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at),
        "current": None,
        "total_urls": None,
    }
    log_file = running.get("log_file")
    if not log_file:
        return info
    try:
        logs_root = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
        path = ensure_within(Path(log_file), logs_root)
        if path.is_file():
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 16384))
                tail = handle.read().decode("utf-8", errors="ignore")
            import re
            for line in tail.splitlines():
                match = re.search(r"Neue URLs:\s*(\d+)", line)
                if match:
                    info["total_urls"] = int(match.group(1))
            for line in reversed(tail.splitlines()):
                if "Verarbeite" in line or "→ Pending" in line or "→ AUTO" in line:
                    info["current"] = line.strip()[-200:]
                    break
    except (OSError, ValueError) as exc:
        logger.warning("Import-Fortschritt nicht lesbar: %s", exc)
    return info


@router.get("/status/current")
def status_current():
    db = get_db()
    return {
        "scraper": db.job_running("scraper"),
        "reanalyze": db.job_running("reanalyze"),
        "pending_count": db.pending_count(),
    }


@router.get("/{job_id}")
def job_detail(job_id: int):
    job = get_db().job_get(job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    return job


@router.get("/{job_id}/log")
def job_log(job_id: int, tail: int = Query(500, ge=1, le=5000)):
    job = get_db().job_get(job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    log_file = job.get("log_file")
    if not log_file:
        return {"log": ""}
    try:
        logs_root = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
        path = ensure_within(Path(log_file), logs_root)
        if not path.is_file():
            return {"log": ""}
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return {"log": "".join(handle.readlines()[-tail:])}
    except ValueError:
        raise HTTPException(403, "Log-Pfad außerhalb des erlaubten Verzeichnisses")
    except OSError as exc:
        return {"log": f"<Fehler: {exc}>"}
