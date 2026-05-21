"""History-API mit Edit-Möglichkeit (Verschieben/Umbenennen/Löschen)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..db import get_db
from ..jobs.scraper import ScraperJob

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
    return ScraperJob().move_history_item(
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
    return ScraperJob().delete_history_item(url)


@router.get("/preview")
def preview_item(url: str):
    """Liefert thumbnail eines History-Items zurück."""
    from fastapi.responses import FileResponse
    entry = get_db().history_list(limit=2000)
    item = next((h for h in entry if h["url"] == url), None)
    if not item:
        raise HTTPException(404, "Nicht in Historie")
    target_dir = item.get("target_dir")
    if not target_dir:
        raise HTTPException(404, "Kein Pfad")
    d = Path(target_dir)
    if not d.exists():
        raise HTTPException(404, "Ordner existiert nicht mehr")
    # .jpg-File suchen
    for jpg in d.glob("*.jpg"):
        return FileResponse(jpg, media_type="image/jpeg")
    raise HTTPException(404, "Kein Vorschaubild")
