"""Historical shopping quantities and durable, household-scoped delete snapshots."""
from __future__ import annotations

import json
import time

from .units import normalize_unit, to_base


def migrate_schema(c):
    columns = {row[1] for row in c.execute("PRAGMA table_info(shopping_cart)")}
    if "source_contributions" not in columns:
        c.execute("ALTER TABLE shopping_cart ADD COLUMN source_contributions TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS shopping_deleted_items (
        account_id INTEGER NOT NULL,
        operation_id TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        restored_by TEXT,
        PRIMARY KEY(account_id, operation_id))""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS delete_shopping_history AFTER DELETE ON user_accounts BEGIN
        DELETE FROM shopping_deleted_items WHERE account_id=OLD.id;
        DELETE FROM shopping_sync_operations WHERE account_id=OLD.id;
    END""")
    c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)",
              (271, "shopping_contributions_and_durable_undo", time.time()))


def contribution(recipe_id, amount, unit, *, kind=None):
    base_unit, base_amount = to_base(normalize_unit(unit), amount)
    return {"recipe_id": recipe_id, "amount": base_amount, "unit": base_unit,
            "kind": kind or ("recipe" if recipe_id else "manual")}


def contributions(item):
    """Never infer a split from old source IDs, even if only one ID remains."""
    raw = item.get("source_contributions")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, list) and raw:
        return [dict(value) for value in raw if isinstance(value, dict)]
    sources = item.get("source_recipe_ids") or []
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except (ValueError, TypeError):
            sources = []
    return [contribution(source, None, item.get("unit"), kind="legacy") for source in sources] or [
        contribution(None, None, item.get("unit"), kind="legacy")]


def combine_contributions(*groups):
    merged = {}
    for group in groups:
        for entry in group:
            entry = contribution(entry.get("recipe_id"), entry.get("amount"), entry.get("unit"), kind=entry.get("kind"))
            key = (entry["recipe_id"], entry["unit"], entry["kind"])
            previous = merged.get(key)
            if previous is None:
                merged[key] = entry
            elif previous["amount"] is None or entry["amount"] is None:
                previous["amount"] = None
            else:
                previous["amount"] += entry["amount"]
    return list(merged.values())


def display_contributions(db, item, recipe_cache=None):
    from .cart_logic import display_amount
    result = []
    recipe_cache = {} if recipe_cache is None else recipe_cache
    for entry in contributions(item):
        recipe_id = entry.get("recipe_id")
        if recipe_id and recipe_id not in recipe_cache:
            recipe_cache[recipe_id] = db.recipe_get(recipe_id)
        recipe = recipe_cache.get(recipe_id)
        if (recipe and recipe.get("deleted_at") is None
                and recipe.get("owner_account_id") in (None, getattr(db, "account_id", 0))):
            name = recipe.get("name") or "Rezept"
        elif recipe_id:
            recipe_id, name = None, "Nicht mehr verfügbares Rezept"
        elif entry.get("kind") == "legacy":
            name = "Frühere Einkäufe (Aufteilung unbekannt)"
        else:
            name = "Manuell hinzugefügt"
        amount, unit = display_amount(entry.get("amount"), entry.get("unit"))
        result.append({"recipe_id": recipe_id, "recipe_name": name, "amount": amount, "unit": unit})
    return result


def restore_deleted(c, account_id, operation):
    """Restore a server snapshot once, without overwriting another device's work."""
    row = c.execute("SELECT * FROM shopping_deleted_items WHERE account_id=? AND operation_id=?",
                    (account_id, operation["target_operation_id"])).fetchone()
    if not row:
        return {"status": "conflict", "reason": "delete_missing"}
    if row["restored_by"]:
        return {"status": "conflict", "reason": "already_restored"}
    snapshot = json.loads(row["snapshot_json"])
    existing = c.execute("SELECT id FROM shopping_cart WHERE account_id=? AND canonical_name IS ? AND unit IS ?",
                         (account_id, snapshot.get("canonical_name"), snapshot.get("unit"))).fetchone()
    if existing:
        return {"status": "conflict", "reason": "item_changed"}
    fields = ("name", "canonical_name", "amount", "unit", "checked", "added_at", "source_recipe_ids",
              "source_contributions", "category", "sort_order")
    cursor = c.execute("INSERT INTO shopping_cart(account_id," + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in range(len(fields)+1)) + ")",
                       (account_id, *(snapshot.get(field) for field in fields)))
    c.execute("UPDATE shopping_deleted_items SET restored_by=? WHERE account_id=? AND operation_id=?",
              (operation["operation_id"], account_id, operation["target_operation_id"]))
    from .cart_logic import cart_content_revision
    snapshot["id"] = int(cursor.lastrowid)
    return {"status": "applied", "item_id": snapshot["id"], "revision": cart_content_revision(snapshot)}
