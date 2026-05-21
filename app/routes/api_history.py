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


# /preview Endpoint wurde entfernt - es gibt keine Frame-Thumbnails mehr.
# Frontend behandelt 404 als "kein Vorschaubild".
