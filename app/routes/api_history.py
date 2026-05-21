"""History-API mit Edit-Möglichkeit (Verschieben/Umbenennen/Löschen)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs.scraper import ScraperJob

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


def _is_under(path_str: str, *roots: Path) -> bool:
    """Defense in depth: stellt sicher, dass ``path_str`` unter einem der
    erlaubten Roots liegt. Schützt FileResponse-Endpoints davor, beliebige
    Pfade aus der DB (Bug oder Manipulation) auszuliefern."""
    if not path_str:
        return False
    try:
        p = Path(path_str).resolve()
    except Exception:
        return False
    for root in roots:
        try:
            p.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


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
    """Liefert Thumbnail eines History-Items zurück."""
    from fastapi.responses import FileResponse

    # Direkter DB-Lookup statt 2000-Row-Scan
    item = get_db().history_get(url)
    if not item:
        raise HTTPException(404, "Nicht in Historie")
    target_dir = item.get("target_dir")
    if not target_dir:
        raise HTTPException(404, "Kein Pfad")

    # Path-Whitelist: nur unter recipe_dir/wedding_dir
    cfg = get_config()
    allowed_roots = [
        Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")),
        Path(cfg.get("paths", "wedding_dir", default="/mnt/hochzeit")),
    ]
    if not _is_under(target_dir, *allowed_roots):
        raise HTTPException(403, "Pfad außerhalb erlaubter Bereiche")

    d = Path(target_dir)
    if not d.exists():
        raise HTTPException(404, "Ordner existiert nicht mehr")
    # .jpg-File suchen
    for jpg in d.glob("*.jpg"):
        return FileResponse(jpg, media_type="image/jpeg")
    raise HTTPException(404, "Kein Vorschaubild")
