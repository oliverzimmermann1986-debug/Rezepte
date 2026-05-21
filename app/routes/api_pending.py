"""API für Pending-Items: Auflisten, Vorschau, Auflösen."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs.scraper import ScraperJob

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
def list_pending(status: str = "pending") -> List[Dict[str, Any]]:
    return get_db().pending_list(status=status)


@router.get("/preview")
def preview_file(url: str):
    """Liefert das Frame-Bild eines Pending-Eintrags zurück."""
    entry = get_db().pending_get(url)
    if not entry:
        raise HTTPException(404, "Nicht gefunden")

    frame = entry.get("frame_path")
    if frame and _is_under_temp(frame) and Path(frame).exists():
        return FileResponse(frame, media_type="image/jpeg")

    # on-the-fly Frame-Extraktion aus dem Video
    video = entry.get("video_path")
    if video and _is_under_temp(video) and Path(video).exists():
        from ..core.downloader import FrameExtractor
        out = Path(video).parent / f"preview_{Path(video).stem}.jpg"
        FrameExtractor.extract(Path(video), out)
        if out.exists():
            return FileResponse(out, media_type="image/jpeg")

    raise HTTPException(404, "Kein Vorschaubild verfügbar")


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
    return ScraperJob().resolve_pending(body.url, decision)


class ReanalyzeRequest(BaseModel):
    url: str


@router.post("/reanalyze")
def reanalyze(body: ReanalyzeRequest):
    """Lässt ein Pending-Item neu durch die KI-Cascade laufen."""
    return ScraperJob().reanalyze_pending(body.url)


import logging as _logging
import threading as _threading
from datetime import datetime as _datetime

_logger = _logging.getLogger(__name__)
_reanalyze_lock = _threading.Lock()


def _reanalyze_all_thread(job_id: int):
    """Background-Worker. Schreibt Progress in eine Log-Datei + bei jedem
    Item updaten wir die summary in der jobs-Tabelle damit das Frontend
    Live-Progress sehen kann."""
    from ..config_store import get_config as _gc
    db = get_db()
    log_dir = Path(_gc().get("paths", "logs_dir", default="/opt/scrapper/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"reanalyze-{_datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
    fh = _logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(_logging.INFO)
    fh.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _logging.getLogger().addHandler(fh)
    db.job_set_log_file(job_id, str(log_file))

    job = ScraperJob()
    summary = {
        "total": 0, "auto_saved": 0, "still_pending": 0, "errors": 0,
        "processed": 0, "current": None,
    }
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
            except Exception as e:
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
    finally:
        _logging.getLogger().removeHandler(fh)
        fh.close()
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
