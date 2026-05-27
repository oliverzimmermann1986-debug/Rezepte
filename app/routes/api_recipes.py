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

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..core.analyzer import build_analyzer
from ..config_store import get_config
from ..db import get_db
from ..recipes.canonical import canonical_name as _canonical
from ..recipes.indexer import (
    ensure_extraction_running,
    is_extraction_running,
    sync_filesystem,
)
from ..recipes.units import normalize_unit

router = APIRouter(prefix="/api/recipes", tags=["recipes"], dependencies=[Depends(require_auth)])


# ── Listing ─────────────────────────────────────────────────────────────

@router.get("")
def list_recipes(
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    folder: Optional[str] = Query(None, description="Pfad-Präfix, z.B. /mnt/rezepte/Hauptgericht"),
    tag_id: Optional[List[int]] = Query(None),
    ingredient: Optional[List[str]] = Query(None, description="canonical_name(s), AND-verknüpft"),
    search: Optional[str] = Query(None),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Hauptlisten-Endpoint. Lazy-Sync + lazy-Extraction Trigger."""
    db = get_db()

    # Lazy-Sync: beim ERSTEN Aufruf (oder wenn 0 Rezepte indiziert sind)
    # einmal das FS scannen. Schnell auch bei >500 Ordnern (~100ms).
    if db.recipe_count() == 0:
        sync_filesystem(db)

    # Lazy-Background-Extraction starten (no-op wenn nichts pending)
    ensure_extraction_running()

    items = db.recipe_list(
        type=type,
        category=category,
        folder_prefix=folder,
        tag_ids=tag_id,
        ingredient_canonical=ingredient,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = db.recipe_count(
        type=type,
        category=category,
        folder_prefix=folder,
        tag_ids=tag_id,
        ingredient_canonical=ingredient,
        search=search,
    )

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
        })
    return {"total": total, "items": out, "extraction_running": is_extraction_running()}


@router.get("/facets")
def facets():
    """Filter-Optionen für die Sidebar: distincts Types, Categories, Tags,
       und Top-20-Zutaten nach Frequenz."""
    db = get_db()
    with db.conn() as c:
        types = [r[0] for r in c.execute(
            "SELECT DISTINCT type FROM recipes WHERE type IS NOT NULL AND type != '' ORDER BY type"
        ).fetchall()]
        cats = [r[0] for r in c.execute(
            "SELECT DISTINCT category FROM recipes WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()]
    return {
        "types": types,
        "categories": cats,
        "tags": db.tag_list(),
        "ingredients": db.ingredients_known()[:50],  # Top 50 für Sidebar
    }


# ── Detail ──────────────────────────────────────────────────────────────

@router.get("/{recipe_id}")
def get_recipe(recipe_id: int):
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r:
        raise HTTPException(404, "Rezept nicht gefunden")
    r["ingredients"] = db.recipe_ingredients_get(recipe_id)
    r["steps"] = db.recipe_steps_get(recipe_id)
    r["tags"] = db.recipe_tags_get(recipe_id)
    return r


# ── Mutation ────────────────────────────────────────────────────────────

class TagsUpdate(BaseModel):
    tags: List[str] = Field(default_factory=list)


@router.put("/{recipe_id}/tags")
def update_tags(recipe_id: int, payload: TagsUpdate):
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    db.recipe_tags_set(recipe_id, payload.tags)
    return {"ok": True, "tags": db.recipe_tags_get(recipe_id)}


class IngredientIn(BaseModel):
    name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class IngredientsUpdate(BaseModel):
    ingredients: List[IngredientIn]


@router.put("/{recipe_id}/ingredients")
def update_ingredients(recipe_id: int, payload: IngredientsUpdate):
    """Manuelle Override der Zutatenliste. Setzt ingredients_status='ok',
    sodass der Background-Worker das Rezept nicht überschreibt."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
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
    return {"ok": True, "ingredients": db.recipe_ingredients_get(recipe_id)}


class StepIn(BaseModel):
    instruction: str
    timer_seconds: Optional[int] = None


class StepsUpdate(BaseModel):
    steps: List[StepIn]


@router.put("/{recipe_id}/steps")
def update_steps(recipe_id: int, payload: StepsUpdate):
    """Manuelles Override der Zubereitungs-Schritte. step_number wird beim
    Insert automatisch aus der Listen-Position abgeleitet (1-basiert),
    sodass das Frontend nur die Reihenfolge ändern muss."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    db.recipe_steps_set(recipe_id, [s.model_dump() for s in payload.steps])
    return {"ok": True, "steps": db.recipe_steps_get(recipe_id)}


class ServingsUpdate(BaseModel):
    servings: Optional[int] = None


@router.put("/{recipe_id}/servings")
def update_servings(recipe_id: int, payload: ServingsUpdate):
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    db.recipe_set_servings(recipe_id, payload.servings)
    return {"ok": True, "servings": payload.servings}


# ── Sync + Extraction ──────────────────────────────────────────────────

@router.post("/sync")
def post_sync():
    """Manueller FS→DB-Resync via Frontend-Button."""
    return sync_filesystem(get_db())


@router.get("/extraction/status")
def extraction_status():
    db = get_db()
    return {
        "running": is_extraction_running(),
        "stats": db.recipes_extraction_stats(),
    }


@router.post("/{recipe_id}/extract")
def extract_one(recipe_id: int, background_tasks: BackgroundTasks):
    """Manueller Trigger: extrahiert (oder re-extrahiert) Zutaten + Schritte +
    Portionen für EIN Rezept synchron. Single KI-Call via analyze_recipe_content."""
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise HTTPException(404, "Rezept nicht gefunden")

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
        content = analyzer.analyze_recipe_content(desc)
    except Exception as e:
        db.recipe_set_extraction_result(recipe_id, status="error", ingredients=[])
        raise HTTPException(502, f"KI-Call fehlgeschlagen: {e}")

    prepared = []
    for it in (content.get("ingredients") or []):
        prepared.append({
            "name": it.get("name") or "",
            "canonical_name": _canonical(it.get("name") or ""),
            "amount": it.get("amount"),
            "unit": normalize_unit(it.get("unit")),
            "raw": it.get("raw"),
        })
    db.recipe_set_extraction_result(recipe_id, status="ok", ingredients=prepared)

    steps = content.get("steps") or []
    if steps:
        db.recipe_steps_set(recipe_id, steps)
    servings = content.get("servings")
    if servings is not None:
        db.recipe_set_servings(recipe_id, servings)

    return {
        "ok": True,
        "status": "ok",
        "ingredients_count": len(prepared),
        "steps_count": len(steps),
        "servings": servings,
        "ingredients": db.recipe_ingredients_get(recipe_id),
        "steps": db.recipe_steps_get(recipe_id),
    }


# ── Static-File-Streaming ──────────────────────────────────────────────
# Thumbnail + Video werden NICHT via FastAPI.StaticFiles bedient, weil
# /mnt/rezepte außerhalb des App-Roots liegt UND der Pfad pro Rezept-DB-Eintrag
# bekannt sein muss (Schutz vor Path-Traversal). Die Endpoints:
#   - prüfen recipe_id → folder_path aus DB
#   - bedienen NUR die zwei DB-bekannten Filenames (thumb_filename, video_filename)
# Damit kann ein User unmöglich beliebige FS-Pfade rausziehen, selbst wenn er
# die filename-Parameter manipulieren könnte (gibt keine).

@router.get("/{recipe_id}/thumb")
def get_thumb(recipe_id: int):
    from pathlib import Path
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r or not r.get("thumb_filename"):
        raise HTTPException(404, "kein thumbnail")
    fp = Path(r["folder_path"]) / r["thumb_filename"]
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "thumbnail-datei fehlt")
    return FileResponse(str(fp))


@router.get("/{recipe_id}/video")
def get_video(recipe_id: int):
    from pathlib import Path
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r or not r.get("video_filename"):
        raise HTTPException(404, "kein video")
    fp = Path(r["folder_path"]) / r["video_filename"]
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "video-datei fehlt")
    # FileResponse setzt automatisch korrekten Content-Type via Mimetype.
    # Range-Requests werden von FileResponse direkt unterstützt — wichtig
    # damit das <video>-Element im Browser Seek-Operationen kann.
    return FileResponse(str(fp))
