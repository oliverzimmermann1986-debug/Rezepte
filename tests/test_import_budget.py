"""Cost limits are persistent, atomic, and precede expensive work."""
from concurrent.futures import ThreadPoolExecutor
import io
import time

from fastapi import HTTPException
from PIL import Image
import pytest

from app import auth, import_budget
from app.config_store import get_config
from app.db import Database
from app.routes import api_auth, api_pending
from app.security import LoginRateLimiter
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, household_context
from tests.test_tenants import _recipe, households  # noqa: F401


@pytest.fixture
def limits(monkeypatch):
    original = get_config().get
    values = {"import_daily_limit": 2, "import_server_daily_limit": 3}

    def get(*keys, default=None):
        if len(keys) == 2 and keys[0] == "web" and keys[1] in values:
            return values[keys[1]]
        return original(*keys, default=default)

    monkeypatch.setattr(get_config(), "get", get)
    return values


def test_account_and_server_limits_precede_pending_and_queue_writes(households, limits, monkeypatch):
    client, db, _, login = households
    queued = []
    monkeypatch.setattr(api_pending, "enqueue", lambda *a, **k: queued.append(a) or len(queued))
    login("anna")
    for index in range(2):
        assert client.post("/api/pending/import-url", json={"url": f"https://recipes.example/quota/{index}"}).status_code == 200
    account_denied = client.post("/api/pending/import-url", json={"url": "https://recipes.example/quota/denied"})
    assert account_denied.status_code == 429 and int(account_denied.headers["retry-after"]) > 0
    login("bert")
    assert client.post("/api/pending/import-url", json={"url": "https://recipes.example/bert/1"}).status_code == 200
    server_denied = client.post("/api/pending/import-url", json={"url": "https://recipes.example/bert/denied"})
    assert server_denied.status_code == 429 and "Servers" in server_denied.json()["detail"]
    assert len(queued) == 3
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 3


def test_linking_an_existing_global_recipe_remains_free_after_budget_exhaustion(households, limits, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "FreeGlobal", "https://recipes.example/free-global")
    limits["import_daily_limit"] = 0
    monkeypatch.setattr(api_pending, "enqueue", lambda *a, **k: pytest.fail("No analysis needed"))
    login("anna")
    response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/free-global"})
    assert response.status_code == 200 and response.json()["recipe_id"] == rid
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 0


def test_queued_url_replay_does_not_consume_another_slot(households, limits, monkeypatch):
    client, db, _, login = households
    limits["import_daily_limit"] = 1
    monkeypatch.setattr(api_pending, "enqueue", db.background_task_enqueue)
    login("anna")
    responses = [client.post("/api/pending/import-url", json={"url": "https://recipes.example/replay"}) for _ in range(2)]
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["task_id"] == responses[1].json()["task_id"]
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 1


def test_server_budget_is_atomic_across_accounts_and_survives_restart(households, limits):
    _, db, users, _ = households
    limits["import_daily_limit"] = 10
    limits["import_server_daily_limit"] = 1

    def reserve(user):
        scoped = HouseholdDatabase(db, HouseholdScope(users[user][1]))
        try:
            with household_context(scoped.scope):
                import_budget.reserve_import(scoped)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ["anna", "bert"])) == [200, 429]
    with pytest.raises(HTTPException) as error:
        import_budget.reserve_import(Database(db.path))
    assert error.value.status_code == 429
    with db.conn() as c:
        c.execute("UPDATE import_budget_usage SET created_at=?", (time.time()-86401,))
    import_budget.reserve_import(db)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["file", "photo", "reanalyze"])
def test_exhausted_budget_does_not_call_synchronous_analyzer(households, limits, monkeypatch, kind):
    client, db, users, login = households
    limits["import_daily_limit"] = 0
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    scoped.pending_add("https://recipes.example/photo", content_type="recipe")
    job = object()
    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: job)
    from app import tenancy
    monkeypatch.setattr(tenancy, "scoped_scraper", lambda *a, **k: job)
    login("anna")
    if kind == "reanalyze":
        response = client.post("/api/pending/reanalyze", json={"url": "https://recipes.example/photo"})
    else:
        data = io.BytesIO()
        Image.new("RGB", (2, 2)).save(data, format="PNG")
        path = "/api/pending/import-file" if kind == "file" else "/api/pending/scan-photo?url=https%3A%2F%2Frecipes.example%2Fphoto"
        response = client.post(path, data={"visibility": "private"}, files={"file": ("recipe.png", data.getvalue(), "image/png")})
    assert response.status_code == 429, response.text


def test_guest_rate_limit_covers_api_and_browser_entry(households, monkeypatch):
    client, _, _, _ = households
    monkeypatch.setattr(api_auth, "guest_limiter", LoginRateLimiter(max_fails=2, window_sec=300, ban_sec=300))
    assert client.post("/api/auth/guest").status_code == 200
    assert client.post("/login/guest", data={}, headers={"Origin": "http://testserver"}, follow_redirects=False).status_code == 303
    client.cookies.clear()  # Native clients send no browser session/CSRF cookie.
    denied = client.post("/api/auth/guest")
    assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0
    assert client.post("/login/guest", data={}, headers={"Origin": "http://testserver"}, follow_redirects=False).status_code == 429


def _configure_image_queue(monkeypatch, db):
    from app.jobs import task_queue
    from app.recipes import image_generation
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {"model": "synthetic-no-ai"})
    monkeypatch.setattr(task_queue, "enqueue", db.background_task_enqueue)


def test_exhausted_analysis_budget_also_blocks_private_image_generation(households, limits, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "LimitedImage", "https://recipes.example/limited-image", owner=users["anna"][1])
    _configure_image_queue(monkeypatch, db)
    limits["import_daily_limit"] = 1
    with household_context(HouseholdScope(users["anna"][1])):
        import_budget.reserve_import(db)
    login("anna")
    response = client.post(f"/api/recipes/{rid}/generate-image", json={})
    assert response.status_code == 429, response.text
    assert int(response.headers["retry-after"]) > 0
    assert db.background_task_list() == []


def test_active_image_replay_is_free_but_new_generation_is_limited(households, limits, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "ImageReplay", "https://recipes.example/image-replay", owner=users["anna"][1])
    _configure_image_queue(monkeypatch, db)
    limits["import_daily_limit"] = 1
    login("anna")
    first = client.post(f"/api/recipes/{rid}/generate-image", json={})
    assert first.status_code == 202, first.text
    replay = client.post(f"/api/recipes/{rid}/generate-image", json={})
    assert replay.status_code == 202 and replay.json()["task_id"] == first.json()["task_id"]
    assert replay.json()["batch_id"] == first.json()["batch_id"]
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 1
    db.background_task_finish(first.json()["task_id"], ok=True, result={})
    assert client.post(f"/api/recipes/{rid}/generate-image", json={}).status_code == 429
    assert len(db.background_task_list()) == 1


def test_server_budget_counts_images_from_different_households(households, limits, monkeypatch):
    client, db, users, login = households
    _configure_image_queue(monkeypatch, db)
    limits["import_daily_limit"] = 5
    limits["import_server_daily_limit"] = 1
    for username, status in (("anna", 202), ("bert", 429)):
        rid = _recipe(db, username, f"https://recipes.example/{username}/image", owner=users[username][1])
        login(username)
        response = client.post(f"/api/recipes/{rid}/generate-image", json={})
        assert response.status_code == status, response.text


@pytest.mark.parametrize("kind", ["image", "url", "share"])
def test_finishing_a_replayed_task_cannot_open_a_free_new_job(households, limits, monkeypatch, kind):
    client, db, users, login = households
    from app.jobs import task_queue
    _configure_image_queue(monkeypatch, db)
    limits["import_daily_limit"] = 1
    login("anna")
    first_task = []

    def finish_before_enqueue(task_kind, payload, **values):
        if first_task:
            db.background_task_finish(first_task[0], ok=True, result={})
        return db.background_task_enqueue(task_kind, payload, **values)

    if kind == "image":
        rid = _recipe(db, "FinishedReplay", "https://recipes.example/finished-replay", owner=users["anna"][1])
        path, payload = f"/api/recipes/{rid}/generate-image", {}
        monkeypatch.setattr(task_queue, "enqueue", finish_before_enqueue)
    elif kind == "url":
        path, payload = "/api/pending/import-url", {"url": "https://recipes.example/finished-replay"}
        monkeypatch.setattr(api_pending, "enqueue", finish_before_enqueue)
    else:
        import hashlib
        from app.routes import api_share
        secret = "synthetic-quota-share-token-" * 3
        db.share_intake_token_create("quota-device", hashlib.sha256(secret.encode()).hexdigest(), "Synthetic", "operator")
        original_get = get_config().get
        monkeypatch.setattr(get_config(), "get", lambda *keys, default=None:
                            True if keys == ("web", "share_enabled") else original_get(*keys, default=default))
        login("operator")
        path, payload = "/api/share", {"url": "https://recipes.example/finished-replay", "token": secret}
        monkeypatch.setattr(api_share, "enqueue", finish_before_enqueue)
    first = client.post(path, json=payload)
    assert first.status_code in (200, 202), first.text
    first_task.append(first.json()["task_id"])
    replay = client.post(path, json=payload)
    assert replay.status_code == 429, replay.text
    assert len(db.background_task_list()) == 1


def test_image_queue_and_budget_are_atomic_across_households(households, limits):
    _, db, users, _ = households
    limits["import_daily_limit"] = 5
    limits["import_server_daily_limit"] = 1
    recipes = {name: _recipe(db, name, f"https://recipes.example/atomic-{name}", owner=users[name][1])
               for name in ("anna", "bert")}

    def enqueue(name):
        scoped = HouseholdDatabase(db, HouseholdScope(users[name][1]))
        try:
            scoped.background_task_enqueue("recipe_image_generate", {"recipe_id": recipes[name], "batch_id": name},
                                           dedupe_key=str(recipes[name]), reserve_budget=True)
            return 202
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(enqueue, ["anna", "bert"])) == [202, 429]
    assert len(db.background_task_list()) == 1
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 1
