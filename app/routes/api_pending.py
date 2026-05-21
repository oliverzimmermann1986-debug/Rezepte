"""API für Pending-Items: Auflisten, Vorschau, Auflösen."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..db import get_db
from ..jobs.scraper import ScraperJob

router = APIRouter(prefix="/api/pending", tags=["pending"], dependencies=[Depends(require_auth)])


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
    if frame and Path(frame).exists():
        return FileResponse(frame, media_type="image/jpeg")

    # on-the-fly Frame-Extraktion aus dem Video
    video = entry.get("video_path")
    if video and Path(video).exists():
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
    if not video or not Path(video).exists():
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


@router.post("/reanalyze-all")
def reanalyze_all():
    """Verarbeitet alle aktuellen Pending-Items neu mit der aktuellen KI-Cascade."""
    job = ScraperJob()
    results = {"total": 0, "auto_saved": 0, "still_pending": 0, "errors": 0, "details": []}
    items = get_db().pending_list("pending")
    results["total"] = len(items)
    for item in items:
        try:
            r = job.reanalyze_pending(item["url"])
            if not r.get("ok"):
                results["errors"] += 1
            elif r.get("action") == "auto_saved":
                results["auto_saved"] += 1
            else:
                results["still_pending"] += 1
            results["details"].append({"url": item["url"], **r})
        except Exception as e:
            results["errors"] += 1
            results["details"].append({"url": item["url"], "ok": False, "error": str(e)})
    return results
