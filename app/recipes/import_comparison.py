"""Local, household-scoped import comparison; merging never mutates a source."""
from __future__ import annotations

import hashlib
import json
import re
import time
from difflib import SequenceMatcher

from .canonical import canonical_name


def _name(value):
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def _keys(rows):
    return {key for row in rows if (key := canonical_name(row.get("name") or ""))}


def snapshot(draft, entry, recipe, ingredients, steps):
    suggestion = {key: value for key, value in (entry.get("ai_suggestion") or {}).items()
                  if key not in {"comparison_pending", "comparison_result"}}
    # Bulk and single-recipe queries expose different database metadata. Hash
    # only the ordered content that the comparison can actually copy.
    ingredient_content = [{key: row.get(key) for key in
                           ("name", "canonical_name", "amount", "unit", "raw", "sort_order")}
                          for row in ingredients]
    step_content = [{key: row.get(key) for key in
                    ("step_number", "instruction", "timer_seconds")} for row in steps]
    payload = [draft, suggestion, entry.get("description"),
               {key: recipe.get(key) for key in ("id", "name", "description", "servings", "url")},
               ingredient_content, step_content]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def candidates(db, entry, draft):
    # Visibility belongs in SQL, before names or ingredient signatures are read.
    visibility = db.recipe_visibility_sql("r") if hasattr(db, "recipe_visibility_sql") else "1=1"
    with db.conn() as connection:
        rows = connection.execute(
            "SELECT r.id,r.name FROM recipes r WHERE r.deleted_at IS NULL "
            "AND COALESCE(r.ingredients_status,'')<>'variant_pending' AND " + visibility +
            " ORDER BY r.id DESC LIMIT 2001"
        ).fetchall()
    truncated = len(rows) > 2000
    rows = [dict(row) for row in rows[:2000]]
    # The shared bulk helper intentionally accepts at most 500 IDs per call.
    ingredient_map = {}
    for offset in range(0, len(rows), 500):
        ingredient_map.update(db.recipe_ingredients_for_ids(
            [int(row["id"]) for row in rows[offset:offset + 500]]))
    incoming_keys = _keys(draft["ingredients"])
    incoming_name = _name(draft["name"])
    ranked = []
    for row in rows:
        ingredients = ingredient_map.get(row["id"], [])
        keys = _keys(ingredients)
        overlap = incoming_keys & keys
        union = incoming_keys | keys
        existing_name = _name(row["name"])
        name_score = SequenceMatcher(None, incoming_name, existing_name).ratio()
        # Named variants such as "Haferbrei mit Apfel" retain the base dish name.
        shortest, longest = sorted((incoming_name, existing_name), key=len)
        if len(shortest) >= 6 and longest.startswith(shortest + " "):
            name_score = max(name_score, .80)
        ingredient_score = len(overlap) / len(union) if union else 0
        # Salt/oil alone is not evidence of a duplicate. Scores are hints, not certainty.
        if not (name_score >= .68 or (len(overlap) >= 3 and ingredient_score >= .65)):
            continue
        ranked.append((round(max(name_score, ingredient_score) * 100), row, ingredients, keys, overlap))
    ranked.sort(key=lambda item: (-item[0], item[1]["name"].casefold(), item[1]["id"]))
    items = []
    for score, row, ingredients, keys, overlap in ranked[:8]:
        steps = db.recipe_steps_get(row["id"])
        public = db.recipe_get(row["id"])
        if not public or public.get("deleted_at") is not None:
            continue
        items.append({
            "id": row["id"], "name": row["name"], "url": public.get("url"),
            "servings": public.get("servings"), "ingredients": ingredients, "steps": steps,
            "score": score,
            "shared_ingredients": sorted(overlap),
            "import_only_ingredients": sorted(incoming_keys - keys),
            "existing_only_ingredients": sorted(keys - incoming_keys),
            "snapshot": snapshot(draft, entry, public, ingredients, steps),
        })
    items.sort(key=lambda item: (-item["score"], item["name"].casefold(), item["id"]))
    return {"items": items[:8], "truncated": truncated}


def finish_pending(db, entry, request_id, recipe_id, request_hash):
    suggestion = dict(entry.get("ai_suggestion") or {})
    suggestion.pop("comparison_pending", None)
    suggestion["comparison_result"] = {"request_id": request_id, "recipe_id": recipe_id, "request_hash": request_hash}
    stored_url = entry.get("_stored_url") or entry["url"]
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT status,content_type,owner_account_id,source_url FROM pending WHERE url=?",
                                     (stored_url,)).fetchone()
        if not current or current["status"] != "pending":
            raise RuntimeError("Dieser Import wurde zwischenzeitlich abgeschlossen. Bitte neu laden.")
        target = connection.execute(
            "SELECT name,folder_path,owner_account_id FROM recipes WHERE id=? AND deleted_at IS NULL "
            "AND COALESCE(ingredients_status,'')<>'variant_pending'", (recipe_id,),
        ).fetchone()
        if not target:
            raise RuntimeError("Das zugeordnete Rezept ist nicht mehr verfügbar.")
        # Match the pending import's existing namespace. Persisting the receipt
        # and history together prevents a resolved URL being enqueued again.
        # A global import must not publish a newly created private variant alias.
        visible_target = target["owner_account_id"] is None or target["owner_account_id"] == current["owner_account_id"]
        connection.execute(
            "INSERT OR REPLACE INTO history "
            "(url,processed_at,content_type,name,target_dir,owner_account_id,source_url) VALUES(?,?,?,?,?,?,?)",
            (stored_url, time.time(), current["content_type"], target["name"] if visible_target else "(zugeordnet)",
             target["folder_path"] if visible_target else "", current["owner_account_id"],
             current["source_url"] or entry["url"]),
        )
        if getattr(db, "account_id", 0) > 0:
            connection.execute(
                "INSERT OR IGNORE INTO account_recipe_state(account_id,recipe_id,saved_at) VALUES(?,?,?)",
                (db.account_id, recipe_id, time.time()),
            )
        connection.execute("UPDATE pending SET status='resolved',ai_suggestion=? WHERE url=?",
                           (json.dumps(suggestion, ensure_ascii=False), stored_url))
