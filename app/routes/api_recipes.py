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
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..core.analyzer import build_analyzer
from ..config_store import get_config
from ..db import get_db
from pathlib import Path
from ..recipes.canonical import canonical_name as _canonical

logger = logging.getLogger(__name__)
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
    sodass der Background-Worker das Rezept nicht überschreibt.

    Nach dem Save werden Diät-Tags (vegan/vegetarisch/laktosefrei/...) neu
    berechnet — User hat ggf. Fleisch entfernt oder Milchprodukte ergänzt,
    die alten Tags wären dann falsch. KI-Stil-Tags (italienisch, schnell,
    pasta) bleiben unangetastet weil die aus der Description kommen, die
    sich nicht geändert hat."""
    from ..recipes.auto_tags import compute_diet_tags, DIET_TAGS
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

    # Diät-Tags recompute: nimm existierende auto-Tags die NICHT in DIET_TAGS
    # sind (das sind die KI-Stil-Tags wie 'pasta', 'schnell') und merge sie
    # mit den frisch berechneten Diät-Tags aus der neuen Zutatenliste.
    try:
        current_tags = db.recipe_tags_get(recipe_id)
        non_diet_auto = [
            t["name"] for t in current_tags
            if t.get("auto") == 1 and t["name"] not in DIET_TAGS
        ]
        new_diet = compute_diet_tags([p["canonical_name"] for p in prepared])
        merged = sorted(set(non_diet_auto) | set(new_diet))
        db.recipe_auto_tags_set(recipe_id, merged)
    except Exception as e:
        # Diät-Tag-Recompute ist nice-to-have — bei Fehler nur loggen,
        # Save selbst ist erfolgreich.
        logger.warning(f"Rezept #{recipe_id}: diet-tag-recompute failed: {e}")

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


@router.post("/recover-empty")
def recover_empty() -> Dict[str, Any]:
    """Setzt ingredients_status='pending' für alle Rezepte die status IN
    ('ok','error') haben aber 0 Zutaten in recipe_ingredients. Meist alte
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
                WHERE r.ingredients_status IN ('ok', 'error')
                  AND r.description IS NOT NULL
                  AND length(r.description) >= 20
                GROUP BY r.id
                HAVING COUNT(ri.id) = 0
            """).fetchall()
        ids = [int(r["id"]) for r in rows]

        # 2. Write: alle IDs in einer Transaktion via executemany
        if ids:
            with db.conn() as c:
                c.executemany(
                    "UPDATE recipes SET ingredients_status='pending' WHERE id=?",
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
def rescrape_recipe(recipe_id: int) -> Dict[str, Any]:
    """Re-Scrape: ruft yt-dlp nochmal für die ursprüngliche URL auf und
    aktualisiert Caption + Thumbnail im Folder. Video wird NICHT neu
    heruntergeladen (--skip-download). Zutaten/Schritte bleiben unberührt.

    Use-Cases:
    - Rezepte ohne Thumbnail (Audit 'Kein Bild') → frisches Bild holen
    - TikTok/Instagram-Caption wurde aktualisiert → frischer Text für
      bessere KI-Extraktion
    - Bei beschädigtem .jpg im Folder: neu pullen

    Fehlerfälle: URL nicht da, yt-dlp-Fehler (Video gelöscht, geo-blocked,
    Login nötig). Returnt 200 mit ok=False + Detail bei Fehlern."""
    import shutil as _shutil
    db = get_db()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    url = rec.get("url")
    if not url:
        return {"ok": False, "error": "Rezept hat keine URL (manuell angelegt?)"}
    folder = rec.get("folder_path")
    if not folder or not Path(folder).exists():
        return {"ok": False, "error": f"FS-Folder nicht da: {folder}"}

    # Downloader bauen mit Config
    cfg = get_config()
    from ..core.downloader import VideoDownloader
    ytdlp_path = cfg.get("paths", "ytdlp", default="yt-dlp")
    temp_dir = Path(cfg.get("paths", "temp_dir", default="/tmp/scrapper"))
    cookies_file = cfg.get("downloader", "cookies_file", default=None)
    dl = VideoDownloader(ytdlp_path=ytdlp_path, temp_dir=temp_dir,
                          cookies_file=cookies_file)

    meta = dl.refresh_metadata(url)
    if not meta:
        return {"ok": False, "error": "yt-dlp lieferte nichts — URL down/geo-blocked/login nötig?"}

    folder_p = Path(folder)
    changed = {"description": False, "thumbnail": False}

    # Description aktualisieren
    new_desc = meta.get("description_text")
    if new_desc and new_desc.strip():
        old_desc = rec.get("description") or ""
        if new_desc != old_desc:
            with db.conn() as c:
                c.execute("UPDATE recipes SET description=? WHERE id=?",
                          (new_desc, recipe_id))
            # Plus im Folder als description.txt ablegen für Konsistenz
            try:
                (folder_p / "description.txt").write_text(new_desc, encoding="utf-8")
            except OSError as e:
                logger.warning(f"description.txt schreiben fehler: {e}")
            changed["description"] = True

    # Thumbnail ersetzen
    new_thumb = meta.get("thumbnail_path")
    if new_thumb and Path(new_thumb).exists():
        try:
            # Existing Thumbs im Folder löschen (jpg/jpeg/webp/png mit thumb-prefix
            # oder gleichem Stem wie folder-name)
            for old in folder_p.glob("thumb.*"):
                old.unlink(missing_ok=True)
            for old in folder_p.glob("*.jpg"):
                # Nur Thumb-Dateien — kein User-Foto
                if old.name.startswith("thumb") or old.stem == folder_p.name:
                    old.unlink(missing_ok=True)
            target_thumb = folder_p / "thumb.jpg"
            _shutil.copy2(new_thumb, target_thumb)
            with db.conn() as c:
                c.execute("UPDATE recipes SET thumb_filename=? WHERE id=?",
                          ("thumb.jpg", recipe_id))
            changed["thumbnail"] = True
            # Tempdir aufräumen
            try:
                _shutil.rmtree(Path(new_thumb).parent, ignore_errors=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Thumbnail-Copy fehler #{recipe_id}: {e}")

    logger.info(f"rescrape #{recipe_id} '{rec.get('name')}': {changed}")
    return {
        "ok": True,
        "description_updated": changed["description"],
        "thumbnail_updated": changed["thumbnail"],
        "any_change": changed["description"] or changed["thumbnail"],
    }


@router.post("/{recipe_id}/verify")
def toggle_verify(recipe_id: int, request: Request,
                    verified: bool = Query(True)) -> Dict[str, Any]:
    """Toggle 'manuell geprüft'-Flag. Verifizierte Rezepte werden aus den
    Audit-Daten-Lücken-Listen ausgeschlossen — User-Override über KI-Heuristik.
    Audit-Trail: speichert username + Timestamp."""
    from ..auth import SESSION_COOKIE, session_user
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    username = session_user(request.cookies.get(SESSION_COOKIE, "")) or "?"
    db.recipe_set_verified(recipe_id, verified, username if verified else None)
    logger.info(f"verify #{recipe_id}: {verified} von '{username}'")
    return {"ok": True, "verified": verified, "by": username}


@router.post("/{recipe_id}/nutrition")
def compute_nutrition_for(recipe_id: int) -> Dict[str, Any]:
    """On-Demand Nährwert-Berechnung für ein Rezept. KI-Single-Call.
    Setzt calories_per_serving + protein/carbs/fat_g + computed_at."""
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        raise HTTPException(404, "Rezept nicht gefunden")
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

    db.recipe_set_nutrition(
        recipe_id, nutr["calories"], nutr["protein_g"],
        nutr["carbs_g"], nutr["fat_g"],
    )
    return {"ok": True, **nutr}


@router.post("/compute-nutrition-bulk")
def compute_nutrition_bulk(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
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
                db.recipe_set_nutrition(
                    int(r["id"]), nutr["calories"], nutr["protein_g"],
                    nutr["carbs_g"], nutr["fat_g"],
                )
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

    # Auto-Tags (KI + Regel-Pass)
    from ..recipes.auto_tags import compute_diet_tags
    ki_tags = content.get("tags") or []
    diet_tags = compute_diet_tags([p["canonical_name"] for p in prepared])
    all_auto_tags = sorted(set(ki_tags) | set(diet_tags))
    db.recipe_auto_tags_set(recipe_id, all_auto_tags)

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


@router.put("/{recipe_id}/rename")
def rename_recipe(recipe_id: int, payload: RenamePayload):
    from ..recipes.manage import safe_rename_recipe
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
def delete_recipe(recipe_id: int, delete_files: bool = True):
    """DELETE mit Query-Param `?delete_files=false` falls Files behalten werden sollen."""
    from ..recipes.manage import safe_delete_recipe
    try:
        return safe_delete_recipe(get_db(), recipe_id, delete_files=delete_files)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


class MergePayload(BaseModel):
    source_id: int
    target_id: int
    delete_source: bool = True


@router.post("/merge")
def merge_recipes(payload: MergePayload):
    from ..recipes.manage import safe_merge_recipes
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
