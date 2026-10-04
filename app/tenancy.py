"""Request-local household identity and the data-preserving tenant migration.

The context is copied into FastAPI's worker threads. Background jobs have no
request identity and explicitly select their import household from their payload.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from .auth import require_auth


@dataclass(frozen=True)
class HouseholdScope:
    account_id: int
    is_admin: bool = False
    is_guest: bool = False
    user_id: int | None = None


CURRENT_HOUSEHOLD: ContextVar[HouseholdScope | None] = ContextVar("household", default=None)
RECIPE_LIBRARY: ContextVar[str] = ContextVar("recipe_library", default="all")
_HELD_HOUSEHOLDS: ContextVar[frozenset] = ContextVar("held_households", default=frozenset())


@contextmanager
def household_write_guard(db, scope: HouseholdScope | None, *, require_active_user=True):
    """Serialize a request with moves/deletions, without holding a SQL writer.

    OS locks survive neither a crash nor a restart, and work across web workers.
    Membership is checked after acquisition: a previously captured request
    identity must never write into a household that has meanwhile moved.
    """
    if scope is None or scope.account_id <= 0:
        yield
        return
    from .jobs.locks import file_lock_path_or_none
    key = (str(db.path.resolve()), scope.account_id)
    held = _HELD_HOUSEHOLDS.get()
    with ExitStack() as stack:
        if key not in held:
            path = db.path.parent / "locks" / f"{db.path.name}.household-{scope.account_id}.lock"
            lock = stack.enter_context(file_lock_path_or_none(path))
            if lock is None:
                raise HTTPException(409, "Im Haushalt läuft gerade eine Änderung oder ein Import. Bitte danach erneut versuchen.")
            token = _HELD_HOUSEHOLDS.set(held | {key})
            stack.callback(_HELD_HOUSEHOLDS.reset, token)
        with db.conn() as c:
            if scope.user_id is not None:
                user = c.execute("SELECT disabled FROM users WHERE id=?", (scope.user_id,)).fetchone()
                if not user or (user[0] and require_active_user):
                    raise HTTPException(401, "Bitte erneut anmelden")
                member = c.execute("SELECT account_id FROM account_members WHERE user_id=?", (scope.user_id,)).fetchone()
                if not member or int(member[0]) != scope.account_id:
                    raise HTTPException(409, "Dein Haushalt hat sich geändert. Bitte die Ansicht aktualisieren und erneut versuchen.")
            elif not c.execute("SELECT 1 FROM user_accounts WHERE id=?", (scope.account_id,)).fetchone():
                raise HTTPException(409, "Der Haushalt ist nicht mehr aktiv")
        yield


@contextmanager
def user_household_guard(db, user_id: int, *, require_active_user=True):
    with db.conn() as c:
        member = c.execute("SELECT account_id FROM account_members WHERE user_id=?", (user_id,)).fetchone()
    account_id = int(member[0]) if member else None
    scope = HouseholdScope(account_id, user_id=user_id) if account_id else None
    with household_write_guard(db, scope, require_active_user=require_active_user):
        yield account_id


@contextmanager
def household_context(scope: HouseholdScope | None):
    token = CURRENT_HOUSEHOLD.set(scope)
    try:
        yield
    finally:
        CURRENT_HOUSEHOLD.reset(token)


def canonical_source(url: str | None) -> str | None:
    if not url:
        return None
    from .core.recipe_web import normalize_recipe_url
    return normalize_recipe_url(url) or url


def private_source_key(account_id: int, url: str | None) -> str | None:
    if not url:
        return None
    return f"private-recipe://{int(account_id)}/{hashlib.sha256(url.encode('utf-8')).hexdigest()}"


def migrate_schema(c) -> None:
    """Runs within Database's backed-up, locked migration transaction."""
    if c.execute("SELECT 1 FROM schema_migrations WHERE version=261").fetchone():
        return
    previous_factory = c.row_factory
    c.row_factory = sqlite3.Row
    for table, columns in {
        "recipes": (("owner_account_id", "INTEGER"), ("source_url", "TEXT")),
        "history": (("owner_account_id", "INTEGER"), ("source_url", "TEXT")),
        "pending": (("owner_account_id", "INTEGER"), ("source_url", "TEXT")),
        "shopping_cart": (("account_id", "INTEGER NOT NULL DEFAULT 0"),),
        "shopping_recurring": (("account_id", "INTEGER NOT NULL DEFAULT 0"),),
        "recipe_cook_history": (("account_id", "INTEGER NOT NULL DEFAULT 0"),),
    }.items():
        existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns:
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    # These legacy keys were server-wide. Rebuild only the three tables whose
    # unique key must now contain the household, preserving IDs and every value.
    for table, key in (
        ("meal_plan_entries", "UNIQUE(planned_for, recipe_id)"),
        ("shopping_products", "canonical_name TEXT PRIMARY KEY COLLATE NOCASE"),
        ("shopping_exclusions", "canonical_name TEXT PRIMARY KEY COLLATE NOCASE"),
    ):
        if "account_id" in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            continue
        ddl = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        indexes = [r[0] for r in c.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
        )]
        columns = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        ddl = ddl.replace(table, f"{table}_tenant_new", 1)
        ddl = ddl.replace(key, "UNIQUE(account_id, planned_for, recipe_id)" if table == "meal_plan_entries"
                          else "canonical_name TEXT NOT NULL COLLATE NOCASE")
        start = ddl.index("(") + 1
        ddl = ddl[:start] + "account_id INTEGER NOT NULL DEFAULT 0, " + ddl[start:]
        end = ddl.rfind(")")
        if table != "meal_plan_entries":
            ddl = ddl[:end] + ", PRIMARY KEY(account_id, canonical_name)" + ddl[end:]
        c.execute(ddl)
        names = ", ".join(columns)
        c.execute(f"INSERT INTO {table}_tenant_new ({names}) SELECT {names} FROM {table}")
        c.execute(f"DROP TABLE {table}")
        c.execute(f"ALTER TABLE {table}_tenant_new RENAME TO {table}")
        for index in indexes:
            c.execute(index)
    c.execute("""CREATE TABLE IF NOT EXISTS account_recipe_state (
        account_id INTEGER NOT NULL REFERENCES user_accounts(id) ON DELETE CASCADE,
        recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        saved_at REAL NOT NULL, is_favorite INTEGER NOT NULL DEFAULT 0,
        rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
        PRIMARY KEY(account_id, recipe_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS tenant_migration_state (id INTEGER PRIMARY KEY CHECK(id=1), account_id INTEGER NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_recipe_source_scope ON recipes(owner_account_id, source_url)")
    for table in ("shopping_cart", "shopping_recurring", "meal_plan_entries", "recipe_cook_history"):
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_account ON {table}(account_id)")
    for row in c.execute('SELECT id, COALESCE(url, deleted_url) FROM recipes WHERE source_url IS NULL '
                         'AND COALESCE(url, deleted_url) IS NOT NULL').fetchall():
        source = str(row[1])
        if not source.startswith('private-recipe://'):
            c.execute('UPDATE recipes SET source_url=? WHERE id=?', (canonical_source(source), row[0]))
    for table in ("history", "pending"):
        for row in c.execute(f"SELECT url FROM {table} WHERE url IS NOT NULL AND source_url IS NULL").fetchall():
            c.execute(f"UPDATE {table} SET source_url=? WHERE url=?", (canonical_source(row[0]), row[0]))
    from .accounts import _account
    for user in c.execute("SELECT id FROM users ORDER BY id").fetchall():
        _account(c, int(user[0]))
    owner = c.execute("SELECT id FROM users WHERE role='admin' AND disabled=0 ORDER BY id LIMIT 1").fetchone()
    if owner:
        claim_legacy_data(c, int(_account(c, int(owner[0]))["id"]))
    c.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES(261, 'household_isolation_and_global_recipe_links', ?)", (time.time(),))
    c.row_factory = previous_factory


def claim_legacy_data(c, account_id: int) -> None:
    if c.execute("SELECT 1 FROM tenant_migration_state WHERE id=1").fetchone():
        return
    for table in ("shopping_cart", "shopping_recurring", "meal_plan_entries", "shopping_products", "shopping_exclusions"):
        c.execute(f"UPDATE {table} SET account_id=? WHERE account_id=0", (account_id,))
    c.execute("""UPDATE recipe_cook_history SET account_id=COALESCE(
        (SELECT am.account_id FROM account_members am JOIN users u ON u.id=am.user_id
         WHERE u.username=recipe_cook_history.cooked_by COLLATE NOCASE), ?)
        WHERE account_id=0""", (account_id,))
    c.execute("""INSERT OR IGNORE INTO account_recipe_state(account_id,recipe_id,saved_at,is_favorite,rating)
        SELECT ?, id, ?, is_favorite, rating FROM recipes
        WHERE is_favorite=1 OR rating>0""", (account_id, time.time()))
    c.execute("INSERT INTO tenant_migration_state(id,account_id) VALUES(1,?)", (account_id,))


def migrate_share_ownership(c) -> None:
    if c.execute("SELECT 1 FROM schema_migrations WHERE version=262").fetchone():
        return
    if "owner_account_id" not in {r[1] for r in c.execute("PRAGMA table_info(recipe_share_links)")}:
        c.execute("ALTER TABLE recipe_share_links ADD COLUMN owner_account_id INTEGER "
                  "REFERENCES user_accounts(id) ON DELETE SET NULL")
    # Private links have an unambiguous recipe owner. For global links a name
    # is usable only if that login already existed when the link was created.
    # Missing/re-created creators stay unassigned; their public tokens remain
    # valid, but another household cannot acquire their management rights.
    c.execute("""UPDATE recipe_share_links SET owner_account_id=COALESCE(
        (SELECT r.owner_account_id FROM recipes r JOIN user_accounts a ON a.id=r.owner_account_id
         WHERE r.id=recipe_share_links.recipe_id),
        (SELECT am.account_id FROM users u JOIN account_members am ON am.user_id=u.id
         WHERE u.username=recipe_share_links.created_by COLLATE NOCASE
           AND u.created_at<=recipe_share_links.created_at))
        WHERE owner_account_id IS NULL""")
    c.execute("""UPDATE recipe_share_links SET owner_account_id=(
        SELECT a.id FROM tenant_migration_state t JOIN user_accounts a ON a.id=t.account_id WHERE t.id=1)
        WHERE owner_account_id IS NULL AND (created_by IS NULL OR created_by='local')
          AND created_at<=(SELECT applied_at FROM schema_migrations WHERE version=261)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_recipe_share_links_owner ON recipe_share_links(owner_account_id,recipe_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS import_budget_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        created_at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_import_budget_time ON import_budget_usage(created_at,account_id)")
    c.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(262,'household_lifecycle_and_import_budget',?)", (time.time(),))


def migrate_cooking_identity(c) -> None:
    migrated = c.execute("SELECT 1 FROM schema_migrations WHERE version=263").fetchone()
    for table, timestamp in (("recipe_cooking_progress", "started_at"),
                             ("recipe_cooking_completion_requests", "created_at")):
        added = "user_id" not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if added:
            c.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        if added or not migrated:
            # A reused name cannot establish the owner of earlier personal
            # state. Preserve ambiguous rows unassigned; registered logins
            # can read/replay only rows bound to their stable user ID.
            c.execute(f"""UPDATE {table} SET user_id=(SELECT u.id FROM users u
                WHERE u.username={table}.username COLLATE NOCASE
                  AND u.created_at<={table}.{timestamp}) WHERE user_id IS NULL""")
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_user ON {table}(user_id)")
    if not migrated:
        c.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(263,'stable_cooking_user_identity',?)",
                  (time.time(),))


def merge_households(c, source: int, target: int) -> None:
    """Invitation acceptance moves the joining person's data, never deletes it."""
    busy = c.execute("SELECT 1 FROM background_tasks WHERE status IN ('queued','running') "
                     "AND json_extract(payload_json,'$.account_id')=? LIMIT 1", (source,)).fetchone()
    if busy:
        raise HTTPException(409, "Bitte erst die laufenden Importe abschließen und danach die Einladung annehmen")
    c.execute("UPDATE recipe_share_links SET owner_account_id=? WHERE owner_account_id=?", (target, source))
    c.execute("UPDATE import_budget_usage SET account_id=? WHERE account_id=?", (target, source))
    c.execute("""INSERT INTO account_recipe_state(account_id,recipe_id,saved_at,is_favorite,rating)
        SELECT ?,recipe_id,saved_at,is_favorite,rating FROM account_recipe_state WHERE account_id=?
        ON CONFLICT(account_id,recipe_id) DO UPDATE SET
          is_favorite=MAX(account_recipe_state.is_favorite,excluded.is_favorite),
          rating=CASE WHEN account_recipe_state.rating=0 THEN excluded.rating ELSE account_recipe_state.rating END""", (target, source))
    for item in c.execute("SELECT * FROM shopping_cart WHERE account_id=?", (source,)).fetchall():
        existing = c.execute("SELECT * FROM shopping_cart WHERE account_id=? AND canonical_name=? AND unit IS ?",
                             (target, item["canonical_name"], item["unit"])).fetchone() if item["canonical_name"] else None
        if existing:
            amount = None if item["amount"] is None and existing["amount"] is None else (item["amount"] or 0)+(existing["amount"] or 0)
            import json
            sources = list(dict.fromkeys(json.loads(item["source_recipe_ids"] or "[]")+json.loads(existing["source_recipe_ids"] or "[]")))
            c.execute("UPDATE shopping_cart SET amount=?,source_recipe_ids=?,checked=? WHERE id=?",
                      (amount, json.dumps(sources), int(bool(item["checked"] and existing["checked"])), existing["id"]))
            c.execute("DELETE FROM shopping_cart WHERE id=?", (item["id"],))
        else:
            c.execute("UPDATE shopping_cart SET account_id=? WHERE id=?", (target, item["id"]))
    for item in c.execute("SELECT * FROM meal_plan_entries WHERE account_id=?", (source,)).fetchall():
        existing = c.execute("SELECT id FROM meal_plan_entries WHERE account_id=? AND planned_for=? AND recipe_id=?",
                             (target, item["planned_for"], item["recipe_id"])).fetchone()
        if existing:
            c.execute("UPDATE meal_plan_entries SET planned_servings=planned_servings+?,updated_at=? WHERE id=?",
                      (item["planned_servings"], time.time(), existing["id"]))
            c.execute("DELETE FROM meal_plan_entries WHERE id=?", (item["id"],))
        else:
            c.execute("UPDATE meal_plan_entries SET account_id=? WHERE id=?", (target, item["id"]))
    for table in ("shopping_products", "shopping_exclusions"):
        columns = [row[1] for row in c.execute(f"PRAGMA table_info({table})") if row[1] != "account_id"]
        names = ",".join(columns)
        c.execute(f"INSERT OR IGNORE INTO {table}(account_id,{names}) SELECT ?,{names} FROM {table} WHERE account_id=?", (target, source))
        c.execute(f"DELETE FROM {table} WHERE account_id=?", (source,))
    for table in ("shopping_recurring", "recipe_cook_history"):
        c.execute(f"UPDATE {table} SET account_id=? WHERE account_id=?", (target, source))
    for recipe in c.execute("SELECT * FROM recipes WHERE owner_account_id=?", (source,)).fetchall():
        source_url = recipe["source_url"]
        new_key = private_source_key(target, source_url)
        if recipe["url"] and new_key and not c.execute("SELECT 1 FROM recipes WHERE url=? AND id!=?", (new_key, recipe["id"])).fetchone():
            c.execute("UPDATE recipes SET url=? WHERE id=?", (new_key, recipe["id"]))
        c.execute("UPDATE recipes SET owner_account_id=? WHERE id=?", (target, recipe["id"]))
    for table in ("pending", "history"):
        for row in c.execute(f"SELECT * FROM {table} WHERE owner_account_id=?", (source,)).fetchall():
            key = private_source_key(target, row["source_url"] or row["url"])
            if c.execute(f"SELECT 1 FROM {table} WHERE url=?", (key,)).fetchone():
                # Keep resolved import provenance without shadowing the active
                # destination record. Its source_url stays searchable.
                key = row["url"]
            c.execute(f"UPDATE {table} SET owner_account_id=?,url=? WHERE url=?", (target, key, row["url"]))


def scope_for_request(request: Request) -> HouseholdScope | None:
    from .auth import auth_disabled, cached_request_user, request_is_guest, request_user
    from .db import get_db
    if request_is_guest(request):
        return HouseholdScope(-1, is_guest=True)
    if auth_disabled():
        return HouseholdScope(0, is_admin=True)
    username = request_user(request)
    db = get_db()
    user = cached_request_user(request) or (db.user_get_by_name(username) if username else None)
    if user and user.get("legacy_config_user"):
        return HouseholdScope(0, is_admin=True)
    if not user or user.get("disabled"):
        return None
    from .accounts import _account
    with db.conn() as c:
        # Normal requests need no writer lock once membership exists.
        row = c.execute("SELECT account_id FROM account_members WHERE user_id=?", (user["id"],)).fetchone()
        if row:
            account_id = int(row[0])
        else:
            c.execute("BEGIN IMMEDIATE")
            account_id = int(_account(c, int(user["id"]))["id"])
        if user.get("role") == "admin" and not c.execute("SELECT 1 FROM tenant_migration_state WHERE id=1").fetchone():
            if not c.in_transaction:
                c.execute("BEGIN IMMEDIATE")
            claim_legacy_data(c, account_id)
    return HouseholdScope(account_id, is_admin=user.get("role") == "admin", user_id=int(user["id"]))


class HouseholdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # The authenticated print/export URL is outside /api but must use the
        # same household boundary. Public /share routes instead authorize an
        # explicit signed, revocable share token and intentionally stay separate.
        if scope["type"] != "http" or not scope.get("path", "").startswith(("/api/", "/recipe/")):
            await self.app(scope, receive, send)
            return
        from starlette.concurrency import run_in_threadpool
        request = Request(scope)
        identity = await run_in_threadpool(scope_for_request, request)
        with household_context(identity):
            library_token = RECIPE_LIBRARY.set(request.query_params.get("library", "all") if
                                              request.query_params.get("library", "all") in {"all", "global", "mine"} else "all")
            async def private_send(message):
                if message["type"] == "http.response.start":
                    original = message.get("headers", [])
                    cache_control = b"private, no-store"
                    if scope.get("method") in {"GET", "HEAD"} and scope.get("path", "").endswith("/thumb") and message.get("status") in {200, 304}:
                        declared = next((v for k, v in original if k.lower() == b"cache-control"), b"")
                        if b"private" in declared.lower() and (b"must-revalidate" in declared.lower() or b"no-cache" in declared.lower()) and b"public" not in declared.lower() and b"no-store" not in declared.lower():
                            cache_control = b"private, max-age=0, must-revalidate"
                    vary = [v.strip() for k, value in original if k.lower() == b"vary" for v in value.split(b",")]
                    vary.extend([b"Cookie", b"Authorization"])
                    headers = [(k, v) for k, v in original if k.lower() not in (b"cache-control", b"vary")]
                    headers.extend([(b"cache-control", cache_control), (b"vary", b", ".join(dict.fromkeys(vary)))])
                    message = {**message, "headers": headers}
                await send(message)
            try:
                with ExitStack() as writes:
                    # File/photo analysis, pending resolution and ordinary
                    # edits use the same boundary as invitation acceptance.
                    # Due cart entries acquire their guard inside the sync DB
                    # worker; ordinary cart reads need neither lock nor write.
                    if scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
                        from .db import get_db
                        from starlette.responses import JSONResponse
                        try:
                            writes.enter_context(household_write_guard(get_db(), identity))
                        except HTTPException as exc:
                            headers = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                       "Referrer-Policy": "no-referrer",
                                       "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                                       **(exc.headers or {})}
                            if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https":
                                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                               headers=headers)(scope, receive, private_send)
                            return
                    await self.app(scope, receive, private_send)
            finally:
                RECIPE_LIBRARY.reset(library_token)


def require_recipe_editor(request: Request, authorization=Depends(require_auth)):
    from .db import get_db
    identity = CURRENT_HOUSEHOLD.get()
    if identity is None:
        # Authentication above permits an unscoped request only in legacy
        # single-user mode (or when a test supplies its authentication fixture).
        return authorization
    recipe_id = request.path_params.get("recipe_id")
    if recipe_id is None and request.path_params.get("backup_id") is not None:
        backup = get_db().recipe_image_backup_get(int(request.path_params["backup_id"]))
        recipe_id = backup["recipe_id"] if backup else None
    recipe = get_db().recipe_get(int(recipe_id)) if recipe_id is not None else None
    if not recipe:
        raise HTTPException(404, "Rezept nicht gefunden")
    if identity.is_guest or (recipe.get("owner_account_id") is None and not identity.is_admin):
        raise HTTPException(403, "Globale Rezepte können nur Administratoren bearbeiten")


def import_database(request: Request, visibility: str):
    from .db import get_db
    from .tenant_db import HouseholdDatabase
    identity = CURRENT_HOUSEHOLD.get() or scope_for_request(request) or HouseholdScope(0, is_admin=True)
    if visibility not in {"private", "global"}:
        raise HTTPException(422, "Sichtbarkeit muss privat oder global sein")
    if identity.is_guest:
        raise HTTPException(403, "Zum Importieren bitte anmelden")
    if visibility == "private" and identity.account_id <= 0 and CURRENT_HOUSEHOLD.get() is not None:
        raise HTTPException(409, "Private Importe benötigen die Kontenanmeldung. Bitte zuerst mit einem Konto anmelden.")
    if visibility == "global" and not identity.is_admin:
        raise HTTPException(403, "Globale Importe können nur Administratoren anlegen")
    db = get_db()
    return HouseholdDatabase(db, identity, import_owner=identity.account_id if visibility == "private" and identity.account_id > 0 else None)


def scoped_scraper(db, original=None):
    """A per-import copy; never mutate the shared scraper or its configured roots."""
    import copy
    from .jobs.scraper import get_scraper_job
    if original is None:
        original = get_scraper_job()
    # Preserve simple injected import fakes; real ScraperJob instances always
    # carry these roots and their own database handle.
    if not hasattr(original, "recipe_dir"):
        return original
    job = copy.copy(original)
    job.db = db
    if db.import_owner is not None:
        from .core.safety import resolve_directory_under
        # Private trees are hidden from the global filesystem indexer.
        root = job.recipe_dir.resolve()
        target = root / ".households" / str(db.import_owner)
        target.mkdir(parents=True, exist_ok=True)
        job.recipe_dir = resolve_directory_under(target, root)
        job.temp_dir = job.temp_dir / "households" / str(db.import_owner)
        job.temp_dir.mkdir(parents=True, exist_ok=True)
    return job
