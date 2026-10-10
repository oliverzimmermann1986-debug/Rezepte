"""Household cookbooks, cooking memories, and collaborative meal wishes.

All authorization predicates are repeated inside write transactions. Household
IDs and actor identity come from the server, never the request body.
"""
from __future__ import annotations

import io
import sqlite3
import time
import warnings
from dataclasses import dataclass
from datetime import date, timedelta

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from ..db import RECIPE_VARIANT_PENDING_STATUS


def migrate_schema(c):
    if "cooked_by_user_id" not in {row[1] for row in c.execute("PRAGMA table_info(recipe_cook_history)")}:
        c.execute("ALTER TABLE recipe_cook_history ADD COLUMN cooked_by_user_id INTEGER")
        # Only accounts which already existed when cooking can own old entries.
        # Persist the ID once: a reused username must never acquire old notes.
        c.execute("UPDATE recipe_cook_history SET cooked_by_user_id=(SELECT u.id FROM users u "
                  "WHERE u.username=recipe_cook_history.cooked_by COLLATE NOCASE "
                  "AND u.created_at<=recipe_cook_history.cooked_at)")
    statements = [
        """CREATE TABLE IF NOT EXISTS household_cookbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
            name TEXT NOT NULL, name_key TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(account_id,name_key))""",
        """CREATE TABLE IF NOT EXISTS household_cookbook_recipes (
            cookbook_id INTEGER NOT NULL REFERENCES household_cookbooks(id) ON DELETE CASCADE,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            added_at REAL NOT NULL, PRIMARY KEY(cookbook_id,recipe_id))""",
        """CREATE TABLE IF NOT EXISTS recipe_cook_notes (
            history_id INTEGER PRIMARY KEY REFERENCES recipe_cook_history(id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', photo BLOB,
            updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS household_meal_wishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
            week_start TEXT NOT NULL, recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_by TEXT NOT NULL, created_by_key TEXT NOT NULL, created_at REAL NOT NULL,
            planned_for TEXT, planned_servings INTEGER,
            UNIQUE(account_id,week_start,recipe_id))""",
        """CREATE TABLE IF NOT EXISTS household_meal_wish_votes (
            wish_id INTEGER NOT NULL REFERENCES household_meal_wishes(id) ON DELETE CASCADE,
            actor_key TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(wish_id,actor_key))""",
        "CREATE INDEX IF NOT EXISTS idx_cook_notes_account ON recipe_cook_notes(account_id)",
        """CREATE TRIGGER IF NOT EXISTS delete_household_features AFTER DELETE ON user_accounts BEGIN
            DELETE FROM household_cookbooks WHERE account_id=OLD.id;
            DELETE FROM recipe_cook_notes WHERE account_id=OLD.id;
            DELETE FROM household_meal_wishes WHERE account_id=OLD.id;
        END""",
        """CREATE TRIGGER IF NOT EXISTS delete_meal_votes_for_user AFTER DELETE ON users BEGIN
            DELETE FROM household_meal_wish_votes WHERE actor_key='user:' || OLD.id;
        END""",
    ]
    for statement in statements:
        c.execute(statement)
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(270,?,?)",
              ("household_cookbooks_notes_and_meal_wishes", time.time()))


def merge_household_features(c, source, target):
    """Merge matching collections and votes without dropping cooking memories."""
    for book in c.execute("SELECT * FROM household_cookbooks WHERE account_id=?", (source,)).fetchall():
        existing = c.execute("SELECT id FROM household_cookbooks WHERE account_id=? AND name_key=?",
                             (target, book["name_key"])).fetchone()
        if existing:
            c.execute("INSERT OR IGNORE INTO household_cookbook_recipes(cookbook_id,recipe_id,added_at) "
                      "SELECT ?,recipe_id,added_at FROM household_cookbook_recipes WHERE cookbook_id=?",
                      (existing["id"], book["id"]))
            c.execute("DELETE FROM household_cookbooks WHERE id=?", (book["id"],))
        else:
            c.execute("UPDATE household_cookbooks SET account_id=? WHERE id=?", (target, book["id"]))
    c.execute("UPDATE recipe_cook_notes SET account_id=? WHERE account_id=?", (target, source))
    for wish in c.execute("SELECT * FROM household_meal_wishes WHERE account_id=?", (source,)).fetchall():
        existing = c.execute("SELECT * FROM household_meal_wishes WHERE account_id=? AND week_start=? AND recipe_id=?",
                             (target, wish["week_start"], wish["recipe_id"])).fetchone()
        if existing:
            c.execute("INSERT OR IGNORE INTO household_meal_wish_votes(wish_id,actor_key,created_at) "
                      "SELECT ?,actor_key,created_at FROM household_meal_wish_votes WHERE wish_id=?",
                      (existing["id"], wish["id"]))
            # Both actual meal-plan entries have already been preserved by the
            # tenancy merge. Keep the destination wish's planning receipt.
            if existing["planned_for"] is None and wish["planned_for"] is not None:
                c.execute("UPDATE household_meal_wishes SET planned_for=?,planned_servings=? WHERE id=?",
                          (wish["planned_for"], wish["planned_servings"], existing["id"]))
            c.execute("DELETE FROM household_meal_wishes WHERE id=?", (wish["id"],))
        else:
            c.execute("UPDATE household_meal_wishes SET account_id=? WHERE id=?", (target, wish["id"]))


def claim_legacy_features(c, target):
    # Migration 232 also calls legacy claiming before these tables exist.
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='household_cookbooks'").fetchone():
        return
    merge_household_features(c, 0, target)
    # Cooking history is attributed to its original user's household during
    # legacy claiming; its attached memory must follow the same history row.
    c.execute("UPDATE recipe_cook_notes SET account_id=(SELECT h.account_id FROM recipe_cook_history h "
              "WHERE h.id=recipe_cook_notes.history_id)")


@dataclass(frozen=True)
class Actor:
    username: str
    key: str
    is_admin: bool = False
    is_guest: bool = False


def account_id(db):
    return int(getattr(db, "account_id", 0))


def writable(actor):
    if actor.is_guest:
        raise HTTPException(403, "Gäste können ansehen, aber nichts verändern")


def _visible(c, db, recipe_id):
    row = c.execute("SELECT * FROM recipes WHERE id=? AND deleted_at IS NULL "
                    "AND COALESCE(ingredients_status,'')<>? AND (owner_account_id IS NULL OR owner_account_id=?)",
                    (recipe_id, RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchone()
    if not row:
        raise HTTPException(404, "Rezept nicht gefunden")
    return row


def _book(c, db, book_id):
    row = c.execute("SELECT * FROM household_cookbooks WHERE id=? AND account_id=?",
                    (book_id, account_id(db))).fetchone()
    if not row:
        raise HTTPException(404, "Kochbuch nicht gefunden")
    return row


def _book_item(c, db, book, recipe_id=None):
    row = c.execute("SELECT COUNT(*) AS n,COALESCE(MAX(r.id=?),0) AS contains_recipe "
                    "FROM household_cookbook_recipes cr JOIN recipes r ON r.id=cr.recipe_id "
                    "WHERE cr.cookbook_id=? AND r.deleted_at IS NULL "
                    "AND COALESCE(r.ingredients_status,'')<>? AND (r.owner_account_id IS NULL OR r.owner_account_id=?)",
                    (recipe_id, book["id"], RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchone()
    return {"id": book["id"], "name": book["name"], "recipe_count": row["n"],
            "contains_recipe": bool(row["contains_recipe"])}


def cookbooks(db, recipe_id=None):
    with db.conn() as c:
        if recipe_id is not None:
            _visible(c, db, recipe_id)
        books = c.execute("SELECT * FROM household_cookbooks WHERE account_id=? ORDER BY name_key,id",
                          (account_id(db),)).fetchall()
        return {"items": [_book_item(c, db, book, recipe_id) for book in books]}


def save_cookbook(db, actor, name, book_id=None):
    writable(actor)
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(422, "Kochbuchname muss 1 bis 80 Zeichen enthalten")
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if book_id is None:
            c.execute("INSERT OR IGNORE INTO household_cookbooks(account_id,name,name_key,created_at) VALUES(?,?,?,?)",
                      (account_id(db), name, name.casefold(), time.time()))
            book = c.execute("SELECT * FROM household_cookbooks WHERE account_id=? AND name_key=?",
                             (account_id(db), name.casefold())).fetchone()
        else:
            _book(c, db, book_id)
            try:
                c.execute("UPDATE household_cookbooks SET name=?,name_key=? WHERE id=? AND account_id=?",
                          (name, name.casefold(), book_id, account_id(db)))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "Ein Kochbuch mit diesem Namen existiert bereits") from exc
            book = _book(c, db, book_id)
        return {"item": _book_item(c, db, book)}


def delete_cookbook(db, actor, book_id):
    writable(actor)
    with db.conn() as c:
        c.execute("DELETE FROM household_cookbooks WHERE id=? AND account_id=?", (book_id, account_id(db)))
    return {"ok": True}


def cookbook_member(db, actor, book_id, recipe_id, present):
    writable(actor)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        book = _book(c, db, book_id)
        _visible(c, db, recipe_id)
        if present:
            c.execute("INSERT OR IGNORE INTO household_cookbook_recipes(cookbook_id,recipe_id,added_at) VALUES(?,?,?)",
                      (book_id, recipe_id, time.time()))
        else:
            c.execute("DELETE FROM household_cookbook_recipes WHERE cookbook_id=? AND recipe_id=?", (book_id, recipe_id))
        return {"item": _book_item(c, db, book, recipe_id)}


def cookbook_recipes(db, book_id):
    with db.conn() as c:
        _book(c, db, book_id)
        rows = c.execute("SELECT r.*, (SELECT COUNT(*) FROM recipe_ingredients ri WHERE ri.recipe_id=r.id) AS ingredients_count, "
                         "(SELECT COUNT(*) FROM recipe_steps rs WHERE rs.recipe_id=r.id) AS steps_count "
                         "FROM household_cookbook_recipes cr JOIN recipes r ON r.id=cr.recipe_id "
                         "WHERE cr.cookbook_id=? AND r.deleted_at IS NULL "
                         "AND COALESCE(r.ingredients_status,'')<>? AND (r.owner_account_id IS NULL OR r.owner_account_id=?) ORDER BY cr.added_at DESC,r.id DESC",
                         (book_id, RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchall()
    items = db._present_recipes(rows) if hasattr(db, "_present_recipes") else [dict(row) for row in rows]
    keys = ("id", "name", "type", "category", "url", "thumb_filename",
            "ingredients_status", "visibility", "verified_by", "servings", "ingredients_count", "steps_count")
    result = []
    for row in items:
        item = {key: row.get(key) for key in keys}
        item.update({key: bool(row.get(key)) for key in ("is_favorite", "in_library", "can_edit", "user_verified")})
        item.update(rating=row.get("rating") or 0, description=(row.get("description") or "").strip()[:220],
                    needs_manual_care=not row["ingredients_count"] or not row["steps_count"])
        result.append(item)
    return {"items": result}


def _history(c, db, history_id):
    row = c.execute("SELECT h.*,n.note,n.photo IS NOT NULL AS has_photo,n.updated_at FROM recipe_cook_history h "
                    "JOIN recipes r ON r.id=h.recipe_id LEFT JOIN recipe_cook_notes n "
                    "ON n.history_id=h.id AND n.account_id=h.account_id WHERE h.id=? AND h.account_id=? "
                    "AND r.deleted_at IS NULL AND COALESCE(r.ingredients_status,'')<>? "
                    "AND (r.owner_account_id IS NULL OR r.owner_account_id=?)",
                    (history_id, account_id(db), RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchone()
    if not row:
        raise HTTPException(404, "Kocheintrag nicht gefunden")
    return row


def _can_edit_note(actor, row):
    return not actor.is_guest and (actor.is_admin or (
        row["cooked_by_user_id"] is not None and actor.key == f"user:{row['cooked_by_user_id']}"
    ))


def _note_item(actor, row):
    return {"history_id": row["id"], "cooked_at": row["cooked_at"], "cooked_by": row["cooked_by"],
            "servings": row["servings"], "note": row["note"] or "", "can_edit": _can_edit_note(actor, row),
            "photo_url": f"/api/cook-notes/{row['id']}/photo?v={row['updated_at']}" if row["has_photo"] else None,
            "updated_at": row["updated_at"] or row["cooked_at"]}


def cook_notes(db, actor, recipe_id):
    with db.conn() as c:
        _visible(c, db, recipe_id)
        ids = c.execute("SELECT id FROM recipe_cook_history WHERE recipe_id=? AND account_id=? ORDER BY cooked_at DESC,id DESC",
                        (recipe_id, account_id(db))).fetchall()
        return {"items": [_note_item(actor, _history(c, db, row["id"])) for row in ids]}


def save_note(db, actor, history_id, *, note=None, photo=None, change_photo=False):
    writable(actor)
    if note is not None and len(note) > 4000:
        raise HTTPException(422, "Notizen dürfen höchstens 4000 Zeichen enthalten")
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _history(c, db, history_id)
        if not _can_edit_note(actor, row):
            raise HTTPException(403, "Nur die kochende Person oder ein Administrator kann diesen Eintrag bearbeiten")
        c.execute("INSERT OR IGNORE INTO recipe_cook_notes(history_id,account_id,updated_at) VALUES(?,?,?)",
                  (history_id, account_id(db), time.time()))
        if change_photo:
            c.execute("UPDATE recipe_cook_notes SET photo=?,updated_at=? WHERE history_id=? AND account_id=?",
                      (photo, time.time(), history_id, account_id(db)))
        else:
            c.execute("UPDATE recipe_cook_notes SET note=?,updated_at=? WHERE history_id=? AND account_id=?",
                      (note or "", time.time(), history_id, account_id(db)))
        return {"item": _note_item(actor, _history(c, db, history_id))}


def note_photo(db, history_id):
    with db.conn() as c:
        _history(c, db, history_id)
        row = c.execute("SELECT photo FROM recipe_cook_notes WHERE history_id=? AND account_id=?",
                        (history_id, account_id(db))).fetchone()
        if not row or not row["photo"]:
            raise HTTPException(404, "Foto nicht gefunden")
        return bytes(row["photo"])


def normalize_photo(data):
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Foto darf höchstens 8 MB groß sein")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20_000_000:
                    raise ValueError("unsupported image")
                if max(source.size) > 8192:
                    raise ValueError("oversized image")
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                normalized.thumbnail((2048, 2048))
                output = io.BytesIO()
                normalized.save(output, format="JPEG", quality=85, optimize=True)
                result = output.getvalue()
                if len(result) > 2 * 1024 * 1024:
                    raise ValueError("normalized image too large")
                return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "Bitte ein gültiges JPEG-, PNG- oder WebP-Foto mit höchstens 20 Megapixeln auswählen") from exc


def week_monday(value):
    try:
        selected = date.fromisoformat(value)
        if selected.isoformat() != value:
            raise ValueError("format")
        return (selected - timedelta(days=selected.weekday())).isoformat()
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Datum muss YYYY-MM-DD sein") from exc


def _wish(c, db, wish_id):
    row = c.execute("SELECT w.*,r.name AS recipe_name FROM household_meal_wishes w JOIN recipes r ON r.id=w.recipe_id "
                    "WHERE w.id=? AND w.account_id=? AND r.deleted_at IS NULL "
                    "AND COALESCE(r.ingredients_status,'')<>? AND (r.owner_account_id IS NULL OR r.owner_account_id=?)",
                    (wish_id, account_id(db), RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchone()
    if not row:
        raise HTTPException(404, "Wunsch nicht gefunden")
    return row


def _wish_item(c, actor, row):
    votes = c.execute("SELECT COUNT(*) AS n,COALESCE(MAX(actor_key=?),0) AS mine FROM household_meal_wish_votes WHERE wish_id=?",
                      (actor.key, row["id"])).fetchone()
    return {**{key: row[key] for key in ("id", "recipe_id", "recipe_name", "created_by", "planned_for", "planned_servings")},
            "votes": votes["n"], "my_vote": bool(votes["mine"]),
            "can_delete": not actor.is_guest and (actor.is_admin or row["created_by_key"] == actor.key)}


def meal_wishes(db, actor, week_start):
    week_start = week_monday(week_start)
    with db.conn() as c:
        ids = c.execute("SELECT w.id FROM household_meal_wishes w JOIN recipes r ON r.id=w.recipe_id "
                        "WHERE w.account_id=? AND w.week_start=? AND r.deleted_at IS NULL "
                        "AND COALESCE(r.ingredients_status,'')<>? AND (r.owner_account_id IS NULL OR r.owner_account_id=?) ORDER BY w.created_at,w.id",
                        (account_id(db), week_start, RECIPE_VARIANT_PENDING_STATUS, account_id(db))).fetchall()
        items = [_wish_item(c, actor, _wish(c, db, row["id"])) for row in ids]
    items.sort(key=lambda item: (-item["votes"], item["id"]))
    return {"week_start": week_start, "items": items}


def add_wish(db, actor, week_start, recipe_id):
    writable(actor)
    week_start = week_monday(week_start)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _visible(c, db, recipe_id)
        c.execute("INSERT OR IGNORE INTO household_meal_wishes(account_id,week_start,recipe_id,created_by,created_by_key,created_at) "
                  "VALUES(?,?,?,?,?,?)", (account_id(db), week_start, recipe_id, actor.username, actor.key, time.time()))
        row = c.execute("SELECT id FROM household_meal_wishes WHERE account_id=? AND week_start=? AND recipe_id=?",
                        (account_id(db), week_start, recipe_id)).fetchone()
        return {"item": _wish_item(c, actor, _wish(c, db, row["id"]))}


def vote_wish(db, actor, wish_id, voted):
    writable(actor)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _wish(c, db, wish_id)
        if voted:
            c.execute("INSERT OR IGNORE INTO household_meal_wish_votes(wish_id,actor_key,created_at) VALUES(?,?,?)",
                      (wish_id, actor.key, time.time()))
        else:
            c.execute("DELETE FROM household_meal_wish_votes WHERE wish_id=? AND actor_key=?", (wish_id, actor.key))
        return {"item": _wish_item(c, actor, row)}


def delete_wish(db, actor, wish_id):
    writable(actor)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        # A repeated deletion, or an ID in another household, changes nothing.
        if not c.execute("SELECT 1 FROM household_meal_wishes WHERE id=? AND account_id=?", (wish_id, account_id(db))).fetchone():
            return {"ok": True}
        row = _wish(c, db, wish_id)
        if not actor.is_admin and row["created_by_key"] != actor.key:
            raise HTTPException(403, "Nur die vorschlagende Person oder ein Administrator kann diesen Wunsch löschen")
        c.execute("DELETE FROM household_meal_wishes WHERE id=? AND account_id=?", (wish_id, account_id(db)))
    return {"ok": True}


def plan_wish(db, actor, wish_id, planned_for, planned_servings):
    writable(actor)
    week = week_monday(planned_for)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _wish(c, db, wish_id)
        if week != row["week_start"]:
            raise HTTPException(422, "Bitte einen Tag innerhalb der Wunsch-Woche auswählen")
        if row["planned_for"] is not None:
            if row["planned_for"] != planned_for or row["planned_servings"] != planned_servings:
                raise HTTPException(409, "Dieser Wunsch wurde bereits eingeplant. Änderungen bitte im Wochenplan vornehmen.")
            return {"item": _wish_item(c, actor, row)}
        now = time.time()
        existing = c.execute("SELECT planned_servings FROM meal_plan_entries WHERE account_id=? AND planned_for=? AND recipe_id=?",
                             (account_id(db), planned_for, row["recipe_id"])).fetchone()
        if existing and existing["planned_servings"] != planned_servings:
            raise HTTPException(409, "Dieses Rezept ist an diesem Tag bereits mit anderen Portionen eingeplant. Bitte im Wochenplan ändern.")
        order = c.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM meal_plan_entries WHERE account_id=? AND planned_for=?",
                          (account_id(db), planned_for)).fetchone()[0]
        c.execute("INSERT OR IGNORE INTO meal_plan_entries(account_id,planned_for,recipe_id,planned_servings,sort_order,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (account_id(db), planned_for, row["recipe_id"], planned_servings, order, now, now))
        c.execute("UPDATE household_meal_wishes SET planned_for=?,planned_servings=? WHERE id=? AND account_id=?",
                  (planned_for, planned_servings, wish_id, account_id(db)))
        return {"item": _wish_item(c, actor, _wish(c, db, wish_id))}
