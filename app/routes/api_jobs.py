"""API für Jobs: starten, Status abrufen, Logs."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs import scraper as scraper_job
from ..jobs import rclone_sync as rclone_job
from ..jobs.locks import file_lock_or_none

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_auth)])

# Globale Locks damit nicht 2x parallel laufen
_locks: Dict[str, threading.Lock] = {
    "scraper": threading.Lock(),
    "backup": threading.Lock(),
}


def _rotate_old_logs(log_dir: Path, days: int = 30) -> None:
    """Löscht Job-Log-Files älter als ``days`` Tage. Best-effort, ignoriert Fehler.
    Wird bei jedem Job-Start aufgerufen, daher amortisierter O(1)."""
    if not log_dir.exists():
        return
    cutoff = time.time() - days * 86400
    patterns = ["scraper-*.log", "backup-*.log", "quicksync-*.log",
                "reanalyze-*.log", "sync-*.log", "quick-*.log"]
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
    """Liefert einen FileHandler der dieses Job-Lauf-Logs aufzeichnet.
    Macht außerdem bei jedem Aufruf eine billige Log-Rotation."""
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
    """Background-Thread. WICHTIG: ein einziger try/finally umschließt ALLES
    inkl. Logger-Setup, sonst kann der Lock bei FileHandler-Fehler hängen bleiben.

    Holt zusätzlich einen ``file_lock_or_none("scraper")`` als prozessübergreifender
    Schutz gegen parallelen CLI-Lauf (systemd-Timer).
    """
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
                summary = scraper_job.run_job()
                db.job_finish(job_id, "ok", summary)
                logger.info(f"=== Scraper-Job {job_id} OK: {summary} ===")
            except Exception as e:
                logger.exception(f"Scraper-Job {job_id} fehlgeschlagen")
                db.job_finish(job_id, "error", {"error": str(e)})
    except Exception as e:
        # Logger-Setup ist geplatzt: Job direkt als error markieren
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


def _run_backup_thread(job_id: int, dry_run: bool, pairs_filter=None):
    db = get_db()
    fh = None
    try:
        with file_lock_or_none("backup") as flock:
            if flock is None:
                logger.warning(f"Backup-Job {job_id}: anderer Prozess (CLI?) hält den Lock - skip")
                db.job_finish(job_id, "skipped", {
                    "error": "anderer Backup-Prozess (CLI?) läuft bereits"
                })
                return
            try:
                log_file, fh = _setup_job_logger(job_id, "backup")
                db.job_set_log_file(job_id, str(log_file))
                logger.info(f"=== Backup-Job {job_id} startet (Web-Trigger, dry_run={dry_run}, pairs={pairs_filter}) ===")
                summary = rclone_job.run_job(dry_run=dry_run, pairs_filter=pairs_filter)
                status = "ok"
                if rclone_job.is_cancelled():
                    status = "error"
                    summary["error"] = "Abgebrochen"
                db.job_finish(job_id, status, summary)
                logger.info(f"=== Backup-Job {job_id} {status} ===")
            except Exception as e:
                logger.exception(f"Backup-Job {job_id} fehlgeschlagen")
                db.job_finish(job_id, "error", {"error": str(e)})
    except Exception as e:
        try:
            db.job_finish(job_id, "error", {"error": f"setup failed: {e}"})
        except Exception:
            pass
        logger.exception(f"Backup-Job {job_id}: Setup gescheitert")
    finally:
        if fh is not None:
            try:
                logging.getLogger().removeHandler(fh)
                fh.close()
            except Exception:
                pass
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
def run_backup(dry_run: bool = Query(False), pairs: Optional[str] = Query(None)):
    """pairs = kommagetrennte Paar-Namen, sonst alle"""
    if not _locks["backup"].acquire(blocking=False):
        raise HTTPException(409, "Backup läuft bereits")
    pairs_filter = [p.strip() for p in pairs.split(",")] if pairs else None
    job_id = get_db().job_start("backup")
    t = threading.Thread(target=_run_backup_thread, args=(job_id, dry_run, pairs_filter), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id, "pairs": pairs_filter}


@router.post("/backup/cancel")
def cancel_backup():
    if not get_db().job_running("backup"):
        return {"ok": False, "error": "Kein laufender Backup-Job"}
    result = rclone_job.cancel_job()
    return result


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


@router.get("/backup/progress")
def backup_progress():
    """Live-Progress des laufenden (oder letzten) Backup-Jobs."""
    import re
    db = get_db()
    cfg = get_config()
    running = db.job_running("backup")
    if not running:
        last = db.job_list(kind="backup", limit=1)
        return {"running": False, "last": last[0] if last else None}

    log_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs")) / "rclone"
    started = float(running["started_at"])

    # rclone "Transferred:" Zeilen können verschiedene Formen haben:
    #   Transferred:   1.234 GiB / 5.678 GiB, 22%, 30 MiB/s, ETA 2m15s
    #   Transferred:   0 B / 0 B, -, 0 B/s, ETA -
    #   Transferred:   1.2 KiB / 0, -, 0 B/s, ETA -      (ohne Größen-Total beim ersten Listing)
    # Wir matchen großzügig.
    stats_re = re.compile(
        r'Transferred:\s*([\d.]+\s*\w*)\s*/\s*([\d.]+\s*\w*)'  # x / y
        r'(?:,\s*(?:([\d.]+)\s*%|-))?'                                # , Z% oder , -
        r'(?:,\s*([\d.]+\s*\w*/s))?'                                  # , speed
        r'(?:,\s*ETA\s*([\w-]+))?'                                     # , ETA
    )
    # Datei-Zähler kommen in einer separaten Zeile (ohne "i" in KiB):
    files_re = re.compile(r'Transferred:\s+(\d+)\s*/\s*(\d+),\s*([\d.]+)\s*%')
    elapsed_re = re.compile(r'Elapsed time:\s*([\w.]+)')
    errors_re = re.compile(r'Errors:\s*(\d+)')

    pairs_status = []
    pairs_cfg = cfg.get("backup", "pairs", default=[]) or []
    for pair in pairs_cfg:
        name = pair["name"]
        # Neueste log-Datei für dieses Paar (gleicher Run)
        matches = sorted(
            log_dir.glob(f"sync-{name}-*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        active_log = None
        for m in matches:
            if m.stat().st_mtime >= started - 30:   # ±30s Toleranz
                active_log = m
                break

        pair_data = {
            "name": name,
            "remote": pair.get("remote", ""),
            "local": pair.get("local", ""),
            "log_file": str(active_log) if active_log else None,
            "status": "pending",
            "transferred": None,
            "total": None,
            "percent": None,
            "speed": None,
            "eta": None,
            "files": None,
            "files_total": None,
            "elapsed": None,
            "errors": 0,
        }

        if active_log:
            try:
                # Letzte 32 KB lesen für Stats (bisync schreibt viel)
                with open(active_log, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 32768))
                    tail = f.read().decode("utf-8", errors="ignore")

                lines = tail.splitlines()
                last_transferred_line = None

                for line in reversed(lines):
                    if "Transferred:" in line and pair_data["transferred"] is None:
                        last_transferred_line = line.strip()
                        m = stats_re.search(line)
                        if m:
                            t = m.group(1).strip() if m.group(1) else None
                            tot = m.group(2).strip() if m.group(2) else None
                            pct = float(m.group(3)) if m.group(3) else None
                            spd = m.group(4).strip() if m.group(4) else None
                            eta = m.group(5).strip() if m.group(5) else None
                            # Erst echte Byte-Stats nehmen (mit B oder i im Total),
                            # nicht Datei-Zähler
                            if tot and (("B" in tot) or ("i" in tot)):
                                pair_data.update({
                                    "transferred": t, "total": tot,
                                    "percent": pct, "speed": spd, "eta": eta,
                                    "status": "running",
                                })
                    if pair_data["files"] is None:
                        m2 = files_re.search(line)
                        if m2:
                            pair_data["files"] = int(m2.group(1))
                            pair_data["files_total"] = int(m2.group(2))
                    if pair_data["elapsed"] is None:
                        m3 = elapsed_re.search(line)
                        if m3:
                            pair_data["elapsed"] = m3.group(1).strip()
                    m4 = errors_re.search(line)
                    if m4:
                        pair_data["errors"] = max(pair_data["errors"], int(m4.group(1)))

                # Status
                lower = tail.lower()
                if "bisync successful" in lower or "completed successfully" in lower:
                    pair_data["status"] = "done"
                elif "bisync critical" in lower or "must run --resync" in lower:
                    pair_data["status"] = "needs_resync"
                elif pair_data["status"] == "pending":
                    pair_data["status"] = "running"

                # Falls Regex nicht griff aber Zeile da war: roh anzeigen
                if pair_data["transferred"] is None and last_transferred_line:
                    pair_data["raw_stats"] = last_transferred_line[-180:]
            except Exception as e:
                logger.warning(f"progress parse {active_log}: {e}")

        pairs_status.append(pair_data)

    import time
    return {
        "running": True,
        "job_id": running["id"],
        "started_at": started,
        "elapsed_sec": round(time.time() - started),
        "pairs": pairs_status,
        "total_pairs": len(pairs_cfg),
        "done_pairs": sum(1 for p in pairs_status if p["status"] == "done"),
    }


@router.get("/scraper/progress")
def scraper_progress():
    """Live-Progress des laufenden Scraper-Jobs (aus dem Job-Log)."""
    import re
    db = get_db()
    cfg = get_config()
    running = db.job_running("scraper")
    if not running:
        last = db.job_list(kind="scraper", limit=1)
        return {"running": False, "last": last[0] if last else None}

    log_file = running.get("log_file")
    info = {
        "running": True,
        "job_id": running["id"],
        "started_at": float(running["started_at"]),
        "elapsed_sec": None,
        "current": None,        # zuletzt verarbeitete URL
        "total_urls": None,
        "processed": 0,
        "auto": 0,
        "pending": 0,
        "errors": 0,
    }
    import time
    info["elapsed_sec"] = round(time.time() - info["started_at"])

    if log_file and Path(log_file).exists():
        try:
            with open(log_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 16384))
                tail = f.read().decode("utf-8", errors="ignore")

            # Counts aus Log-Zeilen extrahieren (best-effort)
            for line in tail.splitlines():
                if "Neue URLs:" in line:
                    m = re.search(r'Neue URLs:\s*(\d+)', line)
                    if m: info["total_urls"] = int(m.group(1))
                if "Job-Summary:" in line:
                    # Job ist fertig - wir sind hier eigentlich gar nicht running
                    pass
            # Letzte URL die verarbeitet wird
            for line in reversed(tail.splitlines()):
                if "Verarbeite" in line or "→ Pending" in line or "→ AUTO" in line:
                    info["current"] = line.strip()[-200:]
                    break
        except Exception as e:
            logger.warning(f"scraper progress: {e}")

    return info

@router.get("/status/current")
def status_current():
    """Was läuft gerade?"""
    db = get_db()
    return {
        "scraper": db.job_running("scraper"),
        "backup": db.job_running("backup"),
        "reanalyze": db.job_running("reanalyze"),
        "pending_count": db.pending_count(),
    }


class QuickSyncRequest(BaseModel):
    remote_path: str
    local_path: str
    direction: str = "bisync"   # pull | push | bisync
    mode: str = "bisync"         # copy | sync | bisync
    dry_run: bool = False


def _run_quick_thread(job_id: int, req: dict):
    db = get_db()
    fh = None
    try:
        with file_lock_or_none("backup") as flock:
            if flock is None:
                logger.warning(f"Quick-Sync {job_id}: anderer Backup-Prozess hält den Lock - skip")
                db.job_finish(job_id, "skipped", {
                    "error": "anderer Backup-Prozess (CLI?) läuft bereits"
                })
                return
            try:
                log_file, fh = _setup_job_logger(job_id, "quicksync")
                db.job_set_log_file(job_id, str(log_file))
                logger.info(f"=== Quick-Sync {job_id}: {req['direction']} {req['remote_path']} ⇄ {req['local_path']} ===")
                summary = rclone_job.run_quick(**req)
                status = "ok" if summary.get("ok") else "error"
                if rclone_job.is_cancelled():
                    status = "error"
                    summary["error"] = "Abgebrochen"
                db.job_finish(job_id, status, summary)
                logger.info(f"=== Quick-Sync {job_id} {status} ===")
            except Exception as e:
                logger.exception(f"Quick-Sync {job_id} crashed")
                db.job_finish(job_id, "error", {"error": str(e)})
    except Exception as e:
        try:
            db.job_finish(job_id, "error", {"error": f"setup failed: {e}"})
        except Exception:
            pass
        logger.exception(f"Quick-Sync {job_id}: Setup gescheitert")
    finally:
        if fh is not None:
            try:
                logging.getLogger().removeHandler(fh)
                fh.close()
            except Exception:
                pass
        _locks["backup"].release()


@router.post("/backup/quick")
def quick_sync(body: QuickSyncRequest):
    """One-shot Sync ohne Config-Paar."""
    if not _locks["backup"].acquire(blocking=False):
        raise HTTPException(409, "Anderer Backup-Job läuft bereits")
    if not body.remote_path or not body.local_path:
        _locks["backup"].release()
        raise HTTPException(400, "remote_path und local_path sind Pflicht")
    job_id = get_db().job_start("quicksync")
    t = threading.Thread(target=_run_quick_thread, args=(job_id, body.model_dump()), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id}
