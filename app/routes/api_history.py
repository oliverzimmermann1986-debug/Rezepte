"""History-API mit Edit-Möglichkeit (Verschieben/Umbenennen/Löschen)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..db import get_db
from ..jobs.scraper import get_scraper_job

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


@router.get("")
def list_history(limit: int = Query(200, ge=1, le=2000)):
    return get_db().history_list(limit=limit)


class EditRequest(BaseModel):
    url: str
    name: str
    type: Optional[str] = None      # für recipe
    category: Optional[str] = None  # für wedding (oder Unter-Kategorie Rezept)


@router.post("/edit")
def edit_item(req: EditRequest):
    """Item im FS umsortieren/umbenennen + DB updaten + leeren alten Parent entfernen."""
    return get_scraper_job().move_history_item(
        req.url,
        new_name=req.name,
        new_type=req.type,
        new_category=req.category,
    )


@router.post("/delete")
def delete_item(payload: dict):
    """Item komplett löschen (FS + DB)."""
    url = payload.get("url")
    if not url:
        raise HTTPException(400, "url fehlt")
    return get_scraper_job().delete_history_item(url)


# ---------------- Reanalyze ----------------

class ReanalyzeOneRequest(BaseModel):
    url: str
    dry_run: bool = False
    auto_move: bool = False


@router.post("/reanalyze")
def reanalyze_one(req: ReanalyzeOneRequest):
    """Holt Description via yt-dlp neu, schickt durch aktuellen AI-Provider,
    aktualisiert DB falls Confidence > threshold und Ergebnis abweicht.
    Mit auto_move=True werden Files auch in den neuen target_dir verschoben."""
    return get_scraper_job().reanalyze_history_one(
        req.url, dry_run=req.dry_run, auto_move=req.auto_move,
    )


# Lock damit nur ein All-Run gleichzeitig läuft
import threading as _th
_history_reanalyze_lock = _th.Lock()


def _reanalyze_history_all_thread(job_id: int, dry_run: bool, limit: int, auto_move: bool):
    """Background-Thread - schreibt in jobs-Tabelle damit der UI-Status-Poll
    Progress sehen kann."""
    from ..db import get_db
    from ..jobs.scraper import get_scraper_job, reset_cancel
    db = get_db()
    reset_cancel()
    try:
        summary = get_scraper_job().reanalyze_history_all(
            dry_run=dry_run, limit=limit, auto_move=auto_move,
        )
        status = "ok"
        if summary.get("cancelled"):
            status = "error"
            summary.setdefault("error", "Abgebrochen")
        elif int(summary.get("errors", 0) or 0) > 0:
            status = "partial"
        db.job_finish(job_id, status, summary)
    except Exception as e:
        db.job_finish(job_id, "error", {"error": str(e)})
    finally:
        try:
            _history_reanalyze_lock.release()
        except RuntimeError:
            pass


@router.post("/reanalyze-all")
def reanalyze_all(payload: dict = None):
    """Startet einen Background-Job der alle History-Items reanalysiert.

    Body: {dry_run: bool, limit: int, auto_move: bool}
      auto_move=True: Files in den neuen target_dir verschieben (Filesystem-Cleanup)
    """
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", False))
    try:
        limit = max(1, min(int(payload.get("limit", 1000)), 5000))
    except (TypeError, ValueError):
        raise HTTPException(400, "limit muss eine Zahl zwischen 1 und 5000 sein")
    auto_move = bool(payload.get("auto_move", False))

    if not _history_reanalyze_lock.acquire(blocking=False):
        raise HTTPException(409, "History-Reanalyze läuft bereits")

    job_id = get_db().job_start("reanalyze")
    t = _th.Thread(
        target=_reanalyze_history_all_thread,
        args=(job_id, dry_run, limit, auto_move),
        daemon=True,
    )
    t.start()
    return {"ok": True, "job_id": job_id, "dry_run": dry_run, "limit": limit,
            "auto_move": auto_move}


@router.get("/junk")
def list_junk():
    """Findet History-Items deren Klassifikation 'Müll' aussieht. Reine Lese-
    Operation; ändert nichts. Zeigt Auto-Detection-Heuristiken pro Item."""
    return get_scraper_job().cleanup_junk_items(dry_run=True)


# /preview Endpoint wurde entfernt - es gibt keine Frame-Thumbnails mehr.
# Frontend behandelt 404 als "kein Vorschaubild".
