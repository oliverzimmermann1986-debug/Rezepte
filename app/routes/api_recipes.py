"""Durchsuchbarer Rezeptkatalog mit sicherer Medienausgabe."""
from __future__ import annotations

import json
import mimetypes
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..path_utils import ensure_within

router = APIRouter(prefix="/api/recipes", tags=["recipes"], dependencies=[Depends(require_auth)])

_INDEX_LOCK = threading.Lock()
_INDEX_READY = False
_MEDIA_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".pdf")
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _recipe_root() -> Path:
    return Path(get_config().get("paths", "recipe_dir", default="/opt/scrapper/files/rezepte"))


def _safe_recipe_dir(entry: Dict[str, Any]) -> Path:
    target = entry.get("target_dir")
    if not target:
        raise HTTPException(404, "Rezeptdateien fehlen")
    try:
        path = ensure_within(Path(target), _recipe_root())
    except ValueError:
        raise HTTPException(404, "Ungültiger Rezeptpfad")
    if not path.exists() or not path.is_dir():
        raise HTTPException(404, "Rezeptordner nicht gefunden")
    return path


def _metadata_from_disk(entry: Dict[str, Any]) -> Dict[str, str]:
    """Liest bestehende info.json-Dateien defensiv und ohne Pfadausbruch."""
    try:
        directory = _safe_recipe_dir(entry)
    except HTTPException:
        return {}
    try:
        info_path = ensure_within(directory / "info.json", _recipe_root())
    except ValueError:
        info_path = None
    info: Dict[str, Any] = {}
    if info_path is not None and info_path.is_file():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                info = raw
        except (OSError, ValueError, TypeError):
            info = {}

    parts = []
    try:
        parts = list(directory.relative_to(_recipe_root().resolve(strict=False)).parts)
    except ValueError:
        pass
    recipe_type = str(info.get("type") or (parts[0] if len(parts) >= 1 else "")).strip()
    category = str(info.get("category") or (parts[1] if len(parts) >= 2 else "")).strip()
    description = str(info.get("description") or "").strip()
    source = str(info.get("source") or ("social" if str(entry.get("url", "")).startswith("http") else "mail")).strip()
    name = str(info.get("name") or entry.get("name") or (parts[-1] if parts else "Rezept")).strip()
    return {
        "name": name[:300],
        "recipe_type": recipe_type[:120],
        "category": category[:120],
        "description": description[:50000],
        "source": source[:80],
    }


def _ensure_index() -> None:
    global _INDEX_READY
    if _INDEX_READY:
        return
    with _INDEX_LOCK:
        if _INDEX_READY:
            return
        db = get_db()
        updates = []
        for entry in db.recipe_all(limit=50000):
            if int(entry.get("metadata_indexed") or 0) == 1:
                continue
            metadata = _metadata_from_disk(entry)
            updates.append({
                "url": entry["url"],
                "name": metadata.get("name") or entry.get("name"),
                "recipe_type": metadata.get("recipe_type") or entry.get("recipe_type"),
                "category": metadata.get("category") or entry.get("category"),
                "description": metadata.get("description") or entry.get("description"),
                "source": metadata.get("source") or entry.get("source"),
            })
        db.recipe_index_metadata_batch(updates)
        _INDEX_READY = True


def _media_file(entry: Dict[str, Any]) -> Optional[Path]:
    try:
        directory = _safe_recipe_dir(entry)
    except HTTPException:
        return None
    files = []
    for candidate in directory.iterdir():
        if candidate.suffix.lower() not in _MEDIA_EXTENSIONS:
            continue
        try:
            safe_file = ensure_within(candidate, _recipe_root())
        except ValueError:
            continue
        if safe_file.is_file():
            files.append(safe_file)
    if not files:
        return None
    # Dokumente/Bilder aus Mail-Anhängen zuerst, sonst Video. Gleichartige Dateien alphabetisch.
    rank = {".pdf": 0, ".jpg": 1, ".jpeg": 1, ".png": 1, ".webp": 1,
            ".mp4": 2, ".webm": 2, ".mkv": 2, ".mov": 2}
    files.sort(key=lambda p: (rank.get(p.suffix.lower(), 9), p.name.lower()))
    return files[0]


def _serialize(entry: Dict[str, Any], *, full: bool = False) -> Dict[str, Any]:
    media = _media_file(entry)
    suffix = media.suffix.lower() if media else ""
    media_kind = "video" if suffix in _VIDEO_EXTENSIONS else "image" if suffix in _IMAGE_EXTENSIONS else "pdf" if suffix == ".pdf" else "file"
    description = str(entry.get("description") or "")
    item_id = str(entry.get("item_id") or "")
    return {
        "id": item_id,
        "name": entry.get("name") or "Unbenanntes Rezept",
        "type": entry.get("recipe_type") or "Sonstiges",
        "category": entry.get("category") or "Allgemein",
        "description": description if full else description[:260],
        "description_truncated": not full and len(description) > 260,
        "processed_at": entry.get("processed_at"),
        "source": entry.get("source") or "",
        "source_url": entry.get("url") if str(entry.get("url") or "").startswith(("http://", "https://")) else "",
        "media_kind": media_kind,
        "media_name": media.name if media else "",
        "has_media": bool(media),
        "media_url": f"/api/recipes/{item_id}/media" if media and item_id else "",
    }


@router.get("")
def search_recipes(
    q: str = Query("", max_length=200),
    type: str = Query("", max_length=120),
    category: str = Query("", max_length=120),
    sort: str = Query("newest", pattern="^(newest|oldest|name|type)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _ensure_index()
    result = get_db().recipe_search(
        query=q,
        recipe_type=type,
        category=category,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    result["items"] = [_serialize(item) for item in result["items"]]
    return result


@router.get("/{item_id}")
def recipe_detail(item_id: str):
    _ensure_index()
    entry = get_db().history_get_by_item_id(item_id)
    if not entry or entry.get("content_type") != "recipe":
        raise HTTPException(404, "Rezept nicht gefunden")
    return _serialize(entry, full=True)


@router.get("/{item_id}/media")
def recipe_media(item_id: str):
    entry = get_db().history_get_by_item_id(item_id)
    if not entry or entry.get("content_type") != "recipe":
        raise HTTPException(404, "Rezept nicht gefunden")
    media = _media_file(entry)
    if not media:
        raise HTTPException(404, "Keine Mediendatei vorhanden")
    media_type, _ = mimetypes.guess_type(media.name)
    return FileResponse(
        media,
        media_type=media_type or "application/octet-stream",
        filename=media.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )
