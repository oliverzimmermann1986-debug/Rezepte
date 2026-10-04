"""Account lifecycle regressions through HTTP and independent OS processes."""
from concurrent.futures import ThreadPoolExecutor
import io
import subprocess
import sys
import threading
import time

from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
from PIL import Image
import pytest

from app import accounts, auth, tenancy
from app.db import Database
from app.routes import api_auth, api_pending, sharing
from app.security import LoginRateLimiter
from app.tenant_db import HouseholdDatabase
from tests.test_tenants import _recipe, households  # noqa: F401


def test_deleted_creators_name_cannot_take_over_household_shares(households, monkeypatch):
    client, db, users, login = households
    monkeypatch.setattr(sharing, "_serializer", lambda: URLSafeTimedSerializer("lifecycle-share-key-" * 4, salt=sharing.SHARE_SALT))
    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "registration_limiter", LoginRateLimiter())
    rid = _recipe(db, "StableShare", "https://recipes.example/stable-share")
    login("anna")
    created = client.post(f"/api/recipes/{rid}/share", json={})
    assert created.status_code == 200, created.text
    share_id = created.json()["share_id"]
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    login("operator")
    assert client.delete(f"/api/users/{users['anna'][0]}").status_code == 200
    login("bert")
    assert [r["id"] for r in client.get(f"/api/recipes/{rid}/shares").json()["items"]] == [share_id]
    client.headers.pop("Authorization")
    registered = client.post("/api/auth/register", json={"username": "anna", "password": "synthetic-lifecycle-password"})
    assert registered.status_code == 201, registered.text
    client.headers["Authorization"] = "Bearer " + registered.json()["token"]
    assert client.get("/api/account").json()["id"] != users["anna"][1]
    assert client.get(f"/api/recipes/{rid}/shares").json()["items"] == []
    assert client.delete(f"/api/recipes/{rid}/shares/{share_id}").status_code == 404
    assert db.recipe_share_link_get(share_id)["revoked_at"] is None
    login("bert")
    assert client.delete(f"/api/recipes/{rid}/shares/{share_id}").status_code == 200


def test_v261_share_migration_does_not_assign_recreated_or_missing_creators(households):
    _, db, users, _ = households
    private_id = _recipe(db, "PrivateShare", "https://recipes.example/private-share", owner=users["anna"][1])
    global_id = _recipe(db, "GlobalShare", "https://recipes.example/global-share")
    for share_id, rid, name in (("private", private_id, "missing"), ("current", global_id, "anna"),
                                 ("recreated", global_id, "bert"), ("missing", global_id, "gone")):
        db.recipe_share_link_create(share_id, rid, expires_at=time.time()+3600, created_by=name)
    with db.conn() as c:
        c.execute("UPDATE users SET created_at=? WHERE username='bert'", (time.time()+30,))
        c.execute("DELETE FROM schema_migrations WHERE version=262")
        c.execute("DROP INDEX idx_recipe_share_links_owner")
        c.execute("ALTER TABLE recipe_share_links DROP COLUMN owner_account_id")
    upgraded = Database(db.path)
    assert upgraded.recipe_share_link_get("private")["owner_account_id"] == users["anna"][1]
    assert upgraded.recipe_share_link_get("current")["owner_account_id"] == users["anna"][1]
    for share_id in ("recreated", "missing"):
        row = upgraded.recipe_share_link_get(share_id)
        assert row["owner_account_id"] is None and row["revoked_at"] is None
    with upgraded.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    before = upgraded.recipe_share_links_list(global_id)
    Database(db.path)
    assert upgraded.recipe_share_links_list(global_id) == before


def test_import_in_flight_and_queued_task_block_join_then_pending_moves(households, monkeypatch):
    client, db, users, _ = households
    old_account, target = users["bert"][1], users["anna"][1]
    invitation = accounts.invite(db, users["anna"][0])
    bearer = {"Authorization": "Bearer " + auth.create_session("bert")}
    reached, resume = threading.Event(), threading.Event()
    original = HouseholdDatabase.global_recipe_for_url

    def lookup(scoped, url):
        if scoped.import_owner == old_account and not resume.is_set():
            reached.set()
            assert resume.wait(15)
        return original(scoped, url)

    monkeypatch.setattr(HouseholdDatabase, "global_recipe_for_url", lookup)
    monkeypatch.setattr(api_pending, "enqueue", db.background_task_enqueue)
    with ThreadPoolExecutor(max_workers=1) as pool:
        importing = pool.submit(client.post, "/api/pending/import-url", headers=bearer,
                                json={"url": "https://recipes.example/overlap", "visibility": "private"})
        try:
            assert reached.wait(15)
            joined = client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]})
            assert joined.status_code == 409, joined.text
        finally:
            resume.set()
        accepted = importing.result(timeout=15)
    assert accepted.status_code == 200, accepted.text
    assert client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]}).status_code == 409
    task_id = accepted.json()["task_id"]
    db.background_task_finish(task_id, ok=True, result={"ok": True})
    assert client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]}).status_code == 200
    with db.conn() as c:
        row = c.execute("SELECT owner_account_id FROM pending WHERE source_url=?", ("https://recipes.example/overlap",)).fetchone()
        assert row[0] == target
        assert c.execute("SELECT 1 FROM user_accounts WHERE id=?", (old_account,)).fetchone() is None
    assert len(client.get("/api/account/imports", headers=bearer).json()["items"]) == 1


@pytest.mark.parametrize("path,body", [
    ("/api/pending/import-url", {"url": "https://recipes.example/stale", "visibility": "private"}),
    ("/api/cart/add", {"name": "Stale apple", "amount": 1, "unit": "Stück"}),
])
def test_request_with_scope_captured_before_join_is_rejected_without_writes(households, monkeypatch, path, body):
    client, db, users, _ = households
    invitation = accounts.invite(db, users["anna"][0])
    bearer = {"Authorization": "Bearer " + auth.create_session("bert")}
    captured, resume = threading.Event(), threading.Event()
    original = tenancy.scope_for_request

    def scope(request):
        result = original(request)
        if request.url.path == path:
            captured.set()
            assert resume.wait(15)
        return result

    monkeypatch.setattr(tenancy, "scope_for_request", scope)
    monkeypatch.setattr(api_pending, "enqueue", lambda *a, **k: pytest.fail("Stale request must not enqueue"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.post, path, headers=bearer, json=body)
        try:
            assert captured.wait(15)
            assert client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]}).status_code == 200
        finally:
            resume.set()
        rejected = request.result(timeout=15)
    assert rejected.status_code == 409, rejected.text
    assert rejected.headers["cache-control"] == "private, no-store"
    assert rejected.headers["x-content-type-options"] == "nosniff"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM shopping_cart").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["file", "photo"])
def test_synchronous_analysis_blocks_join_and_releases_guard_on_completion(households, monkeypatch, kind):
    client, db, users, _ = households
    account = users["bert"][1]
    scoped = HouseholdDatabase(db, tenancy.HouseholdScope(account), import_owner=account)
    scoped.pending_add("https://recipes.example/photo", content_type="recipe")
    invitation = accounts.invite(db, users["anna"][0])
    bearer = {"Authorization": "Bearer " + auth.create_session("bert")}
    reached, resume = threading.Event(), threading.Event()

    class Job:
        def process_attachment(self, *args):
            reached.set()
            assert resume.wait(15)
            return {"status": "pending"}

        def attach_pending_photo(self, *args):
            return self.process_attachment(*args)

    monkeypatch.setattr(tenancy, "scoped_scraper", lambda *a, **k: Job())
    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: Job())
    data = io.BytesIO()
    Image.new("RGB", (2, 2)).save(data, format="PNG")
    path = "/api/pending/import-file" if kind == "file" else "/api/pending/scan-photo?url=https%3A%2F%2Frecipes.example%2Fphoto"
    with ThreadPoolExecutor(max_workers=1) as pool:
        importing = pool.submit(client.post, path, headers=bearer, data={"visibility": "private"},
                                files={"file": ("recipe.png", data.getvalue(), "image/png")})
        try:
            assert reached.wait(15)
            assert client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]}).status_code == 409
        finally:
            resume.set()
        response = importing.result(timeout=15)
    assert response.status_code == 200, response.text
    assert client.post("/api/account/invitations/accept", headers=bearer, json={"token": invitation["token"]}).status_code == 200


def test_lifecycle_guard_is_shared_with_a_separate_process(households):
    _, db, users, _ = households
    invitation = accounts.invite(db, users["anna"][0])
    account = users["bert"][1]
    path = db.path.parent / "locks" / f"{db.path.name}.household-{account}.lock"
    code = "from app.jobs.locks import file_lock_path_or_none\nfrom pathlib import Path\nimport sys\nwith file_lock_path_or_none(Path(sys.argv[1])) as lock:\n assert lock is not None\n print('locked',flush=True)\n sys.stdin.readline()\n"
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(HTTPException) as error:
            accounts.accept(db, users["bert"][0], invitation["token"])
        assert error.value.status_code == 409
    finally:
        child.communicate("release\n", timeout=10)
    accounts.accept(db, users["bert"][0], invitation["token"])


@pytest.mark.parametrize("data_kind", ["recipe", "pending", "cart", "task"])
def test_deleting_last_member_with_data_or_import_is_rejected_atomically(households, data_kind):
    client, db, users, login = households
    uid, aid = users["anna"]
    scoped = HouseholdDatabase(db, tenancy.HouseholdScope(aid))
    if data_kind == "recipe":
        _recipe(db, "RetainedPrivate", "https://recipes.example/retain", owner=aid)
    elif data_kind == "pending":
        scoped.pending_add("https://recipes.example/pending", content_type="recipe")
    elif data_kind == "cart":
        login("anna")
        assert client.post("/api/cart/add", json={"name": "Retained", "amount": 1}).status_code == 200
    else:
        db.background_task_enqueue("share_ingest", {"url": "https://recipes.example/task", "account_id": aid})
    login("operator")
    response = client.delete(f"/api/users/{uid}")
    assert response.status_code == 409, response.text
    assert db.user_get_by_name("anna") is not None
    assert accounts.view(db, uid)["id"] == aid


def test_empty_disabled_last_member_can_still_be_deleted(households):
    client, db, users, login = households
    db.user_set_disabled(users["anna"][0], True)
    login("operator")
    assert client.delete(f"/api/users/{users['anna'][0]}").status_code == 200
    assert db.user_get_by_name("anna") is None


def test_guard_releases_after_route_validation_failure(households):
    client, _, users, login = households
    login("anna")
    assert client.post("/api/pending/import-url", json={"url": "http://localhost/private"}).status_code == 400
    assert client.post("/api/account/invitations").status_code == 201


def test_global_url_import_stays_global_in_persisted_queue_and_worker(households, monkeypatch):
    client, db, users, login = households
    from app.db import get_db
    from app.routes import api_share

    def enqueue(kind, payload, **values):
        # Exercise the real request facade, not the unscoped fixture database.
        return get_db().background_task_enqueue(kind, payload, **values)

    monkeypatch.setattr(api_pending, "enqueue", enqueue)
    login("operator")
    response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/new-global", "visibility": "global"})
    assert response.status_code == 200, response.text
    task = db.background_task_get(response.json()["task_id"])
    assert task["payload"]["account_id"] is None

    class GlobalJob:
        def process_url(self, payload):
            rid = _recipe(db, "ImportedGlobal", payload["url"])
            return {"status": "auto", "recipe_id": rid}

    monkeypatch.setattr(api_share.scraper_job, "get_scraper_job", lambda: GlobalJob())
    monkeypatch.setattr(tenancy, "scoped_scraper", lambda *a: pytest.fail("Global worker must not select private scope"))
    result = api_share.run_share_ingest_task(task["payload"])
    assert result["ok"] and db.recipe_get(result["recipe_id"])["owner_account_id"] is None
    login("bert")
    assert client.get(f"/api/recipes/{result['recipe_id']}").status_code == 200


def test_share_token_import_remains_global_when_sender_is_also_logged_in(households, monkeypatch):
    client, db, _, login = households
    import hashlib
    from app.config_store import get_config
    from app.db import get_db
    from app.routes import api_share
    secret = "synthetic-device-token-" * 3
    db.share_intake_token_create("synthetic-device", hashlib.sha256(secret.encode()).hexdigest(), "Test device", "operator")
    original_get = get_config().get

    def config(*keys, default=None):
        return True if keys == ("web", "share_enabled") else original_get(*keys, default=default)

    monkeypatch.setattr(get_config(), "get", config)
    monkeypatch.setattr(api_share, "enqueue", lambda kind, payload, **values: get_db().background_task_enqueue(kind, payload, **values))
    login("operator")
    response = client.post("/api/share", json={"url": "https://recipes.example/token-global", "token": secret})
    assert response.status_code == 200, response.text
    assert db.background_task_get(response.json()["task_id"])["payload"]["account_id"] is None
