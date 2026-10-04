"""Household-bound database facade. System workers keep the original Database.

IDs remain stable across households. Each mutation checks the household in SQL;
recipe contents are shared globally or belong to exactly one household.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta

from .db import Database, RECIPE_VARIANT_PENDING_STATUS
from .tenancy import HouseholdScope, RECIPE_LIBRARY, canonical_source, private_source_key

_DEFAULT_IMPORT = object()


class HouseholdDatabase(Database):
    def __init__(self, base: Database, scope: HouseholdScope, *, import_owner=_DEFAULT_IMPORT):
        self.__dict__.update(base.__dict__)
        self.scope = scope
        self.import_owner = scope.account_id if import_owner is _DEFAULT_IMPORT and scope.account_id > 0 else (
            None if import_owner is _DEFAULT_IMPORT else import_owner
        )

    @property
    def account_id(self):
        return self.scope.account_id

    def _share_owner_filter(self):
        if self.account_id == 0 and self.scope.is_admin:
            return "1=1", []
        return "owner_account_id=?", [self.account_id]

    def recipe_share_link_create(self, share_id, recipe_id, **values):
        if not self.recipe_get(recipe_id):
            raise LookupError("Rezept nicht gefunden")
        values["owner_account_id"] = self.account_id if self.account_id > 0 else None
        return super().recipe_share_link_create(share_id, recipe_id, **values)

    def recipe_share_links_list(self, recipe_id, limit=100):
        if not self.recipe_get(recipe_id):
            return []
        owners, params = self._share_owner_filter()
        with self.conn() as c:
            rows = c.execute(
                "SELECT *, CASE WHEN revoked_at IS NULL AND expires_at>? THEN 1 ELSE 0 END AS active "
                f"FROM recipe_share_links WHERE recipe_id=? AND {owners} "
                "ORDER BY created_at DESC LIMIT ?",
                [time.time(), int(recipe_id), *params, max(1, min(500, int(limit)))],
            ).fetchall()
        return [dict(row) for row in rows]

    def recipe_share_link_revoke(self, recipe_id, share_id):
        if not self.recipe_get(recipe_id):
            return False
        owners, params = self._share_owner_filter()
        with self.conn() as c:
            result = c.execute(
                "UPDATE recipe_share_links SET revoked_at=COALESCE(revoked_at, ?) "
                f"WHERE id=? AND recipe_id=? AND {owners}",
                [time.time(), share_id, int(recipe_id), *params],
            )
        return result.rowcount > 0

    def recipe_visibility_sql(self, alias="r"):
        visible = f"({alias}.owner_account_id IS NULL OR {alias}.owner_account_id={int(self.account_id)})"
        library = RECIPE_LIBRARY.get()
        if library == "global":
            return f"{alias}.owner_account_id IS NULL"
        if library == "mine":
            return (f"({visible} AND ({alias}.owner_account_id={int(self.account_id)} OR EXISTS "
                    f"(SELECT 1 FROM account_recipe_state ars WHERE ars.recipe_id={alias}.id "
                    f"AND ars.account_id={int(self.account_id)})))")
        return visible

    def recipe_personal_sql(self, field, alias="r"):
        if field not in {"is_favorite", "rating"}:
            raise ValueError("Unknown personal recipe field")
        if self.account_id == 0:
            return f"{alias}.{field}"
        return (f"COALESCE((SELECT ars.{field} FROM account_recipe_state ars "
                f"WHERE ars.recipe_id={alias}.id AND ars.account_id={int(self.account_id)}),0)")

    def _present_recipe(self, row, state=_DEFAULT_IMPORT):
        if not row:
            return None
        result = dict(row)
        if result.get("source_url"):
            result["url"] = result["source_url"] if result.get("deleted_at") is None else None
        result["visibility"] = "global" if result.get("owner_account_id") is None else "private"
        result["can_edit"] = not self.scope.is_guest and (
            self.scope.is_admin or result.get("owner_account_id") == self.account_id
        )
        if state is _DEFAULT_IMPORT:
            with self.conn() as c:
                state = c.execute("SELECT is_favorite,rating FROM account_recipe_state WHERE account_id=? AND recipe_id=?",
                                  (self.account_id, result["id"])).fetchone()
        if self.account_id != 0:
            result["is_favorite"] = bool(state and state["is_favorite"])
            result["rating"] = int(state["rating"]) if state else 0
        result["in_library"] = bool(state) or result.get("owner_account_id") == self.account_id
        return result

    def _present_recipes(self, rows):
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        with self.conn() as c:
            states = {int(r["recipe_id"]): r for r in c.execute(
                "SELECT * FROM account_recipe_state WHERE account_id=? AND recipe_id IN (" + ",".join("?" for _ in ids) + ")",
                (self.account_id, *ids))}
        return [self._present_recipe(row, states.get(int(row["id"]))) for row in rows]

    def recipe_get(self, recipe_id, *, include_pending=False):
        with self.conn() as c:
            sql = "SELECT * FROM recipes WHERE id=? AND (owner_account_id IS NULL OR owner_account_id=?)"
            params = [recipe_id, self.account_id]
            if not include_pending:
                sql += " AND COALESCE(ingredients_status,'')<>?"
                params.append(RECIPE_VARIANT_PENDING_STATUS)
            row = c.execute(sql, params).fetchone()
        return self._present_recipe(row)

    def recipe_get_by_folder(self, folder_path, *, include_pending=False):
        with self.conn() as c:
            sql = "SELECT * FROM recipes WHERE folder_path=? AND (owner_account_id IS NULL OR owner_account_id=?)"
            params = [folder_path, self.account_id]
            if not include_pending:
                sql += " AND COALESCE(ingredients_status,'')<>?"
                params.append(RECIPE_VARIANT_PENDING_STATUS)
            row = c.execute(sql, params).fetchone()
        return self._present_recipe(row)

    def global_recipe_for_url(self, url):
        normalized = canonical_source(url)
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipes WHERE owner_account_id IS NULL AND deleted_at IS NULL "
                            "AND (source_url=? OR url=?) ORDER BY id LIMIT 1", (normalized, normalized)).fetchone()
            if not row:
                row = c.execute("SELECT r.* FROM history h JOIN recipes r ON r.folder_path=h.target_dir "
                                "WHERE h.owner_account_id IS NULL AND r.owner_account_id IS NULL AND r.deleted_at IS NULL "
                                "AND (h.source_url=? OR h.url=?) ORDER BY r.id LIMIT 1", (normalized, normalized)).fetchone()
        return self._present_recipe(row)

    def recipe_get_by_url(self, url):
        # Private imports reuse the global object first. No other household's
        # private source or existence is considered or revealed.
        global_recipe = self.global_recipe_for_url(url)
        if global_recipe:
            return global_recipe
        if self.import_owner is None:
            return None
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipes WHERE owner_account_id=? AND deleted_at IS NULL AND (source_url=? OR url=?)",
                            (self.import_owner, canonical_source(url), self._import_key(url))).fetchone()
        return self._present_recipe(row)

    def _import_key(self, url):
        return private_source_key(self.import_owner, canonical_source(url)) if self.import_owner is not None else url

    def recipe_upsert(self, **values):
        source_url = canonical_source(values.get("url"))
        owner = values.pop("owner_account_id", self.import_owner)
        if owner is not None:
            existing = self.global_recipe_for_url(source_url)
            if existing:
                self.save_recipe(int(existing["id"]))
                return int(existing["id"])
            values["url"] = private_source_key(owner, source_url)
        values["owner_account_id"] = owner
        values["source_url"] = source_url
        recipe_id = super().recipe_upsert(**values)
        if self.account_id > 0:
            self.save_recipe(recipe_id)
        return recipe_id

    def save_recipe(self, recipe_id, *, field=None, value=None):
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            recipe = c.execute("SELECT id FROM recipes WHERE id=? AND deleted_at IS NULL "
                               "AND (owner_account_id IS NULL OR owner_account_id=?)", (recipe_id, self.account_id)).fetchone()
            if not recipe or self.account_id <= 0:
                raise LookupError("Rezept nicht gefunden")
            c.execute("INSERT OR IGNORE INTO account_recipe_state(account_id,recipe_id,saved_at) VALUES(?,?,?)",
                      (self.account_id, recipe_id, time.time()))
            if field in {"is_favorite", "rating"}:
                if value is None and field == "is_favorite":
                    c.execute("UPDATE account_recipe_state SET is_favorite=1-is_favorite WHERE account_id=? AND recipe_id=?",
                              (self.account_id, recipe_id))
                else:
                    c.execute(f"UPDATE account_recipe_state SET {field}=? WHERE account_id=? AND recipe_id=?",
                              (value, self.account_id, recipe_id))
            state = c.execute("SELECT * FROM account_recipe_state WHERE account_id=? AND recipe_id=?",
                              (self.account_id, recipe_id)).fetchone()
        return dict(state)

    def remove_saved_recipe(self, recipe_id):
        with self.conn() as c:
            return c.execute("DELETE FROM account_recipe_state WHERE account_id=? AND recipe_id=?",
                             (self.account_id, recipe_id)).rowcount > 0

    def cart_list(self):
        with self.conn() as c:
            rows = c.execute("SELECT sc.*, COALESCE(sc.category, sp.category, 'Sonstiges') AS resolved_category, sp.icon "
                             "FROM shopping_cart sc LEFT JOIN shopping_products sp ON sp.canonical_name=sc.canonical_name "
                             "AND sp.account_id=sc.account_id WHERE sc.account_id=? "
                             "ORDER BY sc.checked, sc.sort_order IS NULL, sc.sort_order, sc.added_at DESC",
                             (self.account_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["category"] = item.pop("resolved_category")
            result.append(item)
        return result

    def cart_find_mergeable(self, canonical_name, unit):
        with self.conn() as c:
            row = c.execute("SELECT * FROM shopping_cart WHERE account_id=? AND canonical_name=? AND unit IS ?",
                            (self.account_id, canonical_name, unit)).fetchone()
        return dict(row) if row else None

    def _product_upsert(self, c, canonical, name, category, unit, *, increment_usage=True):
        from .recipes.shopping_catalog import product_defaults
        if not canonical:
            return
        defaults = product_defaults(name, canonical, category)
        now = time.time()
        c.execute("INSERT INTO shopping_products(account_id,canonical_name,display_name,category,icon,default_unit,usage_count,last_used_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,canonical_name) DO UPDATE SET "
                  "display_name=excluded.display_name,category=excluded.category,icon=excluded.icon,"
                  "default_unit=COALESCE(excluded.default_unit,shopping_products.default_unit),"
                  "usage_count=shopping_products.usage_count+excluded.usage_count,"
                  "last_used_at=COALESCE(excluded.last_used_at,shopping_products.last_used_at),updated_at=excluded.updated_at",
                  (self.account_id, canonical, name, defaults["category"], defaults["icon"], unit,
                   int(increment_usage), now if increment_usage else None, now))

    def _shopping_product_upsert_conn(self, c, *, canonical_name, display_name, category=None, default_unit=None, increment_usage=True):
        self._product_upsert(c, canonical_name, display_name, category, default_unit, increment_usage=increment_usage)

    def _cart_merge(self, c, item, *, reopen=False):
        canonical, unit = item.get("canonical_name"), item.get("unit")
        existing = c.execute("SELECT * FROM shopping_cart WHERE account_id=? AND canonical_name=? AND unit IS ?",
                             (self.account_id, canonical, unit)).fetchone() if canonical else None
        sources = list(item.get("source_recipe_ids") or [])
        if existing:
            amount = (existing["amount"] or 0) + (item.get("amount") or 0) if item.get("amount") is not None else existing["amount"]
            sources = list(dict.fromkeys(json.loads(existing["source_recipe_ids"] or "[]") + sources))
            c.execute("UPDATE shopping_cart SET amount=?, source_recipe_ids=?, checked=?, category=COALESCE(category,?) "
                      "WHERE id=? AND account_id=?", (amount, json.dumps(sources), 0 if reopen else existing["checked"],
                                                     item.get("category"), existing["id"], self.account_id))
            item_id = int(existing["id"])
        else:
            cur = c.execute("INSERT INTO shopping_cart(account_id,name,canonical_name,amount,unit,checked,added_at,source_recipe_ids,category,sort_order) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)", (self.account_id, item.get("name") or "?", canonical, item.get("amount"), unit,
                                                            int(bool(item.get("checked"))), time.time(), json.dumps(sources),
                                                            item.get("category"), item.get("sort_order")))
            item_id = int(cur.lastrowid)
        self._product_upsert(c, canonical, item.get("name") or "?", item.get("category"), unit)
        return item_id, bool(existing)

    def cart_add_or_merge(self, *, name, canonical_name, amount, unit, source_recipe_id, category=None):
        if source_recipe_id and not self.recipe_get(source_recipe_id):
            raise LookupError("Rezept nicht gefunden")
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._cart_merge(c, {"name": name, "canonical_name": canonical_name, "amount": amount, "unit": unit,
                                       "source_recipe_ids": [source_recipe_id] if source_recipe_id else [], "category": category}, reopen=True)[0]

    def cart_merge_many(self, items):
        merged = 0
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for item in items:
                merged += self._cart_merge(c, item, reopen=True)[1]
        return {"added": len(items)-merged, "merged": merged}

    def cart_update(self, item_id, *, amount=None, checked=None, name=None):
        values = {k: v for k, v in {"amount": amount, "checked": int(checked) if checked is not None else None, "name": name}.items() if v is not None}
        if not values:
            return False
        with self.conn() as c:
            return c.execute("UPDATE shopping_cart SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=? AND account_id=?",
                             (*values.values(), item_id, self.account_id)).rowcount > 0

    def cart_delete(self, item_id):
        with self.conn() as c:
            return c.execute("DELETE FROM shopping_cart WHERE id=? AND account_id=?", (item_id, self.account_id)).rowcount > 0

    def cart_clear(self, *, only_checked=False):
        with self.conn() as c:
            return c.execute("DELETE FROM shopping_cart WHERE account_id=?" + (" AND checked=1" if only_checked else ""),
                             (self.account_id,)).rowcount

    def _cart_replace(self, c, items):
        from .recipes.shopping_optimizer import _source_ids
        c.execute("DELETE FROM shopping_cart WHERE account_id=?", (self.account_id,))
        for item in items:
            c.execute("INSERT INTO shopping_cart(account_id,name,canonical_name,amount,unit,checked,added_at,source_recipe_ids,category,sort_order) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (self.account_id, item.get("name") or "?", item.get("canonical_name"), item.get("amount"),
                                                      item.get("unit"), int(bool(item.get("checked"))), item.get("added_at") or time.time(),
                                                      json.dumps(_source_ids(item.get("source_recipe_ids"))), item.get("category"), item.get("sort_order")))
        return len(items)

    def cart_replace(self, items):
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._cart_replace(c, items)

    def cart_replace_if_unchanged(self, items, expected_fingerprint):
        from .recipes.shopping_optimizer import cart_fingerprint
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            current = [dict(r) for r in c.execute("SELECT sc.*, COALESCE(sc.category,sp.category,'Sonstiges') AS resolved_category "
                                                "FROM shopping_cart sc LEFT JOIN shopping_products sp ON sp.account_id=sc.account_id "
                                                "AND sp.canonical_name=sc.canonical_name WHERE sc.account_id=?", (self.account_id,))]
            for item in current:
                item["category"] = item.pop("resolved_category")
            if cart_fingerprint(current) != expected_fingerprint:
                return None
            return self._cart_replace(c, items)

    def shopping_product_suggestions(self, query="", limit=8):
        from .recipes.shopping_catalog import rank_product_suggestions
        with self.conn() as c:
            rows = c.execute("SELECT canonical_name,display_name AS name,category,icon,default_unit,usage_count,recipe_count,last_used_at "
                             "FROM shopping_products WHERE account_id=?", (self.account_id,)).fetchall()
        return rank_product_suggestions([dict(row) for row in rows], query, limit)

    def shopping_catalog_rebuild(self):
        from .db import _rebuild_shopping_catalog_from_recipes
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return _rebuild_shopping_catalog_from_recipes(c, account_id=self.account_id)

    def ingredient_name_hints(self, limit=500):
        with self.conn() as c:
            rows = c.execute(
                "SELECT MIN(TRIM(i.name)) AS display_name,COUNT(DISTINCT i.recipe_id) AS uses "
                "FROM recipe_ingredients i JOIN recipes r ON r.id=i.recipe_id "
                "WHERE r.deleted_at IS NULL AND TRIM(COALESCE(i.name,''))<>'' AND " + self.recipe_visibility_sql() +
                " AND COALESCE(r.ingredients_status,'')<>? GROUP BY LOWER(TRIM(i.name)) "
                "ORDER BY uses DESC,LENGTH(display_name),display_name COLLATE NOCASE LIMIT ?",
                (RECIPE_VARIANT_PENDING_STATUS, max(1, min(int(limit or 500), 1000))),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def shopping_excluded_canonicals(self):
        with self.conn() as c:
            return {r[0] for r in c.execute("SELECT canonical_name FROM shopping_exclusions WHERE account_id=?", (self.account_id,))}

    def shopping_exclusion_set(self, canonical, excluded=True):
        with self.conn() as c:
            if excluded:
                c.execute("INSERT OR IGNORE INTO shopping_exclusions(account_id,canonical_name,created_at) VALUES(?,?,?)",
                          (self.account_id, canonical, time.time()))
            else:
                c.execute("DELETE FROM shopping_exclusions WHERE account_id=? AND canonical_name=?", (self.account_id, canonical))

    def recurring_list(self):
        from .recipes.cart_logic import display_amount
        from .recipes.shopping_catalog import category_icon
        with self.conn() as c:
            rows = c.execute("SELECT * FROM shopping_recurring WHERE account_id=? ORDER BY active DESC,next_due_on,name COLLATE NOCASE",
                             (self.account_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["amount_base"], item["unit_base"] = item["amount"], item["unit"]
            item["amount"], item["unit"] = display_amount(item["amount"], item["unit"])
            item["due_in_days"] = (date.fromisoformat(item["next_due_on"]) - date.today()).days
            item["is_due"] = bool(item["active"] and item["due_in_days"] <= 0)
            item["icon"] = category_icon(item.get("category") or "Sonstiges")
            result.append(item)
        return result

    def recurring_create(self, **values):
        fields = ["name", "canonical_name", "amount", "unit", "category", "interval_days", "next_due_on", "active"]
        now = time.time()
        with self.conn() as c:
            cur = c.execute("INSERT INTO shopping_recurring(account_id," + ",".join(fields) + ",created_at,updated_at) "
                            "VALUES(" + ",".join("?" for _ in range(len(fields)+3)) + ")",
                            (self.account_id, *(values.get(k, 1 if k == "active" else None) for k in fields), now, now))
            item_id = int(cur.lastrowid)
        return self.recurring_get(item_id)

    def recurring_get(self, item_id):
        with self.conn() as c:
            row = c.execute("SELECT * FROM shopping_recurring WHERE id=? AND account_id=?", (item_id, self.account_id)).fetchone()
        return dict(row) if row else None

    def recurring_update(self, item_id, values):
        allowed = {"name", "canonical_name", "amount", "unit", "category", "interval_days", "next_due_on", "active"}
        updates = {k: v for k, v in values.items() if k in allowed}
        updates["updated_at"] = time.time()
        with self.conn() as c:
            return c.execute("UPDATE shopping_recurring SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=? AND account_id=?",
                             (*updates.values(), item_id, self.account_id)).rowcount > 0

    def recurring_delete(self, item_id):
        with self.conn() as c:
            return c.execute("DELETE FROM shopping_recurring WHERE id=? AND account_id=?", (item_id, self.account_id)).rowcount > 0

    def recurring_run_due(self, *, due_on=None):
        if self.scope.is_guest:
            return []
        run_day = due_on or date.today()
        result = []
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM shopping_recurring WHERE account_id=? AND active=1 AND next_due_on<=? LIMIT 1",
                             (self.account_id, run_day.isoformat())).fetchone():
                return []
        from .tenancy import household_write_guard
        with household_write_guard(self, self.scope), self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT * FROM shopping_recurring WHERE account_id=? AND active=1 AND next_due_on<=? ORDER BY id",
                             (self.account_id, run_day.isoformat())).fetchall()
            for row in rows:
                item_id, _ = self._cart_merge(c, dict(row), reopen=True)
                next_due = date.fromisoformat(row["next_due_on"])
                while next_due <= run_day:
                    next_due += timedelta(days=int(row["interval_days"]))
                c.execute("UPDATE shopping_recurring SET next_due_on=?,last_added_at=?,updated_at=? WHERE id=? AND account_id=?",
                          (next_due.isoformat(), time.time(), time.time(), row["id"], self.account_id))
                result.append({"id": int(row["id"]), "cart_id": item_id, "name": row["name"]})
        return result

    def meal_plan_entries(self, start_date, end_date):
        with self.conn() as c:
            rows = c.execute("SELECT mp.*,r.name AS recipe_name,r.servings AS recipe_servings,r.thumb_filename,r.ingredients_status, "
                             "(SELECT COUNT(*) FROM recipe_ingredients ri WHERE ri.recipe_id=r.id) AS ingredients_count "
                             "FROM meal_plan_entries mp JOIN recipes r ON r.id=mp.recipe_id "
                             "WHERE mp.account_id=? AND mp.planned_for BETWEEN ? AND ? AND r.deleted_at IS NULL "
                             "AND (r.owner_account_id IS NULL OR r.owner_account_id=?) ORDER BY mp.planned_for,mp.sort_order,mp.id",
                             (self.account_id, start_date, end_date, self.account_id)).fetchall()
        return [dict(r) for r in rows]

    def meal_plan_add(self, *, planned_for, recipe_id, planned_servings):
        if not self.recipe_get(recipe_id) or self.recipe_get(recipe_id).get("deleted_at"):
            raise ValueError("Rezept nicht gefunden")
        now = time.time()
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            order = c.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM meal_plan_entries WHERE account_id=? AND planned_for=?",
                              (self.account_id, planned_for)).fetchone()[0]
            c.execute("INSERT INTO meal_plan_entries(account_id,planned_for,recipe_id,planned_servings,sort_order,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,planned_for,recipe_id) DO UPDATE SET "
                      "planned_servings=excluded.planned_servings,updated_at=excluded.updated_at",
                      (self.account_id, planned_for, recipe_id, planned_servings, order, now, now))
            row = c.execute("SELECT * FROM meal_plan_entries WHERE account_id=? AND planned_for=? AND recipe_id=?",
                            (self.account_id, planned_for, recipe_id)).fetchone()
        return dict(row)

    def meal_plan_update(self, item_id, *, planned_for=None, planned_servings=None):
        updates = {k: v for k, v in {"planned_for": planned_for, "planned_servings": planned_servings}.items() if v is not None}
        updates["updated_at"] = time.time()
        with self.conn() as c:
            try:
                return c.execute("UPDATE meal_plan_entries SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=? AND account_id=?",
                                 (*updates.values(), item_id, self.account_id)).rowcount > 0
            except sqlite3.IntegrityError as exc:
                raise ValueError("Rezept ist für diesen Tag bereits eingeplant") from exc

    def meal_plan_delete(self, item_id):
        with self.conn() as c:
            return c.execute("DELETE FROM meal_plan_entries WHERE id=? AND account_id=?", (item_id, self.account_id)).rowcount > 0

    def recipe_cooking_complete(self, recipe_id, username, **values):
        # The legacy completion transaction already protects retry keys. Add
        # the household to the inserted history row in that same transaction.
        if not self.recipe_get(recipe_id):
            raise ValueError("Rezept nicht gefunden")
        return super().recipe_cooking_complete(recipe_id, username, account_id=self.account_id, **values)

    def recipe_cook_history(self, recipe_id, limit=20):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM recipe_cook_history WHERE recipe_id=? AND account_id=? "
                                             "ORDER BY cooked_at DESC,id DESC LIMIT ?", (recipe_id, self.account_id, max(1, min(100, limit))))]

    def recipe_cook_summary(self, recipe_id):
        with self.conn() as c:
            row = c.execute("SELECT COUNT(*) AS count,MAX(cooked_at) AS last_cooked_at FROM recipe_cook_history WHERE recipe_id=? AND account_id=?",
                            (recipe_id, self.account_id)).fetchone()
            latest = c.execute("SELECT cooked_by,servings FROM recipe_cook_history WHERE recipe_id=? AND account_id=? ORDER BY cooked_at DESC,id DESC LIMIT 1",
                               (recipe_id, self.account_id)).fetchone()
        return {"count": int(row["count"]), "last_cooked_at": row["last_cooked_at"],
                "last_cooked_by": latest["cooked_by"] if latest else None, "last_servings": latest["servings"] if latest else None}

    def ingredients_known(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT ri.canonical_name,MIN(ri.name) AS display_name,COUNT(DISTINCT ri.recipe_id) AS n FROM recipe_ingredients ri "
                                             "JOIN recipes r ON r.id=ri.recipe_id WHERE r.deleted_at IS NULL AND " + self.recipe_visibility_sql() +
                                             " AND ri.canonical_name IS NOT NULL AND ri.canonical_name!='' " +
                                             " GROUP BY ri.canonical_name ORDER BY n DESC,ri.canonical_name")]

    def recipe_ingredients_for_ids(self, recipe_ids):
        ids = [rid for rid in recipe_ids if self.recipe_get(rid)]
        return super().recipe_ingredients_for_ids(ids)

    def recipes_for_image_backfill(self, *, ids_only=False):
        with self.conn() as c:
            return [dict(row) for row in c.execute("SELECT " + ("id" if ids_only else "*") +
                                                  " FROM recipes r WHERE deleted_at IS NULL AND " + self.recipe_visibility_sql() + " ORDER BY id")]

    def tag_list(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT t.id,t.name,COUNT(DISTINCT r.id) AS n FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id "
                                             "JOIN recipes r ON r.id=rt.recipe_id WHERE r.deleted_at IS NULL AND " + self.recipe_visibility_sql() +
                                             " GROUP BY t.id ORDER BY n DESC,t.name COLLATE NOCASE")]

    def search_vocabulary(self, limit=3000):
        visible = self.recipe_visibility_sql()
        with self.conn() as c:
            values = [r[0] for r in c.execute("SELECT DISTINCT i.canonical_name FROM recipe_ingredients i "
                                             "JOIN recipes r ON r.id=i.recipe_id WHERE r.deleted_at IS NULL AND " + visible +
                                             " AND i.canonical_name IS NOT NULL AND i.canonical_name!='' LIMIT ?", (limit,))]
            values.extend(r[0] for r in c.execute("SELECT DISTINCT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id "
                                                 "JOIN recipes r ON r.id=rt.recipe_id WHERE r.deleted_at IS NULL AND " + visible + " LIMIT ?", (limit,)))
            for row in c.execute("SELECT name FROM recipes r WHERE deleted_at IS NULL AND " + visible + " LIMIT ?", (limit,)):
                values.extend(str(row[0] or "").replace("-", " ").split())
        return sorted({str(v).strip() for v in values if len(str(v).strip()) >= 3}, key=str.casefold)

    def recipe_count_trash(self):
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM recipes r WHERE deleted_at IS NOT NULL AND " + self.recipe_visibility_sql()).fetchone()[0])

    def recipe_versions_list(self, recipe_id=None, **values):
        if recipe_id is not None and not self.recipe_get(recipe_id):
            return []
        items = super().recipe_versions_list(recipe_id, **values)
        return [item for item in items if self.recipe_get(item["recipe_id"])]

    def recipe_version_get(self, version_id):
        item = super().recipe_version_get(version_id)
        return item if item and self.recipe_get(item["recipe_id"]) else None

    def recipe_image_backup_get(self, backup_id):
        item = super().recipe_image_backup_get(backup_id)
        return item if item and self.recipe_get(item["recipe_id"]) else None

    def recipe_image_backup_list(self, recipe_id=None, limit=200):
        return [item for item in super().recipe_image_backup_list(recipe_id, limit) if self.recipe_get(item["recipe_id"])]

    def recipe_clone_content(self, source_id, target_id, **options):
        if not self.recipe_get(source_id, include_pending=True) or not self.recipe_get(target_id, include_pending=True):
            raise LookupError("Rezept nicht gefunden")
        return super().recipe_clone_content(source_id, target_id, **options)

    def audit_ai_findings_list(self, finding_type=None, only_open=True):
        sql = ("SELECT f.*,r.name AS recipe_name,r.folder_path FROM audit_ai_findings f JOIN recipes r ON r.id=f.recipe_id "
               "WHERE r.deleted_at IS NULL AND " + self.recipe_visibility_sql())
        params = []
        if finding_type:
            sql += " AND f.finding_type=?"
            params.append(finding_type)
        if only_open:
            sql += " AND f.resolved=0"
        with self.conn() as c:
            return [dict(row) for row in c.execute(sql + " ORDER BY f.created_at DESC", params)]

    def audit_ai_finding_resolve(self, finding_id):
        with self.conn() as c:
            row = c.execute("SELECT recipe_id FROM audit_ai_findings WHERE id=?", (finding_id,)).fetchone()
        if not row or not self.recipe_get(row[0]):
            raise LookupError("Finding nicht gefunden")
        return super().audit_ai_finding_resolve(finding_id)

    def audit_ai_findings_count(self, only_open=True):
        counts = {}
        for item in self.audit_ai_findings_list(only_open=only_open):
            counts[item["finding_type"]] = counts.get(item["finding_type"], 0) + 1
        return counts

    def _import_row(self, row):
        if row:
            row["url"] = row.get("source_url") or row["url"]
            row["visibility"] = "private" if row.get("owner_account_id") is not None else "global"
        return row

    def history_has(self, url):
        return self.history_get(url) is not None

    def history_get(self, url):
        return self._import_row(super().history_get(self._import_key(url)))

    def history_add(self, url, **values):
        return super().history_add(self._import_key(url), owner_account_id=self.import_owner,
                                   source_url=canonical_source(url), **values)

    def history_list(self, limit=200):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM history WHERE owner_account_id=? OR (owner_account_id IS NULL AND ?) ORDER BY processed_at DESC LIMIT ?",
                             (self.account_id, self.scope.is_admin, limit)).fetchall()
        return [self._import_row(dict(r)) for r in rows]

    def history_update(self, url, **values):
        return super().history_update(self._import_key(url), **values)

    def history_delete(self, url):
        return super().history_delete(self._import_key(url))

    def pending_get(self, url, *, visibility=None):
        if visibility is None and self.import_owner is None:
            visibility = "global"
        visible = "(owner_account_id=? OR (owner_account_id IS NULL AND ?))"
        if visibility == "private":
            visible += " AND owner_account_id IS NOT NULL"
        elif visibility == "global":
            visible += " AND owner_account_id IS NULL"
        with self.conn() as c:
            row = c.execute("SELECT url FROM pending WHERE (source_url=? OR url=?) AND " + visible +
                            " ORDER BY owner_account_id IS NULL LIMIT 1",
                            (canonical_source(url), self._import_key(url), self.account_id,
                             self.scope.is_admin or self.account_id == 0)).fetchone()
        item = super().pending_get(row["url"]) if row else None
        if item:
            item["_stored_url"] = item["url"]
        return self._import_row(item)

    def pending_add(self, url, **values):
        return super().pending_add(url=self._import_key(url), owner_account_id=self.import_owner,
                                   source_url=canonical_source(url), **values)

    def pending_list(self, status="pending", **values):
        items = super().pending_list(status, **values)
        return [self._import_row(item) for item in items if item.get("owner_account_id") == self.account_id
                or (self.scope.is_admin and item.get("owner_account_id") is None)]

    def pending_resolve(self, url, status="resolved"):
        item = self.pending_get(url)
        if item:
            return super().pending_resolve(item["_stored_url"], status)

    def pending_update_suggestion(self, url, suggestion):
        item = self.pending_get(url)
        if item:
            return super().pending_update_suggestion(item["_stored_url"], suggestion)

    def pending_delete(self, url):
        return super().pending_delete(self._import_key(url))

    def download_failure_clear(self, url):
        return super().download_failure_clear(self._import_key(url))

    def download_failure_record(self, url, error, content_type="recipe"):
        return super().download_failure_record(self._import_key(url), error, content_type)

    def download_failures_list(self, limit=100):
        return [item for item in super().download_failures_list(limit) if not item["url"].startswith("private-recipe://")]

    def download_failure_reset(self, url):
        if url.startswith("private-recipe://"):
            return False
        return super().download_failure_reset(url)

    def pending_count(self):
        return len(self.pending_list(status="pending"))

    def recipes_extraction_stats(self):
        with self.conn() as c:
            return {r[0]: int(r[1]) for r in c.execute("SELECT ingredients_status,COUNT(*) FROM recipes r WHERE deleted_at IS NULL AND " +
                                                     self.recipe_visibility_sql() + " GROUP BY ingredients_status")}

    def _nutrition_pending_sql(self):
        return ("FROM recipes r WHERE r.deleted_at IS NULL AND " + self.recipe_visibility_sql() +
                " AND r.calories_per_serving IS NULL AND r.ingredients_status='ok' "
                "AND (SELECT COUNT(*) FROM recipe_ingredients i WHERE i.recipe_id=r.id)>=3 ")

    def recipes_pending_nutrition(self, limit=50):
        with self.conn() as c:
            return [dict(row) for row in c.execute(
                "SELECT r.id,r.name,r.servings,(SELECT COUNT(*) FROM recipe_ingredients i WHERE i.recipe_id=r.id) AS ing_count " +
                self._nutrition_pending_sql() + "ORDER BY r.id LIMIT ?", (limit,))]

    def recipes_claim_pending_nutrition(self, *, limit, owner):
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT r.id,r.name,r.servings " + self._nutrition_pending_sql() +
                             "AND (r.nutrition_claim_owner IS NULL OR r.nutrition_claimed_at<?) ORDER BY r.id LIMIT ?",
                             (time.time()-30*60, max(1, int(limit)))).fetchall()
            for row in rows:
                c.execute("UPDATE recipes SET nutrition_claimed_at=?,nutrition_claim_owner=? WHERE id=?",
                          (time.time(), owner, row["id"]))
            return [dict(row) for row in rows]

    def recipes_pending_nutrition_count(self):
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) " + self._nutrition_pending_sql()).fetchone()[0])

    def background_task_enqueue(self, kind, payload, **values):
        payload = dict(payload)
        if self.account_id > 0:
            payload.setdefault("account_id", self.account_id)
        return super().background_task_enqueue(kind, payload, **values)

    def background_task_get(self, task_id):
        item = super().background_task_get(task_id)
        if item and (item.get("payload") or {}).get("account_id") not in (None, self.account_id):
            return None
        if item and (item.get("payload") or {}).get("recipe_id") and not self.recipe_get(item["payload"]["recipe_id"]):
            return None
        return item

    def background_task_list(self, **values):
        return [item for item in super().background_task_list(**values)
                if (item.get("payload") or {}).get("account_id") in (None, self.account_id)
                and (not (item.get("payload") or {}).get("recipe_id") or self.recipe_get(item["payload"]["recipe_id"]))]


def _guard_recipe_method(name):
    parent = getattr(Database, name)

    def guarded(self, recipe_id, *args, **kwargs):
        if not self.recipe_get(recipe_id, include_pending=True):
            raise LookupError("Rezept nicht gefunden")
        return parent(self, recipe_id, *args, **kwargs)
    return guarded


# These content helpers are also used from jobs and file operations. Guard their
# recipe identity at the database boundary, including aggregate ingredient reads.
for _name in (
    "recipe_ingredients_get", "recipe_steps_get", "recipe_tags_get", "recipe_set_servings",
    "recipe_tags_set", "recipe_auto_tags_set", "recipe_steps_set", "recipe_set_verified", "recipe_set_nutrition",
    "recipe_set_extraction_result", "recipe_apply_extraction_result", "recipe_cooking_progress_get",
    "recipe_cooking_progress_set", "recipe_cooking_progress_clear", "recipe_snapshot", "recipe_version_create",
    "recipe_soft_delete", "recipe_restore", "recipe_delete", "recipe_delete_with_history",
    "recipe_image_generation_status",
    "recipe_claim_nutrition", "recipe_release_nutrition_claim", "recipe_image_backup_create",
    "recipe_image_backup_for_batch", "audit_ai_finding_set",
    "recipe_source_snapshot_create", "recipe_source_snapshot_state", "recipe_source_snapshot_accept_latest",
    "recipe_variant_finalize",
):
    setattr(HouseholdDatabase, _name, _guard_recipe_method(_name))
