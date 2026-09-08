"""Private Koch-Erfahrungen und transaktional geprüfte Importkorrekturen.

Alle Funktionen erhalten die bereits authentifizierte Identität vom Router.
Der Versionsvergleich findet unter derselben SQLite-Schreibsperre wie die
Änderung statt, damit auch nicht-kooperierende Hintergrundschreiber sicher sind.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .auto_tags import DIET_TAGS, compute_diet_tags
from .source_integrity import recipe_quality_report


class RevisionConflict(ValueError):
    pass


class EntryDeleted(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _snapshot(db: Any, connection: Any, recipe_id: int) -> dict:
    snapshot = db._recipe_snapshot_from_connection(connection, recipe_id)
    if not snapshot or snapshot["recipe"].get("deleted_at") is not None:
        raise LookupError("Rezept nicht gefunden")
    if snapshot["recipe"].get("ingredients_status") == "variant_pending":
        raise LookupError("Rezept nicht gefunden")
    return snapshot


def _content(snapshot: dict) -> dict:
    recipe = snapshot["recipe"]
    return {
        "name": recipe["name"],
        "source": {"url": recipe.get("url"), "description": recipe.get("description") or ""},
        "servings": recipe.get("servings"),
        "ingredients": [
            {key: item.get(key) for key in ("name", "amount", "unit", "raw")}
            for item in snapshot["ingredients"]
        ],
        "steps": [
            {
                "step_number": index,
                "instruction": item["instruction"],
                "timer_seconds": item.get("timer_seconds"),
            }
            for index, item in enumerate(snapshot["steps"], 1)
        ],
    }


def _memory_entry(row: Any, steps: list[dict]) -> dict:
    result = dict(row)
    for key in ("username", "request_hash", "deleted_at"):
        result.pop(key, None)
    index = result.get("step_number")
    result["step_is_current"] = index is None or (
        1 <= index <= len(steps) and steps[index - 1]["instruction"] == result["step_instruction"]
    )
    return result


def memory_list(db: Any, recipe_id: int, username: str, *, limit: int = 100, offset: int = 0) -> dict:
    with db.conn() as c:
        c.execute("BEGIN")
        snapshot = _snapshot(db, c, recipe_id)
        total = c.execute(
            "SELECT COUNT(*) FROM recipe_cooking_memory "
            "WHERE recipe_id=? AND username=? AND deleted_at IS NULL",
            (recipe_id, username),
        ).fetchone()[0]
        rows = c.execute(
            "SELECT * FROM recipe_cooking_memory WHERE recipe_id=? AND username=? AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (recipe_id, username, limit, offset),
        ).fetchall()
        return {"items": [_memory_entry(row, snapshot["steps"]) for row in rows], "total": total}


def memory_create(db: Any, recipe_id: int, username: str, payload: dict) -> dict:
    request_hash = _hash(payload)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        snapshot = _snapshot(db, c, recipe_id)
        existing = c.execute(
            "SELECT * FROM recipe_cooking_memory WHERE recipe_id=? AND username=? AND client_entry_id=?",
            (recipe_id, username, payload["client_entry_id"]),
        ).fetchone()
        if existing:
            if existing["deleted_at"] is not None:
                raise EntryDeleted("Diese Notiz wurde bereits gelöscht")
            if existing["request_hash"] != request_hash:
                raise RevisionConflict("Diese Notiz-ID wurde bereits mit anderem Inhalt verwendet")
            return _memory_entry(existing, snapshot["steps"])
        step_number = payload.get("step_number")
        if step_number is not None and (
            step_number > len(snapshot["steps"])
            or snapshot["steps"][step_number - 1]["instruction"] != payload.get("step_instruction")
        ):
            raise RevisionConflict(
                "Der Schritt wurde geändert. Rezept neu laden und den Hinweis erneut zuordnen."
            )
        count = c.execute(
            "SELECT COUNT(*) FROM recipe_cooking_memory WHERE recipe_id=? AND username=? AND deleted_at IS NULL",
            (recipe_id, username),
        ).fetchone()[0]
        if count >= 500:
            raise ValueError("Maximal 500 persönliche Notizen je Rezept; bitte ältere Notizen löschen")
        cur = c.execute(
            "INSERT INTO recipe_cooking_memory (recipe_id, username, client_entry_id, request_hash, created_at, "
            "note, adjustments, next_time, servings, step_number, step_instruction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipe_id,
                username,
                payload["client_entry_id"],
                request_hash,
                time.time(),
                payload["note"],
                payload["adjustments"],
                payload["next_time"],
                payload.get("servings"),
                step_number,
                payload.get("step_instruction"),
            ),
        )
        row = c.execute("SELECT * FROM recipe_cooking_memory WHERE id=?", (cur.lastrowid,)).fetchone()
        return _memory_entry(row, snapshot["steps"])


def memory_delete(db: Any, recipe_id: int, username: str, entry_id: int) -> None:
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _snapshot(db, c, recipe_id)
        updated = c.execute(
            "UPDATE recipe_cooking_memory SET deleted_at=COALESCE(deleted_at, ?), note='', adjustments='', "
            "next_time='', step_instruction=NULL, step_number=NULL, servings=NULL "
            "WHERE id=? AND recipe_id=? AND username=?",
            (time.time(), entry_id, recipe_id, username),
        ).rowcount
        if not updated:
            raise LookupError("Notiz nicht gefunden")


def memory_cancel(db: Any, recipe_id: int, username: str, client_entry_id: str) -> None:
    """Cancel by client ID without ever requiring the private draft content.

    A delayed POST may not have arrived yet. The unique tombstone and the same
    writer lock as memory_create make both arrival orders end in a deleted note.
    Tombstones are not pruned: an arbitrarily late retry must not resurrect data.
    """
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _snapshot(db, c, recipe_id)
        now = time.time()
        c.execute(
            "INSERT INTO recipe_cooking_memory "
            "(recipe_id, username, client_entry_id, request_hash, created_at, deleted_at) "
            "VALUES (?, ?, ?, '', ?, ?) "
            "ON CONFLICT(recipe_id, username, client_entry_id) DO UPDATE SET "
            "deleted_at=COALESCE(recipe_cooking_memory.deleted_at, excluded.deleted_at), "
            "request_hash='', note='', adjustments='', next_time='', "
            "step_instruction=NULL, step_number=NULL, servings=NULL",
            (recipe_id, username, client_entry_id, now, now),
        )


def _correction(row: Any) -> dict:
    result = dict(row)
    result.pop("request_hash", None)
    result["before"] = json.loads(result.pop("before_json"))
    result["proposed"] = json.loads(result.pop("proposed_json"))
    return result


def _source_status(c: Any, recipe: dict) -> str:
    from ..core.recipe_web import normalize_recipe_url

    raw_url = str(recipe.get("url") or "").strip()
    if not raw_url:
        return "missing"
    latest = c.execute(
        "SELECT state,content_sha256 FROM recipe_source_snapshots WHERE recipe_id=? AND source_url=? "
        "ORDER BY checked_at DESC,id DESC LIMIT 1",
        (recipe["id"], raw_url),
    ).fetchone()
    if not latest:
        return "unchecked" if normalize_recipe_url(raw_url) else "local"
    if latest["state"] == "unavailable":
        return "unavailable"
    baseline = c.execute(
        "SELECT content_sha256 FROM recipe_source_snapshots WHERE recipe_id=? AND source_url=? AND is_baseline=1 "
        "ORDER BY checked_at DESC,id DESC LIMIT 1",
        (recipe["id"], raw_url),
    ).fetchone()
    if baseline and latest["content_sha256"] and baseline["content_sha256"] != latest["content_sha256"]:
        return "changed"
    return "current"


def import_review(db: Any, recipe_id: int, username: str, can_apply: bool) -> dict:
    with db.conn() as c:
        c.execute("BEGIN")
        snapshot = _snapshot(db, c, recipe_id)
        content = _content(snapshot)
        rows = c.execute(
            "SELECT * FROM recipe_import_corrections WHERE recipe_id=? "
            + ("" if can_apply else "AND username=? ")
            + "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC, id DESC LIMIT 50",
            (recipe_id,) if can_apply else (recipe_id, username),
        ).fetchall()
        return {
            "recipe_id": recipe_id,
            **content,
            "revision": _hash(content),
            "can_apply": can_apply,
            "quality": recipe_quality_report(
                snapshot["recipe"],
                snapshot["ingredients"],
                snapshot["steps"],
                source_status=_source_status(c, snapshot["recipe"]),
            ),
            "corrections": [_correction(row) for row in rows],
        }


def _apply(db: Any, c: Any, row: Any, snapshot: dict, actor: str) -> dict:
    if row["status"] == "applied":
        return _correction(row)
    if row["status"] != "pending":
        raise RevisionConflict("Diese Korrektur wurde zurückgezogen oder verworfen")
    if _hash(_content(snapshot)) != row["expected_revision"]:
        raise RevisionConflict("Das Rezept wurde inzwischen geändert. Neu laden und Korrektur erneut prüfen.")
    recipe_id = row["recipe_id"]
    proposed = json.loads(row["proposed_json"])
    next_no = c.execute(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM recipe_versions WHERE recipe_id=?",
        (recipe_id,),
    ).fetchone()[0]
    now = time.time()
    version_id = c.execute(
        "INSERT INTO recipe_versions (recipe_id, version_no, created_at, created_by, source, reason, snapshot_json) "
        "VALUES (?, ?, ?, ?, 'import-review', ?, ?)",
        (recipe_id, next_no, now, actor, f"Importkorrektur #{row['id']}: {row['reason']}", _json(snapshot)),
    ).lastrowid
    ingredients_changed = _content(snapshot)["ingredients"] != [
        {key: item.get(key) for key in ("name", "amount", "unit", "raw")} for item in proposed["ingredients"]
    ]
    steps_changed = _content(snapshot)["steps"] != proposed["steps"]
    if ingredients_changed:
        c.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
        for index, item in enumerate(proposed["ingredients"]):
            c.execute(
                "INSERT INTO recipe_ingredients (recipe_id,name,canonical_name,amount,unit,raw,sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    recipe_id,
                    item["name"],
                    item["canonical_name"],
                    item["amount"],
                    item["unit"],
                    item["raw"],
                    index,
                ),
            )
        # Do not retain old manual safety/diet claims on changed ingredients.
        slots = ",".join("?" for _ in DIET_TAGS)
        c.execute(
            f"DELETE FROM recipe_tags WHERE recipe_id=? AND tag_id IN (SELECT id FROM tags WHERE lower(name) IN ({slots}))",
            (recipe_id, *sorted(DIET_TAGS)),
        )
        for name in compute_diet_tags([item["canonical_name"] for item in proposed["ingredients"]]):
            c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
            c.execute(
                "INSERT OR IGNORE INTO recipe_tags(recipe_id,tag_id,auto) SELECT ?,id,1 FROM tags WHERE name=?",
                (recipe_id, name),
            )
        from ..db import _rebuild_shopping_catalog_from_recipes

        _rebuild_shopping_catalog_from_recipes(c)
    if steps_changed:
        c.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,))
        for item in proposed["steps"]:
            c.execute(
                "INSERT INTO recipe_steps(recipe_id,step_number,instruction,timer_seconds) VALUES (?, ?, ?, ?)",
                (recipe_id, item["step_number"], item["instruction"], item["timer_seconds"]),
            )
        c.execute("DELETE FROM recipe_cooking_progress WHERE recipe_id=?", (recipe_id,))
    c.execute(
        "UPDATE recipes SET servings=?, ingredients_status='ok', ingredients_extracted_at=?, "
        "extraction_claimed_at=NULL, extraction_claim_owner=NULL, user_verified=0, verified_at=NULL, verified_by=NULL "
        "WHERE id=?",
        (proposed["servings"], now, recipe_id),
    )
    from ..db import _invalidate_recipe_nutrition

    _invalidate_recipe_nutrition(c, recipe_id)
    c.execute(
        "UPDATE recipe_import_corrections SET status='applied', applied_at=?, applied_by=?, version_id=? WHERE id=?",
        (now, actor, version_id, row["id"]),
    )
    return _correction(
        c.execute("SELECT * FROM recipe_import_corrections WHERE id=?", (row["id"],)).fetchone()
    )


def submit_correction(db: Any, recipe_id: int, username: str, can_apply: bool, payload: dict) -> dict:
    request_hash = _hash(payload)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        snapshot = _snapshot(db, c, recipe_id)
        existing = c.execute(
            "SELECT * FROM recipe_import_corrections WHERE recipe_id=? AND username=? AND client_request_id=?",
            (recipe_id, username, payload["client_request_id"]),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise RevisionConflict("Diese Korrektur-ID wurde bereits mit anderem Inhalt verwendet")
            return _correction(existing)
        content = _content(snapshot)
        if _hash(content) != payload["expected_revision"]:
            raise RevisionConflict(
                "Das Rezept wurde inzwischen geändert. Neu laden und Korrektur erneut prüfen."
            )
        proposed = {key: payload[key] for key in ("ingredients", "steps", "servings")}
        if all(content[key] == proposed[key] for key in ("steps", "servings")) and content["ingredients"] == [
            {key: item.get(key) for key in ("name", "amount", "unit", "raw")}
            for item in proposed["ingredients"]
        ]:
            raise ValueError("Die Korrektur enthält keine Änderung")
        pending = c.execute(
            "SELECT COUNT(*) FROM recipe_import_corrections WHERE recipe_id=? AND username=? AND status='pending'",
            (recipe_id, username),
        ).fetchone()[0]
        if not can_apply and pending >= 20:
            raise ValueError("Maximal 20 offene Korrekturen je Rezept und Person")
        cur = c.execute(
            "INSERT INTO recipe_import_corrections(recipe_id,username,client_request_id,request_hash,expected_revision, "
            "created_at,reason,status,before_json,proposed_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                recipe_id,
                username,
                payload["client_request_id"],
                request_hash,
                payload["expected_revision"],
                time.time(),
                payload["reason"],
                _json(content),
                _json(proposed),
            ),
        )
        row = c.execute("SELECT * FROM recipe_import_corrections WHERE id=?", (cur.lastrowid,)).fetchone()
        return _apply(db, c, row, snapshot, username) if can_apply else _correction(row)


def apply_correction(db: Any, recipe_id: int, correction_id: int, actor: str) -> dict:
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        snapshot = _snapshot(db, c, recipe_id)
        row = c.execute(
            "SELECT * FROM recipe_import_corrections WHERE recipe_id=? AND id=?",
            (recipe_id, correction_id),
        ).fetchone()
        if not row:
            raise LookupError("Korrektur nicht gefunden")
        return _apply(db, c, row, snapshot, actor)


def withdraw_correction(db: Any, recipe_id: int, correction_id: int, username: str, can_apply: bool) -> dict:
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _snapshot(db, c, recipe_id)
        row = c.execute(
            "SELECT * FROM recipe_import_corrections WHERE recipe_id=? AND id=? "
            + ("" if can_apply else "AND username=?"),
            (recipe_id, correction_id) if can_apply else (recipe_id, correction_id, username),
        ).fetchone()
        if not row:
            raise LookupError("Korrektur nicht gefunden")
        if row["status"] == "applied":
            raise RevisionConflict(
                "Übernommene Korrekturen können nur über eine neue Änderung korrigiert werden"
            )
        c.execute("UPDATE recipe_import_corrections SET status='withdrawn' WHERE id=?", (correction_id,))
        return _correction(
            c.execute("SELECT * FROM recipe_import_corrections WHERE id=?", (correction_id,)).fetchone()
        )
