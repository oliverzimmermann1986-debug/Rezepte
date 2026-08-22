"""API für Import- und Analysejobs: starten, Status abrufen und Logs lesen."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs.locks import file_lock_or_none

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])

# Globaler Lock damit Scraper nicht 2x parallel läuft (Web-Trigger + CLI)
_locks: Dict[str, threading.Lock] = {
    "scraper": threading.Lock(),
}


def _rotate_old_logs(log_dir: Path, days: int = 30) -> None:
    """Löscht Job-Log-Files älter als ``days`` Tage. Best-effort, ignoriert Fehler."""
    if not log_dir.exists():
        return
    cutoff = time.time() - days * 86400
    patterns = ["scraper-*.log", "reanalyze-*.log"]
    deleted = 0
    for pat in patterns:
        for f in log_dir.rglob(pat):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception:
                pass
    if deleted:
        logger.info(f"Log-Rotation: {deleted} alte Files gelöscht (>{days} Tage)")


def _setup_job_logger(job_id: int, kind: str) -> tuple[Path, logging.Handler]:
    """Liefert einen FileHandler der den Job-Lauf aufzeichnet.
    Macht zusätzlich bei jedem Aufruf eine billige Log-Rotation."""
    log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    _rotate_old_logs(log_dir)
    log_file = log_dir / f"{kind}-{datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    return log_file, fh


def _run_scraper_thread(job_id: int):
    """Background-Thread. EIN try/finally für ALLES inkl. Logger-Setup
    damit der Lock bei FileHandler-Fehler nicht hängen bleibt.
    Plus file_lock_or_none als prozessübergreifender Schutz gegen
    parallelen CLI-Lauf (systemd-Timer)."""
    db = get_db()
    fh = None
    try:
        with file_lock_or_none("scraper") as flock:
            if flock is None:
                logger.warning(f"Scraper-Job {job_id}: anderer Prozess (CLI?) hält den Lock - skip")
                db.job_finish(job_id, "skipped", {
                    "error": "anderer Scraper-Prozess (CLI?) läuft bereits"
                })
                return
            try:
                log_file, fh = _setup_job_logger(job_id, "scraper")
                db.job_set_log_file(job_id, str(log_file))
                logger.info(f"=== Scraper-Job {job_id} startet (Web-Trigger) ===")
                scraper_job.reset_cancel()
                summary = scraper_job.run_job()
                status = "ok"
                if summary.get("cancelled"):
                    status = "error"
                    summary.setdefault("error", "Abgebrochen")
                db.job_finish(job_id, status, summary)
                logger.info(f"=== Scraper-Job {job_id} {status}: {summary} ===")
            except Exception as e:
                logger.exception(f"Scraper-Job {job_id} fehlgeschlagen")
                db.job_finish(job_id, "error", {"error": str(e)})
    except Exception as e:
        try:
            db.job_finish(job_id, "error", {"error": f"setup failed: {e}"})
        except Exception:
            pass
        logger.exception(f"Scraper-Job {job_id}: Setup gescheitert")
    finally:
        if fh is not None:
            try:
                logging.getLogger().removeHandler(fh)
                fh.close()
            except Exception:
                pass
        _locks["scraper"].release()


@router.post("/scraper/run")
def run_scraper():
    if not _locks["scraper"].acquire(blocking=False):
        raise HTTPException(409, "Scraper läuft bereits")
    job_id = None
    try:
        job_id = get_db().job_start("scraper")
        t = threading.Thread(target=_run_scraper_thread, args=(job_id,), daemon=True)
        t.start()
    except Exception as exc:
        # Der Worker kann den In-Process-Lock erst in seinem finally lösen,
        # wenn er tatsächlich gestartet wurde. Fehler davor dürfen keinen
        # dauerhaften 409 bis zum nächsten Prozessneustart hinterlassen.
        if job_id is not None:
            try:
                get_db().job_finish(job_id, "error", {"error": f"start failed: {exc}"})
            except Exception:
                pass
        _locks["scraper"].release()
        logger.exception("Scraper-Thread konnte nicht gestartet werden")
        raise HTTPException(500, "Scraper konnte nicht gestartet werden") from exc
    return {"ok": True, "job_id": job_id}


@router.post("/scraper/cancel")
def cancel_scraper():
    """Setzt das Cancel-Flag im Scraper. Bricht zwischen URLs ab — eine
    bereits laufende URL (Download + Analyse) wird komplett verarbeitet,
    aber keine weitere mehr gestartet."""
    if not get_db().job_running("scraper"):
        return {"ok": False, "error": "Kein laufender Scraper-Job"}
    return scraper_job.cancel_job()


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


@router.get("/scraper/progress")
def scraper_progress():
    """Live-Progress des laufenden Scraper-Jobs (aus dem Job-Log)."""
    import re
    db = get_db()
    running = db.job_running("scraper")
    if not running:
        last = db.job_list(kind="scraper", limit=1)
        return {"running": False, "last": last[0] if last else None}

    log_file = running.get("log_file")
    info = {
        "running": True,
        "job_id": running["id"],
        "started_at": float(running["started_at"]),
        "elapsed_sec": round(time.time() - float(running["started_at"])),
        "current": None,
        "total_urls": None,
        "processed": 0,
        "auto": 0,
        "pending": 0,
        "errors": 0,
    }

    if log_file and Path(log_file).exists():
        try:
            with open(log_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 16384))
                tail = f.read().decode("utf-8", errors="ignore")

            for line in tail.splitlines():
                if "Neue URLs:" in line:
                    m = re.search(r'Neue URLs:\s*(\d+)', line)
                    if m:
                        info["total_urls"] = int(m.group(1))
            for line in reversed(tail.splitlines()):
                if "Verarbeite" in line or "→ Pending" in line or "→ AUTO" in line:
                    info["current"] = line.strip()[-200:]
                    break
        except Exception as e:
            logger.warning(f"scraper progress: {e}")

    return info


@router.get("/status/current")
def status_current():
    """Was läuft gerade? Reduziert auf scraper + reanalyze ."""
    db = get_db()
    return {
        "scraper": db.job_running("scraper"),
        "reanalyze": db.job_running("reanalyze"),
        "pending_count": db.pending_count(),
    }
