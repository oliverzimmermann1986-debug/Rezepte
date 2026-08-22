"""Recipe-Browser-API.

Endpoints:
  GET  /api/recipes                — Filter-fähige Liste (type, category,
                                     folder, tags, ingredients, search, limit, offset)
  GET  /api/recipes/{id}           — Detail mit Zutaten + Tags
  POST /api/recipes/sync           — FS→DB-Resync (idempotent)
  GET  /api/recipes/extraction/status — Background-Worker-Stand
  POST /api/recipes/{id}/extract   — Manuell für EIN Rezept extrahieren
  PUT  /api/recipes/{id}/tags      — Tags ersetzen (Liste von Namen)
  PUT  /api/recipes/{id}/ingredients — Zutaten manuell überschreiben
  GET  /api/recipes/facets         — Filter-Optionen (Typen, Kategorien,
                                     Tags, Top-Zutaten) für die Sidebar

Die Liste-Antwort hat absichtlich KEINE Zutaten dabei (nur Count) — das
Frontend zeigt im Grid-View eh nur einen Chip-Strip, und der Detail-View
holt die Zutaten per /api/recipes/{id} separat. Das hält den Listen-
Endpoint schnell auch bei 500+ Rezepten.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..core.analyzer import build_analyzer
from ..core.safety import resolve_directory_under, resolve_regular_file_under
from ..core.ttl_cache import TTLCache
from ..config_store import get_config
from ..db import get_db
from pathlib import Path
from ..recipes.canonical import canonical_name as _canonical
from ..recipes.search import suggest_query

logger = logging.getLogger(__name__)
from ..recipes.indexer import (
    ensure_extraction_running,
    is_extraction_running,
)
from ..recipes.units import normalize_unit
from ..recipes.sync_manager import request_sync, sync_status
from ..recipes.image_cache import ensure_thumbnail, invalidate_thumbnail_cache, normalize_image

router = APIRouter(prefix="/api/recipes", tags=["recipes"], dependencies=[Depends(require_auth)])


def _recipe_root() -> Path:
    return Path(get_config().get("paths", "recipe_dir", default="/mnt/rezepte"))


def _safe_recipe_folder(recipe: Dict[str, Any]) -> Path:
    try:
        return resolve_directory_under(Path(recipe["folder_path"]), _recipe_root())
    except (KeyError, OSError, ValueError) as exc:
        logger.warning("Unsicherer/fehlender Rezeptordner für #%s: %s", recipe.get("id"), exc)
        raise HTTPException(404, "Rezeptordner fehlt oder ist nicht zulässig") from exc


def _safe_recipe_file(recipe: Dict[str, Any], filename: str) -> Path:
    folder = _safe_recipe_folder(recipe)
    try:
        return resolve_regular_file_under(
            folder / str(filename),
            folder,
            _recipe_root(),
        )
    except (OSError, ValueError) as exc:
        logger.warning(
            "Unsicherer/fehlender Medienpfad für Rezept #%s (%r): %s",
            recipe.get("id"),
            filename,
            exc,
        )
        raise HTTPException(404, "Mediendatei fehlt oder ist nicht zulässig") from exc


def _actor(request: Request) -> str:
    from ..auth import auth_disabled, request_user
    if auth_disabled():
        return "local"
    return request_user(request) or "unknown"


def _version_before(recipe_id: int, request: Request, reason: str, source: str = "user") -> int:
    """Erstellt den zwingenden Rücksprungpunkt vor einer Inhaltsänderung.

    Eine Mutation ohne Snapshot würde den zugesagten Rückgängig-Schutz brechen.
    Deshalb wird bei einem DB-/Serialisierungsfehler bewusst nicht weitergeschrieben.
    """
    try:
        version_id = get_db().recipe_version_create(
            recipe_id, created_by=_actor(request), source=source, reason=reason
        )
    except Exception as exc:
        logger.exception("Versions-Snapshot für Rezept #%s fehlgeschlagen", recipe_id)
        raise HTTPException(500, "Änderung abgebrochen: Versions-Snapshot konnte nicht gespeichert werden") from exc
    if version_id is None:
        raise HTTPException(404, "Rezept nicht gefunden")
    return int(version_id)


def _backup_thumbnail_version(recipe: Dict[str, Any], version_id: int) -> None:
    """Sichert vor Cover-Mutationen auch die Binärdatei zur Version."""
    try:
        get_db()._backup_thumbnail_for_version(recipe, version_id)
    except Exception as exc:
        logger.exception("Cover-Sicherung für Version #%s fehlgeschlagen", version_id)
        raise HTTPException(
            500,
            "Bildänderung abgebrochen: Das bisherige Cover konnte nicht versioniert werden",
        ) from exc

_FACET_CACHE = TTLCache(ttl_seconds=5.0, max_entries=128)


# ── Listing ─────────────────────────────────────────────────────────────

@router.get("")
def list_recipes(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    folder: Optional[str] = Query(None, description="Pfad-Präfix, z.B. /mnt/rezepte/Hauptgericht"),
    tag_id: Optional[List[int]] = Query(None),
    ingredient: Optional[List[str]] = Query(None, description="canonical_name(s), AND-verknüpft"),
    exclude_ingredient: Optional[List[str]] = Query(
        None,
        description="canonical_name(s), die im Rezept nicht vorkommen dürfen",
    ),
    search: Optional[str] = Query(None),
    ingredients_status: Optional[str] = Query(None,
        description="Filter auf KI-Extraktions-Status: 'ok' | 'pending' | 'error' | 'skipped'"),
    verified: Optional[bool] = Query(None,
        description="Nur user_verified=1 (True) bzw =0 (False) Rezepte"),
    favorite_only: bool = Query(False,
        description="Nur favorisierte Rezepte (is_favorite=1)"),
    min_rating: int = Query(0, ge=0, le=5,
        description="Mindestbewertung (0=alle, 1-5=Sterne)"),
    needs_manual_care: Optional[bool] = Query(None,
        description="True=nur Rezepte ohne Zutaten oder ohne Schritte, "
                    "False=nur vollständige. Wird serverseitig gefiltert, damit "
                    "total und Seitenzahl zum Filter passen."),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Hauptlisten-Endpoint. Lazy-Sync + lazy-Extraction Trigger."""
    db = get_db()

    # Lazy-Background-Extraction starten (no-op wenn nichts pending)
    ensure_extraction_running()

    items = db.recipe_list(
        type=type,
        category=category,
        folder_prefix=folder,
        tag_ids=tag_id,
        ingredient_canonical=ingredient,
        ingredient_excluded=exclude_ingredient,
        search=search,
        ingredients_status=ingredients_status,
        verified=verified,
        favorite_only=favorite_only,
        min_rating=min_rating,
        needs_manual_care=needs_manual_care,
        limit=limit,
        offset=offset,
    )
    total = db.recipe_count(
        type=type,
        category=category,
        folder_prefix=folder,
        tag_ids=tag_id,
        ingredient_canonical=ingredient,
        ingredient_excluded=exclude_ingredient,
        search=search,
        ingredients_status=ingredients_status,
        verified=verified,
        favorite_only=favorite_only,
        min_rating=min_rating,
        needs_manual_care=needs_manual_care,
    )

    search_meta: Dict[str, Any] = {
        "original": search or "", "query": search or "", "corrected": False,
        "suggestion": "", "corrections": {},
    }
    effective_search = search
    if search and total == 0:
        suggestion = suggest_query(search, db.search_vocabulary(), db.search_synonyms_map())
        if suggestion and suggestion.corrected_query:
            effective_search = suggestion.corrected_query
            items = db.recipe_list(
                type=type, category=category, folder_prefix=folder, tag_ids=tag_id,
                ingredient_canonical=ingredient, search=effective_search,
                ingredient_excluded=exclude_ingredient,
                ingredients_status=ingredients_status, verified=verified,
                favorite_only=favorite_only, min_rating=min_rating,
                needs_manual_care=needs_manual_care,
                limit=limit, offset=offset,
            )
            total = db.recipe_count(
                type=type, category=category, folder_prefix=folder, tag_ids=tag_id,
                ingredient_canonical=ingredient, search=effective_search,
                ingredient_excluded=exclude_ingredient,
                ingredients_status=ingredients_status, verified=verified,
                favorite_only=favorite_only, min_rating=min_rating,
                needs_manual_care=needs_manual_care,
            )
            if total:
                search_meta["query"] = effective_search
                search_meta["corrected"] = True
                search_meta["suggestion"] = effective_search
                search_meta["corrections"] = suggestion.corrections

    # Pro Item nur die wichtigsten Felder + ingredients_count
    out = []
    for r in items:
        # Zutaten-Count + Tags ohne weiteren SELECT, sparen wir hier
        out.append({
            "id": r["id"],
            "name": r["name"],
            "type": r.get("type"),
            "category": r.get("category"),
            "url": r.get("url"),
            "folder_path": r.get("folder_path"),
            "thumb_filename": r.get("thumb_filename"),
            "video_filename": r.get("video_filename"),
            "source_added_at": r.get("source_added_at"),
            "ingredients_status": r.get("ingredients_status"),
            "is_favorite": bool(r.get("is_favorite")),
            "rating": r.get("rating") or 0,
            "user_verified": bool(r.get("user_verified")),
            "verified_by": r.get("verified_by"),
            "servings": r.get("servings"),
            "ingredients_count": int(r.get("ingredients_count") or 0),
            "steps_count": int(r.get("steps_count") or 0),
            "needs_manual_care": (
                int(r.get("ingredients_count") or 0) == 0
                or int(r.get("steps_count") or 0) == 0
            ),
            "description": ((r.get("description") or "").strip()[:220]),
        })
    return {"total": total, "items": out, "extraction_running": is_extraction_running(),
            "search_meta": search_meta, "sync": sync_status()}


@router.get("/facets")
def facets(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    tag_id: Optional[List[int]] = Query(None),
    ingredient: Optional[List[str]] = Query(None),
    exclude_ingredient: Optional[List[str]] = Query(None),
    search: Optional[str] = Query(None),
    ingredients_status: Optional[str] = Query(None),
    verified: Optional[bool] = Query(None),
    favorite_only: bool = Query(False),
    min_rating: int = Query(0, ge=0, le=5),
):
    """Filter-Optionen für die Sidebar. Tag-/Zutaten-Counts sind cross-gefiltert:
       jede Option zeigt die Treffer unter den übrigen aktiven Filtern, sodass
       die Zahlen beim Setzen eines Filters in den anderen Feldern schrumpfen.
       Types/Categories bleiben die volle Distinct-Liste (keine Counts in der UI)."""
    cache_key = (
        type or "", category or "", tuple(sorted(tag_id or [])),
        tuple(sorted(ingredient or [])), tuple(sorted(exclude_ingredient or [])),
        search or "", ingredients_status or "",
        verified, bool(favorite_only), int(min_rating),
    )
    cached = _FACET_CACHE.get(cache_key)
    if cached is not None:
        return cached

    db = get_db()
    with db.conn() as c:
        types = [r[0] for r in c.execute(
            "SELECT DISTINCT type FROM recipes WHERE deleted_at IS NULL "
            "AND type IS NOT NULL AND type != '' ORDER BY type"
        ).fetchall()]
        cats = [r[0] for r in c.execute(
            "SELECT DISTINCT category FROM recipes WHERE deleted_at IS NULL "
            "AND category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()]
    flt = dict(
        type=type, category=category, tag_ids=tag_id, ingredient_canonical=ingredient,
        ingredient_excluded=exclude_ingredient,
        search=search, ingredients_status=ingredients_status, verified=verified,
        favorite_only=favorite_only, min_rating=min_rating,
    )
    result = {
        "types": types,
        "categories": cats,
        "tags": db.tag_facets(**flt),
        "ingredients": db.ingredient_facets(**flt)[:50],
    }
    _FACET_CACHE.set(cache_key, result)
    return result


# ── Detail ──────────────────────────────────────────────────────────────

@router.get("/ingredients/known")
def known_ingredients():
    """Distinct Zutaten-Namen (canonical, mit Verwendungs-Count) für die
    Autocomplete beim Zutaten-Editieren — reduziert Dubletten wie
    'Zwiebel' vs. 'Zwiebeln' vs. 'rote Zwiebel'."""
    return {"ingredients": get_db().ingredients_known()}


@router.get("/{recipe_id}")
def get_recipe(recipe_id: int):
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r:
        raise HTTPException(404, "Rezept nicht gefunden")
    r["ingredients"] = db.recipe_ingredients_get(recipe_id)
    r["steps"] = db.recipe_steps_get(recipe_id)
    r["is_favorite"] = bool(r.get("is_favorite"))
    r["needs_manual_care"] = not r["ingredients"] or not r["steps"]
    r["manual_care_reasons"] = [
        label for missing, label in (
            (not r["ingredients"], "Zutaten fehlen"),
            (not r["steps"], "Zubereitungsschritte fehlen"),
        ) if missing
    ]
    r["tags"] = db.recipe_tags_get(recipe_id)
    r["cook_summary"] = db.recipe_cook_summary(recipe_id)
    r["cook_history"] = db.recipe_cook_history(recipe_id, limit=10)
    # PDF-Rezepte (Mail-Import): Original-PDF melden, damit das Frontend
    # einen "PDF öffnen"-Button zeigen kann (Bild allein reicht nicht).
    try:
        folder = _safe_recipe_folder(r)
        pdfs = sorted(p.name for p in folder.iterdir()
                      if p.is_file() and not p.is_symlink()
                      and p.suffix.lower() == ".pdf")
        r["pdf_filename"] = pdfs[0] if pdfs else None
        original_text = folder / "description_original.txt"
        if original_text.is_file() and not original_text.is_symlink():
            r["description_original"] = original_text.read_text(
                encoding="utf-8", errors="replace"
            )[:20000]
        else:
            r["description_original"] = None
    except Exception:
        r["pdf_filename"] = None
        r["description_original"] = None
    return r


@router.get("/{recipe_id}/pdf")
def get_recipe_pdf(recipe_id: int):
    """Liefert das Original-PDF eines Rezepts (inline, Browser-Viewer)."""
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r:
        raise HTTPException(404, "Rezept nicht gefunden")
    folder = _safe_recipe_folder(r)
    pdfs = sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.is_symlink()
                  and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise HTTPException(404, "Kein PDF vorhanden")
    pdf = _safe_recipe_file(r, pdfs[0].name)
    return FileResponse(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{pdf.name}"',
            "Cache-Control": "private, max-age=300",
        },
    )


# ── Mutation ────────────────────────────────────────────────────────────

class TagsUpdate(BaseModel):
    tags: List[str] = Field(default_factory=list, max_length=30)


def _normalized_user_tags(tags: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in tags:
        name = " ".join((raw or "").split())
        if not name:
            continue
        if len(name) > 80:
            raise HTTPException(400, "Ein Tag darf höchstens 80 Zeichen lang sein")
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized


@router.put("/{recipe_id}/tags")
def update_tags(recipe_id: int, payload: TagsUpdate, request: Request):
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    tags = _normalized_user_tags(payload.tags)
    _version_before(recipe_id, request, "Tags geändert")
    db.recipe_tags_set(recipe_id, tags)
    _FACET_CACHE.clear()
    return {"ok": True, "tags": db.recipe_tags_get(recipe_id)}


class BulkRecipeUpdate(BaseModel):
    recipe_ids: List[int] = Field(min_length=1, max_length=100)
    category: Optional[str] = Field(None, max_length=200)
    add_tags: List[str] = Field(default_factory=list, max_length=30)
    remove_tags: List[str] = Field(default_factory=list, max_length=30)


@router.post("/bulk-edit")
def bulk_edit_recipes(payload: BulkRecipeUpdate, request: Request) -> Dict[str, Any]:
    """Ändert Kategorie und User-Tags für bis zu 100 Rezepte.

    Jedes Rezept bekommt genau einen Snapshot. Ein Ordnerkonflikt stoppt nur
    das betroffene Rezept und wird transparent in ``failed`` gemeldet.
    """
    from ..recipes.manage import safe_update_recipe_metadata

    category = payload.category.strip() if payload.category is not None else None
    if category is not None:
        if not category:
            raise HTTPException(400, "Kategorie darf nicht leer sein")
        if any(part in category for part in ("/", "\\", "..")):
            raise HTTPException(
                400, "Kategorie darf keine Pfad-Separatoren oder '..' enthalten"
            )
    add_tags = _normalized_user_tags(payload.add_tags)
    remove_tags = _normalized_user_tags(payload.remove_tags)
    add_keys = {tag.casefold() for tag in add_tags}
    remove_keys = {tag.casefold() for tag in remove_tags}
    overlap = add_keys & remove_keys
    if overlap:
        raise HTTPException(
            400,
            "Ein Tag kann nicht gleichzeitig hinzugefügt und entfernt werden",
        )
    if category is None and not add_tags and not remove_tags:
        raise HTTPException(400, "Keine Änderung ausgewählt")

    db = get_db()
    updated: List[Dict[str, Any]] = []
    unchanged: List[int] = []
    failed: List[Dict[str, Any]] = []
    for recipe_id in dict.fromkeys(payload.recipe_ids):
        recipe = db.recipe_get(int(recipe_id))
        if not recipe or recipe.get("deleted_at") is not None:
            failed.append({"recipe_id": recipe_id, "error": "Rezept nicht gefunden"})
            continue

        current_tags = db.recipe_tags_get(int(recipe_id))
        manual_tags = [tag["name"] for tag in current_tags if not tag.get("auto")]
        auto_keys = {
            str(tag["name"]).casefold() for tag in current_tags if tag.get("auto")
        }
        next_tags = [tag for tag in manual_tags if tag.casefold() not in remove_keys]
        known_keys = {tag.casefold() for tag in next_tags} | auto_keys
        for tag in add_tags:
            if tag.casefold() not in known_keys:
                next_tags.append(tag)
                known_keys.add(tag.casefold())
        category_changed = category is not None and category != recipe.get("category")
        tags_changed = {
            tag.casefold() for tag in next_tags
        } != {tag.casefold() for tag in manual_tags}
        if not category_changed and not tags_changed:
            unchanged.append(int(recipe_id))
            continue
        if len(next_tags) > 30:
            failed.append({
                "recipe_id": recipe_id,
                "name": recipe.get("name"),
                "error": "Mehr als 30 eigene Tags wären nicht zulässig",
            })
            continue

        try:
            _version_before(
                int(recipe_id), request, "Massenpflege: Kategorie oder Tags geändert"
            )
            if category_changed:
                safe_update_recipe_metadata(
                    db,
                    int(recipe_id),
                    name=recipe.get("name") or "Unbekannt",
                    recipe_type=recipe.get("type") or "Sonstiges",
                    category=category or "Allgemein",
                    description=recipe.get("description") or "",
                    servings=recipe.get("servings"),
                    url=recipe.get("url"),
                )
            if tags_changed:
                db.recipe_tags_set(int(recipe_id), next_tags)
            updated.append({
                "recipe_id": int(recipe_id),
                "name": recipe.get("name"),
                "category": category if category_changed else recipe.get("category"),
            })
        except HTTPException as exc:
            failed.append({
                "recipe_id": recipe_id,
                "name": recipe.get("name"),
                "error": str(exc.detail),
            })
        except (ValueError, RuntimeError) as exc:
            failed.append({
                "recipe_id": recipe_id,
                "name": recipe.get("name"),
                "error": str(exc),
            })

    if updated:
        _FACET_CACHE.clear()
    return {
        "ok": not failed,
        "updated": updated,
        "unchanged": unchanged,
        "failed": failed,
    }


class MetadataUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=50_000)
    servings: Optional[int] = Field(None, ge=1, le=50)
    url: Optional[str] = Field(None, max_length=2_000)


def _metadata_source_url(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password or any(ch.isspace() for ch in value):
            raise ValueError
        parsed.port  # validiert auch ungültige Portangaben
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Die Quelladresse muss eine gültige HTTPS-URL sein") from exc
    return value


@router.put("/{recipe_id}/metadata")
def update_metadata(recipe_id: int, payload: MetadataUpdate, request: Request):
    from ..recipes.manage import safe_update_recipe_metadata

    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    source_url = _metadata_source_url(payload.url)
    _version_before(recipe_id, request, "Rezeptinformationen geändert")
    try:
        result = safe_update_recipe_metadata(
            db,
            recipe_id,
            name=payload.name,
            recipe_type=payload.type,
            category=payload.category,
            description=payload.description,
            servings=payload.servings,
            url=source_url,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    _FACET_CACHE.clear()
    return {**result, "recipe": get_recipe(recipe_id)}


class CookingProgressUpdate(BaseModel):
    completed_steps: List[int] = Field(default_factory=list, max_length=200)
    active_step: int = Field(default=0, ge=0)
    servings: Optional[int] = Field(None, ge=1, le=50)


def _validated_cooking_steps(
    recipe_id: int, completed_steps: List[int], active_step: int
) -> tuple[List[int], int, int]:
    steps = get_db().recipe_steps_get(recipe_id)
    if not steps:
        raise HTTPException(409, "Das Rezept hat keine Zubereitungsschritte")
    count = len(steps)
    completed = sorted(set(completed_steps))
    if any(step < 0 or step >= count for step in completed):
        raise HTTPException(400, "Der Kochfortschritt passt nicht zur Schrittliste")
    if active_step >= count:
        raise HTTPException(400, "Der aktive Kochschritt existiert nicht")
    return completed, active_step, count


@router.get("/{recipe_id}/cooking-progress")
def cooking_progress(recipe_id: int, request: Request) -> Dict[str, Any]:
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise HTTPException(404, "Rezept nicht gefunden")
    username = _actor(request)
    progress = db.recipe_cooking_progress_get(recipe_id, username)
    step_count = len(db.recipe_steps_get(recipe_id))
    if not progress:
        return {
            "recipe_id": recipe_id,
            "username": username,
            "completed_steps": [],
            "active_step": 0,
            "servings": recipe.get("servings"),
            "started_at": None,
            "updated_at": None,
            "exists": False,
            "step_count": step_count,
        }
    progress["completed_steps"] = [
        step for step in progress["completed_steps"] if step < step_count
    ]
    progress["active_step"] = min(
        max(0, int(progress.get("active_step") or 0)), max(0, step_count - 1)
    )
    progress["step_count"] = step_count
    return progress


@router.put("/{recipe_id}/cooking-progress")
def update_cooking_progress(
    recipe_id: int, payload: CookingProgressUpdate, request: Request
) -> Dict[str, Any]:
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise HTTPException(404, "Rezept nicht gefunden")
    completed, active, count = _validated_cooking_steps(
        recipe_id, payload.completed_steps, payload.active_step
    )
    progress = db.recipe_cooking_progress_set(
        recipe_id,
        _actor(request),
        completed_steps=completed,
        active_step=active,
        servings=payload.servings,
    )
    progress["step_count"] = count
    return progress


@router.delete("/{recipe_id}/cooking-progress")
def clear_cooking_progress(recipe_id: int, request: Request) -> Dict[str, Any]:
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    return {
        "ok": True,
        "cleared": db.recipe_cooking_progress_clear(recipe_id, _actor(request)),
    }


class CookingComplete(BaseModel):
    servings: Optional[int] = Field(None, ge=1, le=50)


@router.post("/{recipe_id}/cooking-complete")
def complete_cooking(
    recipe_id: int, payload: CookingComplete, request: Request
) -> Dict[str, Any]:
    db = get_db()
    try:
        entry = db.recipe_cooking_complete(
            recipe_id,
            _actor(request),
            servings=payload.servings,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "ok": True,
        "entry": entry,
        "summary": db.recipe_cook_summary(recipe_id),
    }


@router.get("/{recipe_id}/cook-history")
def cook_history(
    recipe_id: int, limit: int = Query(20, ge=1, le=100)
) -> Dict[str, Any]:
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    return {
        "items": db.recipe_cook_history(recipe_id, limit=limit),
        "summary": db.recipe_cook_summary(recipe_id),
    }


class IngredientIn(BaseModel):
    name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class IngredientsUpdate(BaseModel):
    ingredients: List[IngredientIn]


@router.put("/{recipe_id}/ingredients")
def update_ingredients(recipe_id: int, payload: IngredientsUpdate, request: Request):
    """Manuelle Override der Zutatenliste. Setzt ingredients_status='ok',
    sodass der Background-Worker das Rezept nicht überschreibt.

    Nach dem Save werden Diät-Tags (vegan/vegetarisch/laktosefrei/...) neu
    berechnet — User hat ggf. Fleisch entfernt oder Milchprodukte ergänzt,
    die alten Tags wären dann falsch. KI-Stil-Tags (italienisch, schnell,
    pasta) bleiben unangetastet weil die aus der Description kommen, die
    sich nicht geändert hat."""
    from ..recipes.auto_tags import refresh_diet_auto_tags
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    _version_before(recipe_id, request, "Zutaten manuell geändert")
    prepared = []
    for ing in payload.ingredients:
        if not ing.name.strip():
            continue
        prepared.append({
            "name": ing.name.strip(),
            "canonical_name": _canonical(ing.name),
            "amount": ing.amount,
            "unit": normalize_unit(ing.unit),
            "raw": ing.raw,
        })
    db.recipe_set_extraction_result(recipe_id, status="ok", ingredients=prepared)

    # Diät-Tags recompute: nimm existierende auto-Tags die NICHT in DIET_TAGS
    # sind (das sind die KI-Stil-Tags wie 'pasta', 'schnell') und merge sie
    # mit den frisch berechneten Diät-Tags aus der neuen Zutatenliste.
    try:
        refresh_diet_auto_tags(
            db,
            recipe_id,
            [p["canonical_name"] for p in prepared],
        )
    except Exception as e:
        # Diät-Tag-Recompute ist nice-to-have — bei Fehler nur loggen,
        # Save selbst ist erfolgreich.
        logger.warning(f"Rezept #{recipe_id}: diet-tag-recompute failed: {e}")

    return {"ok": True, "ingredients": db.recipe_ingredients_get(recipe_id)}


class StepIn(BaseModel):
    instruction: str
    timer_seconds: Optional[int] = Field(None, ge=1, le=86_400)


class StepsUpdate(BaseModel):
    steps: List[StepIn]


@router.put("/{recipe_id}/steps")
def update_steps(recipe_id: int, payload: StepsUpdate, request: Request):
    """Manuelles Override der Zubereitungs-Schritte. step_number wird beim
    Insert automatisch aus der Listen-Position abgeleitet (1-basiert),
    sodass das Frontend nur die Reihenfolge ändern muss."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    _version_before(recipe_id, request, "Zubereitungsschritte geändert")
    db.recipe_steps_set(recipe_id, [s.model_dump() for s in payload.steps])
    return {"ok": True, "steps": db.recipe_steps_get(recipe_id)}


class ServingsUpdate(BaseModel):
    servings: Optional[int] = Field(None, ge=1, le=50)


@router.put("/{recipe_id}/servings")
def update_servings(recipe_id: int, payload: ServingsUpdate, request: Request):
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    _version_before(recipe_id, request, "Portionszahl geändert")
    db.recipe_set_servings(recipe_id, payload.servings)
    return {"ok": True, "servings": payload.servings}


# ── Sync + Extraction ──────────────────────────────────────────────────

@router.post("/sync", status_code=202)
def post_sync():
    """Plant einen manuellen FS→DB-Resync ein und kehrt sofort zurück."""
    return request_sync(reason="manual", force=True, db=get_db())


@router.get("/sync/status")
def get_sync_status():
    """Fortschritt/Ergebnis des aktuellen oder letzten FS-Syncs."""
    return sync_status()


@router.get("/extraction/status")
def extraction_status():
    db = get_db()
    return {
        "running": is_extraction_running(),
        "stats": db.recipes_extraction_stats(),
    }


@router.post("/recover-empty")
def recover_empty(request: Request) -> Dict[str, Any]:
    """Setzt ingredients_status='pending' für alle aktiven Rezepte die status IN
    ('ok','error','skipped') haben aber 0 Zutaten in recipe_ingredients. Meist alte
    Extracts wo das Prompt zu restriktiv war oder das JSON abgeschnitten
    wurde. Worker pickt die zurückgesetzten Rezepte auf und versucht neu
    mit dem aktuellen Prompt + max_tokens=6000.

    Robustheit: fetch im read-only context, UPDATE in executemany (eine
    Transaktion). Response minimal (count + ids), kein dict(r) damit
    JSON-Encoding nicht an Umlauten/None scheitern kann."""
    db = get_db()
    try:
        # 1. Read: fetch alle Kandidaten in einer einzigen SELECT
        with db.conn() as c:
            rows = c.execute("""
                SELECT r.id
                FROM recipes r
                LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id
                WHERE r.ingredients_status IN ('ok', 'error', 'skipped')
                  AND r.deleted_at IS NULL
                  AND COALESCE(r.user_verified, 0) = 0
                  AND r.description IS NOT NULL
                  AND length(r.description) >= 20
                GROUP BY r.id
                HAVING COUNT(ri.id) = 0
            """).fetchall()
        ids = [int(r["id"]) for r in rows]

        # 2. Vor der Sammeländerung für jedes Rezept einen Rücksprungpunkt
        # sichern. Erst wenn alle Snapshots erfolgreich sind, folgt das Update.
        for rid in ids:
            _version_before(rid, request, "Leere Extraktion erneut eingeplant", source="admin")
        if ids:
            with db.conn() as c:
                c.executemany(
                    "UPDATE recipes SET ingredients_status='pending', "
                    "ingredients_extracted_at=NULL, extraction_claimed_at=NULL, "
                    "extraction_claim_owner=NULL WHERE id=?",
                    [(rid,) for rid in ids],
                )

        # 3. Worker starten
        from ..recipes.indexer import ensure_extraction_running
        worker_started = False
        try:
            worker_started = ensure_extraction_running()
        except Exception as e:
            logger.warning(f"recover-empty: worker-start failed: {e}", exc_info=True)

        logger.info(
            f"recover-empty: {len(ids)} Rezepte → 'pending', "
            f"worker_started={worker_started}"
        )
        return {
            "ok": True,
            "reset_count": len(ids),
            "ids": ids,
            "worker_started": worker_started,
        }
    except Exception as e:
        # Defensiv: Stack-Trace ins Log damit User-Report 'Fehler 500' diagnostizierbar
        logger.exception(f"recover-empty failed: {e}")
        raise HTTPException(500, f"recover-empty failed: {type(e).__name__}: {e}")


@router.post("/{recipe_id}/rescrape")
def rescrape_recipe(
    recipe_id: int,
    request: Request,
    reanalyze: bool = Query(False, description="Zutatenanalyse auch bei unveränderter Caption neu starten"),
) -> Dict[str, Any]:
    """Re-Scrape: ruft yt-dlp nochmal für die ursprüngliche URL auf und
    aktualisiert Caption + Thumbnail im Folder. Bei TikTok wird optional die
    im Browser aufgeklappte lange Caption gelesen. Video wird NICHT neu
    heruntergeladen (--skip-download). Nach einer Caption-Änderung wird die
    Zutaten-/Schrittanalyse erneut eingeplant.

    Use-Cases:
    - Rezepte ohne Thumbnail (Audit 'Kein Bild') → frisches Bild holen
    - TikTok/Instagram-Caption wurde aktualisiert → frischer Text für
      bessere KI-Extraktion
    - Bei beschädigtem .jpg im Folder: neu pullen

    Fehlerfälle: URL nicht da, yt-dlp-Fehler (Video gelöscht, geo-blocked,
    Login nötig). Returnt 200 mit ok=False + Detail bei Fehlern."""
    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    url = rec.get("url")
    if not url:
        return {"ok": False, "error": "Rezept hat keine URL (manuell angelegt?)"}
    try:
        folder_p = _safe_recipe_folder(rec)
    except HTTPException:
        return {"ok": False, "error": "FS-Folder fehlt oder ist nicht zulässig"}

    # Downloader bauen mit Config
    cfg = get_config()
    from ..core.downloader import VideoDownloader
    ytdlp_cfg = cfg.get("ytdlp", default={}) or {}
    ytdlp_path = ytdlp_cfg.get("binary", "/opt/scrapper/venv/bin/yt-dlp")
    temp_dir = Path(cfg.get("paths", "temp_dir", default="/tmp/scrapper"))
    cookies_file = str(ytdlp_cfg.get("cookies_file") or "").strip() or None
    dl = VideoDownloader(ytdlp_path=ytdlp_path, temp_dir=temp_dir,
                          cookies_file=cookies_file)

    meta = dl.refresh_metadata(url) or {}
    description_source = "yt-dlp"

    # TikTok exposes only the short first line through public metadata in some
    # posts. Its website renders the long recipe caption after clicking "mehr".
    if ytdlp_cfg.get("expanded_tiktok_caption", True):
        from ..core.tiktok_caption import fetch_expanded_tiktok_caption
        expanded = fetch_expanded_tiktok_caption(
            url,
            fallback_text=str(meta.get("description_text") or ""),
            cookies_file=cookies_file,
            timeout_seconds=int(ytdlp_cfg.get("browser_timeout_seconds", 35)),
            executable_path=str(ytdlp_cfg.get("browser_executable_path") or "").strip() or None,
        )
        if expanded:
            meta["description_text"] = expanded
            description_source = "tiktok-browser"
    if not meta:
        return {"ok": False, "error": "yt-dlp lieferte nichts — URL down/geo-blocked/login nötig?"}

    changed = {"description": False, "thumbnail": False}

    # Description aktualisieren
    extraction_queued = False
    new_desc = meta.get("description_text")
    if new_desc and new_desc.strip():
        old_desc = rec.get("description") or ""
        if new_desc != old_desc:
            _version_before(recipe_id, request, "Beschreibung aus Quelle aktualisiert", source="import")
            from ..core.safety import atomic_write_text
            try:
                atomic_write_text(folder_p / "description.txt", new_desc)
            except OSError as exc:
                logger.warning("description.txt schreiben fehler: %s", exc)
                return {
                    "ok": False,
                    "error": "Beschreibung konnte nicht sicher gespeichert werden",
                }
            with db.conn() as c:
                c.execute(
                    "UPDATE recipes SET description=?, ingredients_status='pending', "
                    "ingredients_extracted_at=NULL, extraction_claimed_at=NULL, "
                    "extraction_claim_owner=NULL WHERE id=?",
                    (new_desc, recipe_id),
                )
            changed["description"] = True
            extraction_queued = True

    # Bei leeren Rezepten muss die Analyse auch dann erneut laufen, wenn die
    # Quelle exakt denselben Text liefert. Der normale Einzel-Re-Scrape bleibt
    # ohne reanalyze-Flag unverändert und fasst bestehende KI-Daten nicht an.
    effective_desc = str(new_desc or rec.get("description") or "").strip()
    if reanalyze and not extraction_queued and len(effective_desc) >= 20:
        _version_before(
            recipe_id,
            request,
            "Zutatenanalyse nach Quellenabruf erneut eingeplant",
            source="admin",
        )
        with db.conn() as c:
            c.execute(
                "UPDATE recipes SET ingredients_status='pending', "
                "ingredients_extracted_at=NULL, extraction_claimed_at=NULL, "
                "extraction_claim_owner=NULL WHERE id=?",
                (recipe_id,),
            )
        extraction_queued = True

    # Thumbnail ersetzen
    new_thumb = meta.get("thumbnail_bytes")
    if new_thumb:
        staged_thumb = folder_p / f".thumb-refresh-{time.time_ns()}.img"
        normalized_thumb = folder_p / f".thumb-refresh-{time.time_ns()}.jpg"
        try:
            from ..core.safety import atomic_write_bytes
            atomic_write_bytes(staged_thumb, bytes(new_thumb))
            target_thumb = folder_p / "thumb.jpg"
            normalize_image(staged_thumb, normalized_thumb)
            version_id = _version_before(
                recipe_id,
                request,
                "Coverbild aus Quelle ersetzt",
                source="import",
            )
            _backup_thumbnail_version(rec, version_id)
            normalized_thumb.replace(target_thumb)
            with db.conn() as c:
                c.execute("UPDATE recipes SET thumb_filename=? WHERE id=?",
                          ("thumb.jpg", recipe_id))
            # Erst nach erfolgreicher Normalisierung + DB-Aktualisierung alte
            # Varianten entfernen. Das funktionierende Bild bleibt bei Fehlern
            # dadurch erhalten.
            for old in folder_p.glob("thumb.*"):
                if old != target_thumb:
                    old.unlink(missing_ok=True)
            for old in folder_p.glob("*.jpg"):
                if old != target_thumb and (
                    old.name.startswith("thumb") or old.stem == folder_p.name
                ):
                    old.unlink(missing_ok=True)
            invalidate_thumbnail_cache(folder_p)
            changed["thumbnail"] = True
        except Exception as e:
            logger.warning(f"Thumbnail-Copy fehler #{recipe_id}: {e}")
        finally:
            staged_thumb.unlink(missing_ok=True)
            normalized_thumb.unlink(missing_ok=True)

    worker_started = False
    if extraction_queued:
        try:
            worker_started = ensure_extraction_running()
        except Exception as e:
            logger.warning("Re-Scrape: Extraktions-Worker konnte nicht starten: %s", e, exc_info=True)

    logger.info(
        "rescrape #%s '%s': %s, description_source=%s, worker_started=%s",
        recipe_id, rec.get("name"), changed, description_source, worker_started,
    )
    return {
        "ok": True,
        "description_updated": changed["description"],
        "thumbnail_updated": changed["thumbnail"],
        "description_source": description_source,
        "ingredients_queued": extraction_queued,
        "worker_started": worker_started,
        "any_change": changed["description"] or changed["thumbnail"],
    }


@router.post("/{recipe_id}/upload-thumbnail")
async def upload_thumbnail(
    recipe_id: int,
    request: Request,
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    """Lädt ein Bild ein, normalisiert es und tauscht das alte Thumbnail atomar."""
    import tempfile

    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    folder_p = _safe_recipe_folder(rec)
    max_size = 10 * 1024 * 1024
    size = 0
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(prefix="recipe-upload-", suffix=".img", delete=False) as tmp:
            temp_path = Path(tmp.name)
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(400, "Datei zu groß (max. 10 MB)")
                tmp.write(chunk)
        if size == 0:
            raise HTTPException(400, "Leere Datei")

        target = folder_p / "thumb.jpg"
        staged_target = folder_p / f".thumb-upload-{time.time_ns()}.jpg"
        try:
            normalize_image(temp_path, staged_target)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Bild konnte nicht gelesen werden: {exc}") from exc

        version_id = _version_before(recipe_id, request, "Coverbild ersetzt")
        _backup_thumbnail_version(rec, version_id)
        staged_target.replace(target)

        # Erst nach erfolgreichem atomaren Austausch alte Varianten entfernen.
        for old in folder_p.glob("thumb.*"):
            if old != target and old.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    old.unlink()
                except OSError:
                    pass
        invalidate_thumbnail_cache(folder_p)
        ensure_thumbnail(target, 400)
        ensure_thumbnail(target, 800)
        with db.conn() as c:
            c.execute("UPDATE recipes SET thumb_filename=? WHERE id=?", (target.name, recipe_id))
        logger.info("thumbnail upload #%s '%s' → %s (%s B)", recipe_id, rec.get("name"), target.name, size)
        return {
            "ok": True,
            "thumbnail": target.name,
            "size_bytes": size,
            "version_id": version_id,
        }
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            staged_target.unlink(missing_ok=True)
        except (NameError, OSError):
            pass


@router.post("/{recipe_id}/extract-frame")
def extract_frame(recipe_id: int, request: Request, seconds: float = 2.0) -> Dict[str, Any]:
    """Extrahiert einen Frame aus dem Video im Recipe-Folder via ffmpeg und
    setzt diesen als Thumbnail. Alternative zu rescrape wenn die URL tot
    ist aber das Video noch lokal liegt.

    `seconds` ist der Zeitstempel (default 2.0s — vermeidet schwarze
    Anfangsframes). Sucht im folder_path nach .mp4/.mov/.webm/.mkv."""
    import subprocess as _sp
    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    folder = rec.get("folder_path")
    if not folder or not Path(folder).exists():
        return {"ok": False, "error": f"Folder fehlt: {folder}"}
    folder_p = Path(folder)

    # Video finden — erste Datei mit Video-Extension
    video = None
    for ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
        cands = list(folder_p.glob(f"*{ext}"))
        if cands:
            video = cands[0]
            break
    if not video:
        return {"ok": False, "error": "Kein Video im Folder gefunden (.mp4/.mov/.webm/.mkv)"}

    target = folder_p / "thumb.jpg"
    staged_target = folder_p / f".frame-extract-{time.time_ns()}.jpg"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(seconds), "-i", str(video),
        "-frames:v", "1", "-q:v", "2",
        str(staged_target),
    ]
    try:
        r = _sp.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        staged_target.unlink(missing_ok=True)
        return {"ok": False, "error": "ffmpeg nicht installiert"}
    except _sp.TimeoutExpired:
        staged_target.unlink(missing_ok=True)
        return {"ok": False, "error": "ffmpeg Timeout (>60s)"}
    if r.returncode != 0 or not staged_target.exists():
        # Vielleicht ist seconds > Video-Länge → Frame aus 0.5s versuchen
        cmd[cmd.index("-ss") + 1] = "0.5"
        try:
            r2 = _sp.run(cmd, capture_output=True, text=True, timeout=60)
            if r2.returncode != 0 or not staged_target.exists():
                staged_target.unlink(missing_ok=True)
                return {"ok": False,
                        "error": f"ffmpeg: {(r.stderr or r2.stderr).strip()[:200]}"}
        except Exception as e:
            staged_target.unlink(missing_ok=True)
            return {"ok": False, "error": str(e)}

    version_id = _version_before(recipe_id, request, "Coverbild aus Video ersetzt")
    _backup_thumbnail_version(rec, version_id)
    staged_target.replace(target)
    for old in folder_p.glob("thumb.*"):
        if old != target and old.is_file() and not old.is_symlink():
            old.unlink(missing_ok=True)
    invalidate_thumbnail_cache(folder_p)

    with db.conn() as c:
        c.execute("UPDATE recipes SET thumb_filename=? WHERE id=?",
                  ("thumb.jpg", recipe_id))
    logger.info(f"frame #{recipe_id} '{rec.get('name')}' @ {seconds}s → {target.name}")
    return {"ok": True, "thumbnail": "thumb.jpg", "video": video.name,
            "seconds": seconds, "size_bytes": target.stat().st_size,
            "version_id": version_id}


@router.post("/{recipe_id}/verify")
def toggle_verify(recipe_id: int, request: Request,
                    verified: bool = Query(True)) -> Dict[str, Any]:
    """Markiert ausschließlich die Zutatenliste als manuell geprüft."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    if verified and not db.recipe_ingredients_get(recipe_id):
        raise HTTPException(409, "Eine leere Zutatenliste kann nicht als geprüft markiert werden")
    username = _actor(request)
    _version_before(recipe_id, request, "Prüfstatus geändert")
    db.recipe_set_verified(recipe_id, verified, username if verified else None)
    logger.info(f"verify #{recipe_id}: {verified} von '{username}'")
    return {"ok": True, "verified": verified, "by": username}


@router.post("/{recipe_id}/nutrition")
def compute_nutrition_for(recipe_id: int, request: Request) -> Dict[str, Any]:
    """On-Demand Nährwert-Berechnung für ein Rezept. KI-Single-Call.
    Setzt calories_per_serving + protein/carbs/fat_g + computed_at."""
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise HTTPException(404, "Rezept nicht gefunden")
    if recipe.get("ingredients_status") == "running":
        raise HTTPException(
            409,
            "Für dieses Rezept läuft bereits eine Extraktion",
        )
    ings = db.recipe_ingredients_get(recipe_id)
    if len(ings) < 3:
        raise HTTPException(400, f"Zu wenig Zutaten ({len(ings)}) für sinnvolle Schätzung")
    cfg = get_config()
    try:
        analyzer = build_analyzer(cfg.get("ai", default={}) or {})
    except Exception as e:
        raise HTTPException(500, f"Analyzer-Setup fehlgeschlagen: {e}")

    nutr = analyzer.compute_nutrition(ings, recipe.get("servings"))
    if not nutr:
        raise HTTPException(502, "KI konnte keine Nährwerte berechnen (zu wenig Info?)")

    _version_before(recipe_id, request, "Nährwerte neu berechnet", source="ai")
    db.recipe_set_nutrition(
        recipe_id, nutr["calories"], nutr["protein_g"],
        nutr["carbs_g"], nutr["fat_g"],
    )
    for idx, kcal in (nutr.get("per_ingredient") or {}).items():
        if 0 <= idx < len(ings):
            db.recipe_ingredient_set_calories(ings[idx]["id"], kcal)
    return {"ok": True, **nutr}


@router.post("/compute-nutrition-bulk")
def compute_nutrition_bulk(request: Request, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Bulk: bis zu N Rezepte ohne Nährwerte berechnen. Synchroner Lauf —
    bei vielen Rezepten >30s, daher mit Limit. UI ruft das wiederholt auf
    bis pending=0."""
    db = get_db()
    cfg = get_config()
    try:
        analyzer = build_analyzer(cfg.get("ai", default={}) or {})
    except Exception as e:
        raise HTTPException(500, f"Analyzer-Setup fehlgeschlagen: {e}")

    pending = db.recipes_pending_nutrition(limit=limit)
    computed = 0
    failed = []
    for r in pending:
        ings = db.recipe_ingredients_get(int(r["id"]))
        try:
            nutr = analyzer.compute_nutrition(ings, r.get("servings"))
            if nutr:
                _version_before(int(r["id"]), request, "Nährwerte gesammelt berechnet", source="ai")
                db.recipe_set_nutrition(
                    int(r["id"]), nutr["calories"], nutr["protein_g"],
                    nutr["carbs_g"], nutr["fat_g"],
                )
                for idx, kcal in (nutr.get("per_ingredient") or {}).items():
                    if 0 <= idx < len(ings):
                        db.recipe_ingredient_set_calories(ings[idx]["id"], kcal)
                computed += 1
            else:
                failed.append({"id": r["id"], "name": r["name"], "reason": "KI-leer"})
        except Exception as e:
            failed.append({"id": r["id"], "name": r["name"], "reason": str(e)[:100]})
    logger.info(f"compute-nutrition-bulk: {computed}/{len(pending)} berechnet")
    return {
        "ok": True,
        "computed": computed,
        "processed": len(pending),
        "failed": failed,
        "remaining": max(0, db.recipe_count() - computed) if computed else None,
    }


@router.post("/{recipe_id}/extract")
def extract_one(recipe_id: int, background_tasks: BackgroundTasks, request: Request):
    """Manueller Trigger: extrahiert (oder re-extrahiert) Zutaten + Schritte +
    Portionen für EIN Rezept synchron. Single KI-Call via analyze_recipe_content."""
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise HTTPException(404, "Rezept nicht gefunden")
    if recipe.get("ingredients_status") == "running":
        raise HTTPException(
            409,
            "Für dieses Rezept läuft bereits eine Extraktion",
        )
    _version_before(recipe_id, request, "KI-Inhalte neu extrahiert", source="ai")

    desc = recipe.get("description") or ""
    if len(desc.strip()) < 20:
        db.recipe_set_extraction_result(recipe_id, status="skipped", ingredients=[])
        return {"ok": True, "status": "skipped", "reason": "Beschreibung zu kurz"}

    cfg = get_config()
    try:
        analyzer = build_analyzer(cfg.get("ai", default={}) or {})
    except Exception as e:
        raise HTTPException(500, f"Analyzer-Setup fehlgeschlagen: {e}")

    try:
        with db.conn() as c:
            tag_rows = c.execute("SELECT name FROM tags").fetchall()
            existing_tags = [r[0] for r in tag_rows]
            can_rows = c.execute(
                "SELECT DISTINCT canonical_name FROM recipe_ingredients "
                "WHERE canonical_name IS NOT NULL AND canonical_name != ''"
            ).fetchall()
            existing_canonical = [r[0] for r in can_rows]
    except Exception:
        existing_tags, existing_canonical = [], []

    try:
        content = analyzer.analyze_recipe_content(
            desc, existing_tags=existing_tags, existing_canonical=existing_canonical,
        )
    except Exception as e:
        db.recipe_set_extraction_result(recipe_id, status="error", ingredients=[])
        raise HTTPException(502, f"KI-Call fehlgeschlagen: {e}")
    if content is None:
        db.recipe_set_extraction_result(recipe_id, status="error", ingredients=[])
        raise HTTPException(502, "KI lieferte kein verwertbares Ergebnis")

    prepared = []
    for it in (content.get("ingredients") or []):
        prepared.append({
            "name": it.get("name") or "",
            "canonical_name": _canonical(it.get("name") or ""),
            "amount": it.get("amount"),
            "unit": normalize_unit(it.get("unit")),
            "raw": it.get("raw"),
        })
    steps = content.get("steps") or []
    servings = content.get("servings")

    # Auto-Tags (KI + Regel-Pass)
    from ..recipes.auto_tags import compute_diet_tags
    ki_tags = content.get("tags") or []
    diet_tags = compute_diet_tags([p["canonical_name"] for p in prepared])
    all_auto_tags = sorted(set(ki_tags) | set(diet_tags))
    db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=prepared,
        steps=steps,
        servings=servings,
        auto_tags=all_auto_tags,
    )

    return {
        "ok": True,
        "status": "ok",
        "ingredients_count": len(prepared),
        "steps_count": len(steps),
        "servings": servings,
        "auto_tags": all_auto_tags,
        "ingredients": db.recipe_ingredients_get(recipe_id),
        "steps": db.recipe_steps_get(recipe_id),
        "tags": db.recipe_tags_get(recipe_id),
    }


# ── Mutations: Rename / Delete / Merge ────────────────────────────────
# Diese Endpoints touchen das FS. Werden vom Audit-Dashboard für
# Inline-Aktionen genutzt. Logik liegt in app/recipes/manage.py mit
# Path-Traversal-Schutz + atomic FS/DB-Operations.

class RenamePayload(BaseModel):
    new_name: str
    rename_folder: bool = True


class DuplicatePayload(BaseModel):
    new_name: str = Field(min_length=1, max_length=200)


@router.post("/{recipe_id}/duplicate")
def duplicate_recipe(recipe_id: int, payload: DuplicatePayload) -> Dict[str, Any]:
    from ..recipes.manage import safe_duplicate_recipe

    try:
        return safe_duplicate_recipe(
            get_db(), recipe_id, new_name=payload.new_name
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/{recipe_id}/rename")
def rename_recipe(recipe_id: int, payload: RenamePayload, request: Request):
    from ..recipes.manage import safe_rename_recipe
    _version_before(recipe_id, request, "Rezept umbenannt")
    try:
        return safe_rename_recipe(
            get_db(), recipe_id,
            new_name=payload.new_name,
            rename_folder=payload.rename_folder,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        # Path-Konflikt oder Folder-Konflikt
        raise HTTPException(409, str(e))


class DeletePayload(BaseModel):
    delete_files: bool = True


@router.delete("/{recipe_id}")
def delete_recipe(recipe_id: int, request: Request, delete_files: bool = False, hard: bool = False):
    """Soft-Delete in Papierkorb (Default). Mit ?hard=true endgültig.
    ?delete_files=true entfernt den Folder zusätzlich (auch beim Soft-Delete —
    dann kann Restore die Files nicht zurückholen, nur den DB-Eintrag)."""
    from ..recipes.manage import safe_delete_recipe
    _version_before(recipe_id, request, "Rezept gelöscht" if hard else "In Papierkorb verschoben")
    try:
        return safe_delete_recipe(get_db(), recipe_id,
                                  delete_files=delete_files, hard=hard)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


# ════════ Papierkorb ════════════════════════════════════════════════════
@router.get("/trash/list")
def trash_list(limit: int = Query(200, ge=1, le=500),
               offset: int = Query(0, ge=0)) -> Dict[str, Any]:
    """Liste der Rezepte im Papierkorb (deleted_at IS NOT NULL),
    sortiert nach Löschzeit absteigend (zuletzt gelöscht oben)."""
    db = get_db()
    items = db.recipe_list(only_deleted=True, limit=limit, offset=offset)
    total = db.recipe_count_trash()
    # Für jedes Item: Anzahl Tage im Papierkorb + days_until_purge
    now = time.time()
    for it in items:
        # Interne UNIQUE-Platzhalter nicht an Clients ausliefern. Im
        # Papierkorb bleiben die ursprünglichen Werte sichtbar.
        it["url"] = it.get("url") or it.get("deleted_url")
        it["folder_path"] = it.get("deleted_folder_path") or it.get("folder_path")
        if it.get("deleted_at"):
            age_days = (now - it["deleted_at"]) / 86400.0
            it["days_in_trash"] = round(age_days, 1)
            it["days_until_purge"] = max(0, round(30 - age_days, 1))
    return {"items": items, "total": total}


@router.post("/{recipe_id}/restore")
def restore_recipe(recipe_id: int, request: Request) -> Dict[str, Any]:
    """Aus Papierkorb wiederherstellen (deleted_at = NULL)."""
    from ..recipes.manage import safe_restore_recipe

    _version_before(recipe_id, request, "Aus Papierkorb wiederhergestellt")
    try:
        result = safe_restore_recipe(get_db(), recipe_id)
        logger.info(
            "recipe #%s restored (files_restored=%s)",
            recipe_id,
            result.get("files_restored"),
        )
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/trash/empty")
def empty_trash(delete_files: bool = True) -> Dict[str, Any]:
    """Papierkorb endgültig leeren — alle Rezepte mit deleted_at IS NOT NULL
    werden HARD-DELETE'd. delete_files=True entfernt zusätzlich die FS-Folder
    (falls noch da)."""
    from ..recipes.manage import safe_delete_recipe
    db = get_db()
    with db.conn() as c:
        trash_ids = [
            int(row["id"])
            for row in c.execute(
                "SELECT id FROM recipes WHERE deleted_at IS NOT NULL "
                "ORDER BY deleted_at, id"
            ).fetchall()
        ]
    deleted = 0
    errors = []
    for recipe_id in trash_ids:
        item = db.recipe_get(recipe_id) or {"id": recipe_id}
        try:
            safe_delete_recipe(db, recipe_id, delete_files=delete_files, hard=True)
            deleted += 1
        except Exception as e:
            errors.append({"id": item["id"], "name": item.get("name"), "error": str(e)})
    logger.info(f"empty_trash: {deleted} purged, {len(errors)} errors")
    return {"ok": True, "purged": deleted, "errors": errors}


class MergePayload(BaseModel):
    source_id: int
    target_id: int
    delete_source: bool = True


@router.post("/merge")
def merge_recipes(payload: MergePayload, request: Request):
    from ..recipes.manage import safe_merge_recipes
    _version_before(payload.source_id, request, f"Mit Rezept #{payload.target_id} zusammengeführt")
    _version_before(payload.target_id, request, f"Rezept #{payload.source_id} übernommen")
    try:
        return safe_merge_recipes(
            get_db(),
            source_id=payload.source_id,
            target_id=payload.target_id,
            delete_source=payload.delete_source,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


# ── Static-File-Streaming ──────────────────────────────────────────────
# Thumbnail + Video werden NICHT via FastAPI.StaticFiles bedient, weil
# /mnt/rezepte außerhalb des App-Roots liegt UND der Pfad pro Rezept-DB-Eintrag
# bekannt sein muss (Schutz vor Path-Traversal). Die Endpoints:
#   - prüfen recipe_id → folder_path aus DB
#   - bedienen NUR die zwei DB-bekannten Filenames (thumb_filename, video_filename)
# Damit kann ein User unmöglich beliebige FS-Pfade rausziehen, selbst wenn er
# die filename-Parameter manipulieren könnte (gibt keine).

@router.post("/{recipe_id}/favorite")
def toggle_favorite(recipe_id: int) -> Dict[str, Any]:
    """Toggle is_favorite (0↔1) für ein Rezept."""
    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    new_state = 0 if rec.get("is_favorite") else 1
    with db.conn() as c:
        c.execute("UPDATE recipes SET is_favorite=? WHERE id=?", (new_state, recipe_id))
    return {"ok": True, "is_favorite": bool(new_state)}


@router.post("/{recipe_id}/rating")
def set_rating(recipe_id: int, value: int = Query(..., ge=0, le=5,
               description="Bewertung 0=ungerated, 1-5=Sterne")) -> Dict[str, Any]:
    """Setzt rating (0-5) für ein Rezept. 0 = unbewertet."""
    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    with db.conn() as c:
        c.execute("UPDATE recipes SET rating=? WHERE id=?", (value, recipe_id))
    return {"ok": True, "rating": value}


@router.get("/{recipe_id}/thumb")
def get_thumb(recipe_id: int, w: Optional[int] = Query(None, ge=64, le=2048,
              description="Optional: Breite in Pixel (z.B. 400). Resized via ffmpeg + cached on-disk.")):
    """Thumbnail-Endpoint. Mit ?w=400 wird das Original on-the-fly auf
    Breite 400px resized (Höhe proportional), Ergebnis als thumb-w400.jpg
    im Folder gecached. Beim nächsten Aufruf direkt vom Cache, kein
    ffmpeg-Aufruf mehr. ETag basiert auf Source-mtime damit invalidiert
    wenn das Original ersetzt wird."""
    from pathlib import Path
    import subprocess as _sp
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r:
        raise HTTPException(404, "rezept nicht gefunden")

    folder = _safe_recipe_folder(r)
    src = None

    # 1. Registriertes Thumbnail bevorzugen
    if r.get("thumb_filename"):
        try:
            src = _safe_recipe_file(r, r["thumb_filename"])
        except HTTPException:
            src = None

    # 2. Fallback: kein registriertes Thumb → im Folder nach Medien suchen.
    #    Deckt Email-Importe (PDF/Bild-Attachment) und nicht-registrierte
    #    Bilder ab. Reihenfolge: echtes Bild zuerst, dann PDF (1. Seite
    #    rendern). thumb-w*-Caches werden ignoriert (sind selbst erzeugt).
    if src is None and folder.is_dir():
        img_exts = {".jpg", ".jpeg", ".png", ".webp"}
        images = sorted(
            p for p in folder.iterdir()
            if p.is_file() and not p.is_symlink()
            and p.suffix.lower() in img_exts
            and not p.name.startswith("thumb-w")
        )
        if images:
            src = _safe_recipe_file(r, images[0].name)
        else:
            # PDF → erste Seite zu JPG rendern, on-disk cachen (pdf-page1.jpg)
            pdfs = sorted(p for p in folder.iterdir()
                          if p.is_file() and not p.is_symlink()
                          and p.suffix.lower() == ".pdf")
            if pdfs:
                pdf = _safe_recipe_file(r, pdfs[0].name)
                rendered = folder / "pdf-page1.jpg"
                if rendered.exists() and rendered.stat().st_mtime >= pdf.stat().st_mtime:
                    src = rendered
                else:
                    # pdftoppm rendert Seite 1; -singlefile hängt KEINE Endung
                    # an wenn man -o mit vollem Namen nutzt → wir geben den
                    # Prefix ohne .jpg an, pdftoppm ergänzt es.
                    try:
                        _sp.run(
                            ["pdftoppm", "-jpeg", "-r", "150", "-f", "1", "-l", "1",
                             "-singlefile", str(pdf), str(folder / "pdf-page1")],
                            check=True, timeout=20,
                        )
                        if rendered.exists():
                            src = rendered
                    except (_sp.CalledProcessError, _sp.TimeoutExpired, FileNotFoundError) as e:
                        logger.warning(f"pdf-render fail für #{recipe_id}: {e}")

    if src is None:
        raise HTTPException(404, "kein thumbnail")

    serve = src
    if w:
        try:
            serve = ensure_thumbnail(src, w)
        except Exception as e:
            # Fallback: Original ausliefern wenn Pillow das Format nicht lesen kann.
            logger.warning(f"thumb resize w={w} fail für #{recipe_id}: {e}")
            serve = src

    mtime = src.stat().st_mtime  # ETag immer auf SOURCE-mtime, nicht Cache
    return FileResponse(
        str(serve),
        headers={
            "Cache-Control": "private, max-age=86400, stale-while-revalidate=604800",
            "ETag": f'"{int(mtime)}-{serve.stat().st_size}"',
        },
    )


@router.get("/{recipe_id}/video")
def get_video(recipe_id: int):
    from pathlib import Path
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r or not r.get("video_filename"):
        raise HTTPException(404, "kein video")
    fp = _safe_recipe_file(r, r["video_filename"])
    # Range-Requests werden von FileResponse direkt unterstützt — wichtig
    # damit das <video>-Element im Browser Seek-Operationen kann.
    # Videos sind ~10-50MB pro Stück, cachen sich daher schnell auf.
    mtime = fp.stat().st_mtime
    return FileResponse(
        str(fp),
        headers={
            "Cache-Control": "private, max-age=86400, stale-while-revalidate=604800",
            "ETag": f'"{int(mtime)}-{fp.stat().st_size}"',
        },
    )
