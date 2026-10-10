"""Explicit sole-member deletion with a durable, recoverable file-erasure outbox.

The SQL transaction removes access and private records. Files are erased only
after commit; a crash or filesystem error leaves a persistent pending receipt.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

from fastapi import HTTPException

from .config_store import get_config
from .jobs.locks import file_lock_path_or_none
from .tenancy import user_household_guard

logger = logging.getLogger(__name__)
CONFIRMATION = "HAUSHALT LÖSCHEN"
ACCOUNT_TABLES = (
    "account_recipe_state", "shopping_cart", "shopping_recurring", "shopping_products",
    "shopping_exclusions", "meal_plan_entries", "recipe_cook_history", "recipe_cook_notes",
    "household_cookbooks", "household_meal_wishes", "shopping_deleted_items",
    "shopping_sync_operations", "import_budget_usage",
)


def migrate_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS household_purges (
        id TEXT PRIMARY KEY, account_id INTEGER NOT NULL UNIQUE,
        manifest_json TEXT NOT NULL, created_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at REAL,
        error TEXT)""")
    # Non-personal tombstones also reject stale system workers after logout.
    c.execute("CREATE TABLE IF NOT EXISTS deleted_households (account_id INTEGER PRIMARY KEY, deleted_at REAL NOT NULL)")
    for table, column in [*( (table, "account_id") for table in ACCOUNT_TABLES),
                          *((table, "owner_account_id") for table in ("recipes", "history", "pending"))]:
        for operation in ("INSERT", "UPDATE"):
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_deleted_household_{operation.lower()}
                BEFORE {operation} ON {table}
                WHEN EXISTS(SELECT 1 FROM deleted_households WHERE account_id=NEW.{column})
                BEGIN SELECT RAISE(ABORT, 'household_deleted'); END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS background_tasks_deleted_household
        BEFORE INSERT ON background_tasks
        WHEN json_valid(NEW.payload_json) AND EXISTS(SELECT 1 FROM deleted_households
            WHERE account_id=json_extract(NEW.payload_json,'$.account_id'))
        BEGIN SELECT RAISE(ABORT, 'household_deleted'); END""")
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(272,?,?)",
              ("explicit_household_deletion_outbox", time.time()))


def _roots(db):
    cfg = get_config()
    return {
        "recipes": Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve(),
        "temporary": Path(cfg.get("paths", "temp_dir", default="/opt/scrapper/temp")).resolve(),
        "trash": Path(cfg.get("safety", "trash_dir", default="/opt/scrapper/data/trash")).resolve(),
        "images": (Path(cfg.get("paths", "data_dir", default=str(db.path.parent)) or db.path.parent)
                   / "recipe-image-originals").resolve(),
    }


def _linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0)
                                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _contained(path, root):
    """Reject roots, traversal and symlink/junction ancestors, including absent leaves."""
    path = Path(os.path.abspath(path))
    root = Path(root)
    if root.resolve() != root or (os.path.lexists(root) and _linked(root)):
        raise ValueError("Linked erasure root")
    relative = path.relative_to(root)
    if not relative.parts:
        raise ValueError("Unsafe erasure target")
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _linked(current):
            raise ValueError("Linked erasure target")
    if path.resolve() != path:
        raise ValueError("Unsafe erasure target")
    return path


def _manifest(c, db, account_id, recipes):
    roots = _roots(db)
    paths = set()

    def add(path, kind, *, household_tree=False):
        if not path:
            return
        candidate = _contained(Path(path), roots[kind])
        if household_tree:
            parts = candidate.relative_to(roots[kind]).parts
            prefix = ".households" if kind == "recipes" else "households"
            if len(parts) < 2 or parts[0] != prefix or not parts[1].isdigit() or int(parts[1]) <= 0:
                raise ValueError("Unassigned household file")
        paths.add((kind, str(candidate)))

    add(roots["recipes"] / ".households" / str(account_id), "recipes", household_tree=True)
    add(roots["temporary"] / "households" / str(account_id), "temporary", household_tree=True)
    recipe_ids = {row["id"] for row in recipes}
    for recipe in recipes:
        for key in ("folder_path", "deleted_folder_path"):
            if recipe[key]:
                if key == "folder_path" and recipe[key] == f"__trash__/{recipe['id']}":
                    continue
                # A soft-deleted recipe's old path may now belong to another recipe.
                occupied = c.execute("SELECT owner_account_id FROM recipes WHERE folder_path=? AND id!=?",
                                     (recipe[key], recipe["id"])).fetchone()
                if occupied and occupied[0] != account_id and recipe["deleted_at"] is None:
                    raise ValueError("Foreign recipe folder")
                if not occupied:
                    add(recipe[key], "recipes", household_tree=True)
    for row in c.execute("SELECT target_dir FROM history WHERE owner_account_id=?", (account_id,)):
        if row[0]:
            add(row[0], "recipes", household_tree=True)
    for row in c.execute("SELECT video_path,frame_path FROM pending WHERE owner_account_id=?", (account_id,)):
        for value in row:
            if value:
                kind = "recipes" if Path(os.path.abspath(value)).is_relative_to(roots["recipes"]) else "temporary"
                add(value, kind, household_tree=True)
    deleted_ids = []
    foreign = []
    for row in c.execute("SELECT * FROM deleted_history"):
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        belongs = (isinstance(metadata, dict) and isinstance(metadata.get("recipe_id"), int)
                   and metadata["recipe_id"] in recipe_ids)
        belongs = belongs or str(row["url"] or "").startswith(f"private-recipe://{account_id}/")
        historical_id = metadata.get("recipe_id") if isinstance(metadata, dict) else None
        current_owner = c.execute("SELECT owner_account_id FROM recipes WHERE id=?", (historical_id,)).fetchone() if isinstance(historical_id, int) else None
        target = Path(os.path.abspath(row["target_dir"])) if row["target_dir"] else None
        own_tree = roots["recipes"] / ".households" / str(account_id)
        if not current_owner and target and target.is_relative_to(own_tree):
            belongs = True
        if isinstance(metadata, dict) and metadata.get("owner_account_id") == account_id:
            belongs = True
        if current_owner and current_owner[0] != account_id:
            belongs = False
        if not belongs:
            if row["quarantine_path"]:
                foreign.append(Path(os.path.abspath(row["quarantine_path"])))
            continue
        deleted_ids.append(row["id"])
        if isinstance(historical_id, int) and historical_id > 0:
            recipe_ids.add(historical_id)
        if row["quarantine_path"]:
            # Each quarantine entry has its own parent, including its metadata.
            add(Path(row["quarantine_path"]).parent, "trash")
    for row in c.execute("SELECT recipe_id,backup_path FROM recipe_image_backups"):
        if row["recipe_id"] in recipe_ids:
            relative = Path(row["backup_path"])
            if len(relative.parts) != 3 or relative.parts[1] != str(row["recipe_id"]):
                raise ValueError("Unassigned image original")
            add(roots["images"] / row["backup_path"], "images")
        else:
            foreign.append(Path(os.path.abspath(roots["images"] / row["backup_path"])))
    # Merged households may still have files beneath a previous household ID.
    # Reject any overlap with another owner rather than deleting an entire tree.
    for table, columns in (("recipes", ("folder_path", "deleted_folder_path")),
                           ("history", ("target_dir",)), ("pending", ("video_path", "frame_path"))):
        for row in c.execute(f"SELECT {','.join(columns)} FROM {table} WHERE owner_account_id IS NOT ?", (account_id,)):
            foreign.extend(Path(os.path.abspath(value)) for value in row if value)
    for _, path in paths:
        candidate = Path(path)
        if any(other == candidate or other.is_relative_to(candidate) or candidate.is_relative_to(other) for other in foreign):
            raise ValueError("Shared erasure target")
    return {"roots": {kind: str(root) for kind, root in roots.items()},
            "paths": [{"kind": kind, "path": path} for kind, path in sorted(paths)]}, deleted_ids, recipe_ids


def _scrub_maintenance_state(c, recipe_ids):
    """Remove private recipe snapshots from shared image-batch retry receipts."""
    for row in c.execute("SELECT id,result_json FROM maintenance_runs WHERE kind='recipe_image_backfill'").fetchall():
        try:
            state = json.loads(row["result_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(state, dict):
            continue
        changed = False
        for key in ("recipe_ids", "completed_ids", "skipped_ids"):
            values = state.get(key)
            if isinstance(values, list):
                retained = [value for value in values if not isinstance(value, int) or value not in recipe_ids]
                changed = changed or retained != values
                state[key] = retained
        errors = state.get("errors")
        if isinstance(errors, list):
            retained = [value for value in errors if not isinstance(value, dict)
                        or not isinstance(value.get("recipe_id"), int) or value["recipe_id"] not in recipe_ids]
            changed = changed or retained != errors
            state["errors"] = retained
        if changed:
            # These are history-only receipts: active batches were rejected.
            # Their former totals no longer describe the redacted inventory.
            for key in ("total", "processed", "backup_processed", "error_count"):
                state.pop(key, None)
            c.execute("UPDATE maintenance_runs SET result_json=? WHERE id=?", (json.dumps(state), row["id"]))


def delete_household(db, user_id, expected_version):
    """Called only after fresh authentication and explicit API confirmation."""
    from .oidc import queue_deleted_user
    from .recipes.manage import _recipe_mutation_lock
    from .recipes.image_publish import image_publication_lock
    receipt = uuid.uuid4().hex
    with user_household_guard(db, user_id) as locked_account, ExitStack() as locks:
        with db.conn() as c:
            initial = c.execute("SELECT id,folder_path,deleted_at FROM recipes WHERE owner_account_id=? ORDER BY id", (locked_account,)).fetchall()
        ids = [row["id"] for row in initial]
        for recipe in initial:
            try:
                locks.enter_context(_recipe_mutation_lock(db, recipe["id"]))
                if recipe["deleted_at"] is None and Path(recipe["folder_path"]).is_dir():
                    folder = _contained(Path(recipe["folder_path"]), _roots(db)["recipes"])
                    locks.enter_context(image_publication_lock(folder))
            except (RuntimeError, TimeoutError, ValueError):
                raise HTTPException(409, "Ein Rezept wird gerade bearbeitet. Bitte danach erneut löschen.") from None
        with db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or user["disabled"] or user["session_version"] != expected_version:
                raise HTTPException(409, "Dein Konto wurde inzwischen geändert. Bitte erneut anmelden und bestätigen.")
            account = c.execute("SELECT a.* FROM user_accounts a JOIN account_members m ON a.id=m.account_id WHERE m.user_id=?", (user_id,)).fetchone()
            if not account or account["id"] != locked_account or account["owner_user_id"] != user_id:
                raise HTTPException(409, "Dein Haushalt hat sich geändert. Bitte die Ansicht aktualisieren.")
            account_id = account["id"]
            if c.execute("SELECT COUNT(*) FROM account_members WHERE account_id=?", (account_id,)).fetchone()[0] != 1:
                raise HTTPException(409, "Der Haushalt hat weitere Mitglieder. Du kannst nur dein eigenes Konto löschen; gemeinsame Daten bleiben erhalten.")
            db._assert_admin_survives(c, user, role="user", disabled=True)
            recipes = c.execute("SELECT * FROM recipes WHERE owner_account_id=? ORDER BY id", (account_id,)).fetchall()
            if [row["id"] for row in recipes] != ids:
                raise HTTPException(409, "Deine Rezepte haben sich geändert. Bitte erneut versuchen.")
            busy = c.execute("SELECT 1 FROM background_tasks WHERE status='running' AND json_valid(payload_json) AND "
                             "(json_extract(payload_json,'$.account_id')=? OR kind='recipe_image_backfill' OR "
                             "json_extract(payload_json,'$.recipe_id') IN (SELECT id FROM recipes WHERE owner_account_id=?)) LIMIT 1",
                             (account_id, account_id)).fetchone()
            shared_batch = c.execute("SELECT 1 FROM background_tasks WHERE kind='recipe_image_backfill' AND status='queued' "
                                     "AND json_valid(payload_json) AND json_extract(payload_json,'$.account_id') IS NULL LIMIT 1").fetchone()
            if busy or shared_batch or any(row["ingredients_status"] == "running" or row["nutrition_claimed_at"] is not None
                           or row["image_generation_status"] == "running" for row in recipes):
                raise HTTPException(409, "Ein Import oder eine Analyse läuft noch. Warte auf den Abschluss und bestätige die Löschung danach erneut.")
            try:
                manifest, deleted_ids, historical_recipe_ids = _manifest(c, db, account_id, recipes)
            except (OSError, ValueError):
                raise HTTPException(409, "Die privaten Dateien können derzeit nicht sicher zugeordnet werden. Bitte kontaktiere den Betreiber; es wurde nichts gelöscht.") from None
            c.execute("INSERT INTO household_purges(id,account_id,manifest_json,created_at) VALUES(?,?,?,?)",
                      (receipt, account_id, json.dumps(manifest), time.time()))
            c.execute("INSERT INTO deleted_households(account_id,deleted_at) VALUES(?,?)", (account_id, time.time()))
            c.execute("DELETE FROM maintenance_runs WHERE kind='recipe_image_backfill' AND id IN ("
                      "SELECT json_extract(payload_json,'$.run_id') FROM background_tasks WHERE json_valid(payload_json) "
                      "AND json_extract(payload_json,'$.account_id')=?)", (account_id,))
            c.execute("DELETE FROM background_tasks WHERE json_valid(payload_json) AND (json_extract(payload_json,'$.account_id')=? OR "
                      "json_extract(payload_json,'$.recipe_id') IN (SELECT id FROM recipes WHERE owner_account_id=?))", (account_id, account_id))
            c.execute("DELETE FROM recipe_share_links WHERE owner_account_id=?", (account_id,))
            _scrub_maintenance_state(c, historical_recipe_ids)
            for recipe_id in historical_recipe_ids:
                for table in ("recipe_versions", "recipe_image_backups", "audit_ai_findings"):
                    c.execute(f"DELETE FROM {table} WHERE recipe_id=?", (recipe_id,))
            for row in recipes:
                c.execute("DELETE FROM sync_errors WHERE folder_path=?", (row["folder_path"],))
            c.execute("DELETE FROM recipes WHERE owner_account_id=?", (account_id,))
            for table in ACCOUNT_TABLES:
                c.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))
            for table in ("history", "pending"):
                c.execute(f"DELETE FROM {table} WHERE owner_account_id=?", (account_id,))
            c.execute("DELETE FROM download_failures WHERE url LIKE ?", (f"private-recipe://{account_id}/%",))
            c.executemany("DELETE FROM deleted_history WHERE id=?", [(value,) for value in deleted_ids])
            c.execute("DELETE FROM share_intake_tokens WHERE created_by=? COLLATE NOCASE AND created_at>=?", (user["username"], user["created_at"]))
            queue_deleted_user(c, user_id)
            c.execute("DELETE FROM users WHERE id=?", (user_id,))
    complete = run_purge(db, receipt)
    return {"ok": True, "status": "deleted" if complete else "deletion_pending",
            "message": ("Dein Konto und dein privater Haushalt wurden gelöscht." if complete else
                        "Dein Konto ist gelöscht und der Zugriff gesperrt. Die dauerhafte Dateibereinigung wird automatisch fortgesetzt.")}


def _erase(path, root):
    path = _contained(Path(path), Path(root))
    if not os.path.lexists(path):
        return
    if path.is_dir():
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                if _linked(Path(parent) / name):
                    raise ValueError("Linked file in erasure target")
        shutil.rmtree(path)
    else:
        path.unlink()


def run_purge(db, receipt):
    with file_lock_path_or_none(db.path.parent / "locks" / f"household-purge-{receipt}.lock") as lock:
        if lock is None:
            return False
        with db.conn() as c:
            row = c.execute("SELECT * FROM household_purges WHERE id=?", (receipt,)).fetchone()
        if not row:
            return True
        try:
            manifest = json.loads(row["manifest_json"])
            for item in manifest["paths"]:
                _erase(item["path"], manifest["roots"][item["kind"]])
        except (OSError, ValueError, KeyError, TypeError):
            with db.conn() as c:
                c.execute("UPDATE household_purges SET attempts=attempts+1,last_attempt_at=?,error=? WHERE id=?",
                          (time.time(), "Dateibereinigung ausstehend", receipt))
            logger.warning("Household file erasure remains pending; receipt=%s", receipt)
            return False
        with db.conn() as c:
            c.execute("DELETE FROM household_purges WHERE id=?", (receipt,))
        return True


def recover_purges(db, limit=10):
    """Startup/periodic recovery; completed manifests are removed, failures retry."""
    with db.conn() as c:
        receipts = [row[0] for row in c.execute("SELECT id FROM household_purges ORDER BY COALESCE(last_attempt_at,0),created_at LIMIT ?", (limit,))]
    for receipt in receipts:
        run_purge(db, receipt)
