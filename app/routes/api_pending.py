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
