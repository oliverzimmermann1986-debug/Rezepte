"""Shared recipe content with independent household collection membership.

The importer remains the editor. Pending imports and unpublished variants retain
their household namespace; publishing does not expose inbox/history records.
"""
from __future__ import annotations

import time


def migrate_schema(c):
    if c.execute("SELECT 1 FROM schema_migrations WHERE version=273").fetchone():
        return
    c.execute("ALTER TABLE recipes ADD COLUMN is_global INTEGER NOT NULL DEFAULT 0 CHECK(is_global IN (0,1))")
    c.execute("ALTER TABLE account_recipe_state ADD COLUMN is_saved INTEGER NOT NULL DEFAULT 1 CHECK(is_saved IN (0,1))")
    # Preserve IDs, files, source keys, authorship and all personal values.
    c.execute("INSERT OR IGNORE INTO account_recipe_state(account_id,recipe_id,saved_at) "
              "SELECT owner_account_id,id,? FROM recipes WHERE owner_account_id IS NOT NULL", (time.time(),))
    c.execute("UPDATE recipes SET is_global=1 WHERE COALESCE(ingredients_status,'')<>'variant_pending'")
    c.execute("CREATE INDEX idx_household_saved_recipes ON account_recipe_state(account_id,is_saved,recipe_id)")
    c.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(273,'shared_recipes_and_independent_household_flag',?)", (time.time(),))
