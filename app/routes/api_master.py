"""Stammdaten-Verwaltung: Tags + canonical Zutaten-Namen.

Endpoints:
  GET    /api/master/tags                — alle Tags mit Nutzungs-Counts
  POST   /api/master/tags/rename         — Tag umbenennen oder mergen
  DELETE /api/master/tags/{tag_id}       — Tag komplett entfernen
  GET    /api/master/canonicals          — alle canonical-Namen mit Counts
  POST   /api/master/canonicals/rename   — canonical umbenennen/mergen

Die rename-Endpoints sind UPSERT-fähig: wenn der Ziel-Name schon existiert,
werden die Vorkommen gemergt statt unique-Konflikt. Das ist der typische
User-Workflow ('pasta' und 'Pasta' zusammenführen).

Kategorien (type/category) werden HIER nicht verwaltet — die hängen am
Folder-Pfad, brauchen FS-Move. Eigene Iteration falls gebraucht.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/master", tags=["master"], dependencies=[Depends(require_auth)])


# ─── Tags ────────────────────────────────────────────────────────────────
@router.get("/tags")
def list_tags() -> Dict[str, Any]:
    """Alle Tags mit Recipe-Counts. Sortiert nach Nutzung descending —
    häufige Tags oben, seltene + unbenutzte unten."""
    db = get_db()
    with db.conn() as c:
        rows = c.execute("""
            SELECT t.id, t.name,
                   COUNT(rt.recipe_id) as recipe_count,
                   COALESCE(SUM(CASE WHEN rt.auto=1 THEN 1 ELSE 0 END), 0) as auto_count,
                   COALESCE(SUM(CASE WHEN rt.auto=0 THEN 1 ELSE 0 END), 0) as user_count
            FROM tags t
            LEFT JOIN recipe_tags rt ON rt.tag_id = t.id
            GROUP BY t.id
            ORDER BY recipe_count DESC, t.name
        """).fetchall()
    return {"tags": [dict(r) for r in rows]}


class TagRename(BaseModel):
    old_name: str
    new_name: str


@router.post("/tags/rename")
def rename_tag(payload: TagRename) -> Dict[str, Any]:
    """Umbenennen ODER Mergen. Wenn new_name schon als Tag existiert,
    werden alle recipe_tags-Einträge von old auf new umgestellt; bei
    UNIQUE-Konflikt (Rezept hat beide Tags) wird der alte Eintrag entfernt."""
    db = get_db()
    old = payload.old_name.strip()
    new = payload.new_name.strip()
    if not old or not new:
        raise HTTPException(400, "old_name und new_name pflichtig")
    if old == new:
        return {"ok": True, "noop": True}

    with db.conn() as c:
        old_row = c.execute("SELECT id FROM tags WHERE name=?", (old,)).fetchone()
        if not old_row:
            raise HTTPException(404, f"Tag '{old}' nicht gefunden")
        old_id = int(old_row["id"])

        new_row = c.execute("SELECT id FROM tags WHERE name=?", (new,)).fetchone()
        if new_row:
            # MERGE-Fall: new existiert bereits
            new_id = int(new_row["id"])
            # Doppelt-Mappings vermeiden: lösche alte Einträge wo Rezept
            # bereits den neuen Tag hat (UNIQUE auf recipe_id+tag_id).
            c.execute("""
                DELETE FROM recipe_tags
                WHERE tag_id = ?
                AND recipe_id IN (SELECT recipe_id FROM recipe_tags WHERE tag_id = ?)
            """, (old_id, new_id))
            # Rest auf neuen tag_id umstellen
            c.execute("UPDATE recipe_tags SET tag_id=? WHERE tag_id=?", (new_id, old_id))
            c.execute("DELETE FROM tags WHERE id=?", (old_id,))
            return {"ok": True, "merged": True, "old_id": old_id, "new_id": new_id}

        # RENAME: nur den Tag-Namen ändern, keine recipe_tags-Anpassung nötig
        c.execute("UPDATE tags SET name=? WHERE id=?", (new, old_id))
        return {"ok": True, "renamed": True, "tag_id": old_id}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int) -> Dict[str, Any]:
    """Tag komplett entfernen. Recipes verlieren diesen Tag.
    Falls Auto-Tag und vom Worker neu gesetzt würde, kommt er ggf. zurück
    — das ist gewollt (User-Delete = soft, Worker-Set = hard rule)."""
    db = get_db()
    with db.conn() as c:
        row = c.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Tag nicht gefunden")
        c.execute("DELETE FROM recipe_tags WHERE tag_id=?", (tag_id,))
        c.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    return {"ok": True, "deleted": dict(row)["name"]}


# ─── Canonical Ingredients ──────────────────────────────────────────────
@router.get("/canonicals")
def list_canonicals() -> Dict[str, Any]:
    """Alle canonical_name-Werte mit Counts + den verschiedenen 'raw'-
    Schreibweisen (z.B. canonical 'tomate' → raw_names: 'Tomaten, frische Tomaten')."""
    db = get_db()
    with db.conn() as c:
        rows = c.execute("""
            SELECT canonical_name,
                   COUNT(*) as ingredient_count,
                   COUNT(DISTINCT recipe_id) as recipe_count,
                   GROUP_CONCAT(DISTINCT name) as raw_names
            FROM recipe_ingredients
            WHERE canonical_name IS NOT NULL AND canonical_name != ''
            GROUP BY canonical_name
            ORDER BY recipe_count DESC, canonical_name
        """).fetchall()
    return {"canonicals": [dict(r) for r in rows]}


class CanonicalRename(BaseModel):
    old_canonical: str
    new_canonical: str
    update_names: bool = False   # auch das angezeigte name-Feld mit-updaten?


@router.post("/canonicals/rename")
def rename_canonical(payload: CanonicalRename) -> Dict[str, Any]:
    """canonical_name in allen recipe_ingredients umstellen. Wenn new schon
    existiert, werden Vorkommen einfach gemergt (kein UNIQUE-Konflikt weil
    canonical_name nicht UNIQUE ist — mehrere Rezepte können die gleiche
    Zutat haben).

    update_names=true: auch das angezeigte name-Feld setzen. Default false,
    damit z.B. 'Roma-Tomaten' / 'Cherrytomaten' beide auf canonical 'tomate'
    zeigen können ohne ihre spezifische Anzeige zu verlieren."""
    db = get_db()
    old = payload.old_canonical.strip()
    new = payload.new_canonical.strip()
    if not old or not new:
        raise HTTPException(400, "old_canonical und new_canonical pflichtig")
    if old == new:
        return {"ok": True, "noop": True}

    with db.conn() as c:
        count = int(c.execute(
            "SELECT COUNT(*) FROM recipe_ingredients WHERE canonical_name=?",
            (old,),
        ).fetchone()[0])
        if count == 0:
            raise HTTPException(404, f"Canonical '{old}' nicht gefunden")

        if payload.update_names:
            c.execute(
                "UPDATE recipe_ingredients SET canonical_name=?, name=? WHERE canonical_name=?",
                (new, new, old),
            )
        else:
            c.execute(
                "UPDATE recipe_ingredients SET canonical_name=? WHERE canonical_name=?",
                (new, old),
            )
    return {"ok": True, "affected": count}
