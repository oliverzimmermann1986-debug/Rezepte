"""API für Pending-Items: Auflisten, Vorschau, Auflösen."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs.scraper import get_scraper_job

router = APIRouter(prefix="/api/pending", tags=["pending"], dependencies=[Depends(require_auth)])


def _is_under_temp(path_str: str) -> bool:
    """Defense-in-depth: nur Pfade unter temp_dir erlauben."""
    if not path_str:
        return False
    try:
        p = Path(path_str).resolve()
        temp_root = Path(
            get_config().get("paths", "temp_dir", default="/opt/scrapper/temp")
        ).resolve()
        p.relative_to(temp_root)
        return True
    except (ValueError, OSError):
        return False


@router.get("")
def list_pending(status: str = "pending", sort: str = "newest") -> List[Dict[str, Any]]:
    return get_db().pending_list(status=status, sort=sort)


class BulkSkipBody(BaseModel):
    urls: List[str]


@router.post("/bulk-skip")
def bulk_skip(body: BulkSkipBody) -> Dict[str, Any]:
    """Mehrere Pending-Items in einem Rutsch überspringen.
    Schreibt sie in die History als '(skipped)', löscht das stash-Video,
    und markiert sie als status='skipped'.
    """
    db = get_db()
    job = get_scraper_job()
    skipped = 0
    errors = []
    for url in body.urls:
        try:
            r = job.resolve_pending(url, {"action": "skip"})
            if r.get("ok"):
                skipped += 1
            else:
                errors.append({"url": url, "error": r.get("error", "unknown")})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})
    return {"ok": True, "skipped": skipped, "errors": errors,
            "total_requested": len(body.urls)}


# /preview Endpoint wurde entfernt - Frame-Extraktion ist raus.
# Das Frontend zeigt automatisch den "kein Vorschaubild"-Placeholder,
# wenn der GET 404 zurückgibt (siehe @error-Handler in index.html).


@router.get("/video")
def video_file(url: str):
    entry = get_db().pending_get(url)
    if not entry:
        raise HTTPException(404, "Nicht gefunden")
    video = entry.get("video_path")
    if not video or not _is_under_temp(video) or not Path(video).exists():
        raise HTTPException(404, "Video nicht verfügbar")
    return FileResponse(video, media_type="video/mp4")


class ResolveBody(BaseModel):
    url: str
    action: str                   # 'save' | 'skip'
    name: Optional[str] = None
    type: Optional[str] = None    # für Rezept
    category: Optional[str] = None


@router.post("")
def resolve(body: ResolveBody):
    if body.action not in ("save", "skip"):
        raise HTTPException(400, "action muss 'save' oder 'skip' sein")
    decision = {
        "action": body.action,
        "name": body.name,
        "type": body.type,
        "category": body.category,
    }
    return get_scraper_job().resolve_pending(body.url, decision)


class ReanalyzeRequest(BaseModel):
    url: str


@router.post("/reanalyze")
def reanalyze(body: ReanalyzeRequest):
    """Lässt ein Pending-Item neu durch die KI-Cascade laufen."""
    return get_scraper_job().reanalyze_pending(body.url)


import logging as _logging
import threading as _threading
from datetime import datetime as _datetime

_logger = _logging.getLogger(__name__)
_reanalyze_lock = _threading.Lock()


def _reanalyze_all_thread(job_id: int):
    """Background-Worker. Schreibt Progress in eine Log-Datei + bei jedem
    Item updaten wir die summary in der jobs-Tabelle damit das Frontend
    Live-Progress sehen kann.
    
    WICHTIG: ein einziger try/finally umschließt ALLES inkl. Logger-Setup,
    sonst kann der Lock bei FileHandler-Fehler hängen bleiben.
    """
    from ..config_store import get_config as _gc
    db = get_db()
    fh = None
    summary = {
        "total": 0, "auto_saved": 0, "still_pending": 0, "errors": 0,
        "processed": 0, "current": None,
    }
    try:
        log_dir = Path(_gc().get("paths", "logs_dir", default="/opt/scrapper/logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"reanalyze-{_datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
        fh = _logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(_logging.INFO)
        fh.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _logging.getLogger().addHandler(fh)
        db.job_set_log_file(job_id, str(log_file))

        job = get_scraper_job()
        try:
            items = db.pending_list("pending")
            summary["total"] = len(items)
            _logger.info(f"=== Pending-Reanalyze {job_id} startet: {summary['total']} Items ===")
            for item in items:
                url = item["url"]
                summary["current"] = url
                db.job_update_summary(job_id, summary)
                try:
                    r = job.reanalyze_pending(url)
                    if not r.get("ok"):
                        summary["errors"] += 1
                        _logger.warning(f"FEHLER {url}: {r.get('error')}")
                    elif r.get("action") == "auto_saved":
                        summary["auto_saved"] += 1
                        _logger.info(f"AUTO-SAVE {url} → {r.get('target')}")
                    else:
                        summary["still_pending"] += 1
                        _logger.info(f"STILL-PENDING {url} (conf={(r.get('analysis') or {}).get('confidence')})")
                except Exception:
                    summary["errors"] += 1
                    _logger.exception(f"Exception {url}")
                summary["processed"] += 1
                db.job_update_summary(job_id, summary)

            summary["current"] = None
            db.job_finish(job_id, "ok", summary)
            _logger.info(f"=== Pending-Reanalyze {job_id} fertig: {summary} ===")
        except Exception as e:
            _logger.exception("Reanalyze-Job crashed")
            db.job_finish(job_id, "error", {"error": str(e), **summary})
    except Exception as e:
        try:
            db.job_finish(job_id, "error", {"error": f"setup failed: {e}", **summary})
        except Exception:
            pass
        _logger.exception(f"Reanalyze-Job {job_id}: Setup gescheitert")
    finally:
        if fh is not None:
            try:
                _logging.getLogger().removeHandler(fh)
                fh.close()
            except Exception:
                pass
        _reanalyze_lock.release()


@router.post("/reanalyze-all")
def reanalyze_all():
    """Startet Background-Job der alle Pending-Items neu analysiert."""
    if not _reanalyze_lock.acquire(blocking=False):
        raise HTTPException(409, "Reanalyze läuft bereits")
    job_id = get_db().job_start("reanalyze")
    t = _threading.Thread(target=_reanalyze_all_thread, args=(job_id,), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id}


@router.get("/reanalyze/progress")
def reanalyze_progress():
    db = get_db()
    running = db.job_running("reanalyze")
    if not running:
        last = db.job_list(kind="reanalyze", limit=1)
        return {"running": False, "last": last[0] if last else None}
    import time as _t
    return {
        "running": True,
        "job_id": running["id"],
        "started_at": float(running["started_at"]),
        "elapsed_sec": round(_t.time() - float(running["started_at"])),
        "summary": running.get("summary") or {},
    }


# ---------------- Failed Downloads (Email Recovery) ----------------

@router.get("/failed")
def list_failed_downloads(limit: int = 100) -> List[Dict[str, Any]]:
    """Liste aller URLs, deren Download mehrfach fehlgeschlagen ist.

    Werden vom Scraper nach MAX_DOWNLOAD_ATTEMPTS (default 3) übersprungen.
    Diese Liste zeigt sie, damit der User entscheiden kann was tun:
    - Retry-Counter zurücksetzen (URL wird beim nächsten Mail-Sync neu versucht)
    - Komplett aus dem Failed-Tracking löschen
    """
    return get_db().download_failures_list(limit=limit)


@router.post("/failed/{url:path}/retry")
def retry_failed(url: str) -> Dict[str, Any]:
    """Setzt den Failure-Counter zurück (Zeile bleibt erhalten).

    Der nächste Scraper-Lauf nimmt die URL als Retry-Kandidat direkt aus
    download_failures auf — die Quell-Mail wird NICHT mehr benötigt
    (verarbeitete Mails werden gelöscht, wenn delete_processed aktiv ist).
    """
    get_db().download_failure_reset(url)
    return {"ok": True, "url": url, "reset": True}


@router.post("/failed/{url:path}/discard")
def discard_failed(url: str) -> Dict[str, Any]:
    """Verwirft eine endgültig fehlgeschlagene URL dauerhaft.

    Schreibt sie als '(verworfen)' in die History (→ Mail-Sync überspringt
    sie ab jetzt, auch wenn die Mail im Postfach bleibt) und entfernt den
    Failure-Eintrag. Bewusste User-Entscheidung — das frühere automatische
    History-Schreiben nach MAX Versuchen wurde entfernt.
    """
    db = get_db()
    db.history_add(url, content_type="recipe", name="(verworfen)")
    db.download_failure_clear(url)
    return {"ok": True, "url": url, "discarded": True}


@router.post("/failed/clear-all")
def clear_all_failed() -> Dict[str, Any]:
    """Alle Failure-Counter löschen. Bei nächstem Mail-Sync werden alle
    noch in Mails enthaltenen URLs nochmal versucht."""
    count = get_db().download_failures_clear_all()
    return {"ok": True, "cleared": count}
