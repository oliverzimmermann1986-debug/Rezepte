"""Tenant boundaries exercised through real sessions, including guessed IDs."""
from pathlib import Path
import time

import pytest
from itsdangerous import URLSafeTimedSerializer

from app import accounts, auth
from app.config_store import get_config
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, household_context


def test_thumbnail_revalidation_remains_authenticated_and_household_scoped(households):
    from PIL import Image
    client, db, users, login = households
    rid = _recipe(db, "PrivateThumb", "https://recipes.example/private-thumb", owner=users["anna"][1])
    recipe = db.recipe_get(rid)
    Image.new("RGB", (8, 8), "orange").save(Path(recipe["folder_path"]) / "thumb.jpg")
    with db.conn() as c:
        c.execute("UPDATE recipes SET thumb_filename='thumb.jpg' WHERE id=?", (rid,))
    login("anna")
    response = client.get(f"/api/recipes/{rid}/thumb")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert {"cookie", "authorization"} <= {value.strip().lower() for value in response.headers["vary"].split(",")}
    etag = response.headers["etag"]
    cached = client.get(f"/api/recipes/{rid}/thumb", headers={"If-None-Match": etag})
    assert cached.status_code == 304 and not cached.content
    assert cached.headers["cache-control"] == "private, max-age=0, must-revalidate"
    login("bert")
    denied = client.get(f"/api/recipes/{rid}/thumb", headers={"If-None-Match": etag})
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "private, no-store"
    client.headers.pop("Authorization")
    denied = client.get(f"/api/recipes/{rid}/thumb", headers={"If-None-Match": etag})
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "private, no-store"


@pytest.fixture
def households(client, test_db, monkeypatch):
    from app.main import app
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("tenant-test-key-" * 4))
    old = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    users = {}
    for username, role in (("operator", "admin"), ("anna", "user"), ("bert", "user")):
        uid = test_db.user_create(username, "unused-password-hash", role=role)
        aid = accounts.view(test_db, uid)["id"]
        users[username] = (uid, aid)
    def login(username):
        client.headers["Authorization"] = "Bearer " + (auth.create_guest_session() if username == "guest" else auth.create_session(username))
        return client
    yield client, test_db, users, login
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def _recipe(db, name, url, *, owner=None):
    root = Path(get_config().get("paths", "recipe_dir"))
    folder = root / (".households" if owner else "Hauptgericht") / (str(owner) if owner else "Test") / name
    folder.mkdir(parents=True, exist_ok=True)
    if owner:
        scoped = HouseholdDatabase(db, HouseholdScope(owner), import_owner=owner)
    else:
        scoped = db
    rid = scoped.recipe_upsert(url=url, name=name, type="Hauptgericht", category=name,
                              folder_path=str(folder), description=f"Rezept für {name}", thumb_filename=None,
                              video_filename=None, source_added_at=time.time())
    db.recipe_set_extraction_result(rid, ingredients=[{"name": "Hafer", "canonical_name": "hafer", "amount": 100, "unit": "g"}], status="ok")
    db.recipe_steps_set(rid, [{"instruction": "Alles vermischen."}])
    db.recipe_set_servings(rid, 2)
    return rid


def test_global_url_import_links_without_queue_or_download(households, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "Gemeinsam", "https://recipes.example/soup?utm_source=old")
    from app.routes import api_pending
    monkeypatch.setattr(api_pending, "enqueue", lambda *a, **k: pytest.fail("Global recipe must not be queued"))
    login("anna")
    for _ in range(2):
        response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/soup?utm_source=new#recipe", "visibility": "private"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "linked_global"
        assert response.json()["recipe_id"] == rid and response.json()["downloaded"] is False
    assert db.recipe_count() == 1
    assert len(client.get("/api/recipes?library=mine").json()["items"]) == 1
    login("bert")
    assert client.get("/api/recipes?library=mine").json()["items"] == []
    assert client.get("/api/recipes?library=global").json()["total"] == 1


def test_private_content_details_media_filters_and_edits_are_isolated(households):
    client, db, users, login = households
    rid = _recipe(db, "NurAnna", "https://recipes.example/private", owner=users["anna"][1])
    login("anna")
    assert client.get(f"/api/recipes/{rid}").json()["visibility"] == "private"
    assert client.put(f"/api/recipes/{rid}/steps", json={"steps": [{"instruction": "Privat geändert."}]}).status_code == 200
    for name in ("bert", "guest", "operator"):
        login(name)
        assert client.get("/api/recipes").json()["total"] == 0
        for suffix in ("", "/thumb", "/pdf", "/video", "/cook-history", "/cooking-progress"):
            assert client.get(f"/api/recipes/{rid}{suffix}").status_code == 404
        assert client.get("/api/recipes/facets").json()["categories"] == []
        assert client.get("/api/recipes/count?search=NurAnna").json()["total"] == 0
        if name != "guest":
            assert client.put(f"/api/recipes/{rid}/steps", json={"steps": []}).status_code == 404


def test_print_pdf_is_scoped_like_api_and_does_not_render_foreign_recipes(households, monkeypatch):
    client, db, users, login = households
    from app.routes import sharing
    private_id = _recipe(db, "PrivatePrint", "https://recipes.example/private-print", owner=users["anna"][1])
    global_id = _recipe(db, "GlobalPrint", "https://recipes.example/global-print")
    rendered = []

    def render(recipe):
        rendered.append(recipe["id"])
        return b"%PDF-1.4 synthetic regression fixture"

    monkeypatch.setattr(sharing, "build_recipe_pdf", render)
    for name in ("bert", "guest", "operator"):
        login(name)
        response = client.get(f"/recipe/{private_id}/pdf")
        assert response.status_code == 404
        assert "PrivatePrint" not in response.text
        assert response.headers["cache-control"] == "private, no-store"
    assert rendered == []
    for name, recipe_id in (("anna", private_id), ("guest", global_id)):
        login(name)
        response = client.get(f"/recipe/{recipe_id}/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["cache-control"] == "private, no-store"
        assert {"cookie", "authorization"} <= {value.strip().lower() for value in response.headers["vary"].split(",")}
    assert rendered == [private_id, global_id]


def test_global_recipe_share_management_is_private_to_household(households, monkeypatch):
    client, db, users, login = households
    from app.routes import sharing
    monkeypatch.setattr(sharing, "_serializer", lambda: URLSafeTimedSerializer("share-test-key-" * 4, salt=sharing.SHARE_SALT))
    rid = _recipe(db, "SharedGlobal", "https://recipes.example/share-global")
    login("anna")
    anna = client.post(f"/api/recipes/{rid}/share", json={}).json()["share_id"]
    login("bert")
    bert = client.post(f"/api/recipes/{rid}/share", json={}).json()["share_id"]
    assert [item["id"] for item in client.get(f"/api/recipes/{rid}/shares").json()["items"]] == [bert]
    assert client.delete(f"/api/recipes/{rid}/shares/{anna}").status_code == 404
    assert next(row for row in db.recipe_share_links_list(rid) if row["id"] == anna)["active"] == 1
    for name in ("guest", "operator"):
        login(name)
        assert client.get(f"/api/recipes/{rid}/shares").json()["items"] == []
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    login("bert")
    assert {item["id"] for item in client.get(f"/api/recipes/{rid}/shares").json()["items"]} == {anna, bert}
    assert client.delete(f"/api/recipes/{rid}/shares/{anna}").status_code == 200


def test_job_event_stream_requires_admin_and_keeps_regular_and_guest_data_private(households, monkeypatch):
    client, db, users, login = households
    from app.routes import api_events

    async def stream(_request):
        yield b"event: status\ndata: {}\n\n"

    monkeypatch.setattr(api_events, "_stream", stream)
    for name in ("anna", "bert", "guest"):
        login(name)
        response = client.get("/api/events")
        assert response.status_code == 403
        assert "text/event-stream" not in response.headers["content-type"]
    login("operator")
    assert client.get("/api/events").status_code == 200


def test_failed_browser_login_does_not_log_entered_username_or_ip(households, monkeypatch, caplog):
    client, db, users, login = households
    from app import main
    from app.security import LoginRateLimiter
    monkeypatch.setattr(main, "check_credentials", lambda *_args: False)
    monkeypatch.setattr(main, "login_limiter", LoginRateLimiter())
    monkeypatch.setattr(main, "client_ip", lambda _request: "192.0.2.123")
    response = client.post("/login", data={"username": "accidentally-pasted-private-value", "password": "test-value"},
                           headers={"Origin": str(client.base_url).rstrip("/")})
    assert response.status_code == 401
    assert "accidentally-pasted-private-value" not in caplog.text
    assert "192.0.2.123" not in caplog.text


def test_global_content_is_read_only_for_members_but_personal_state_is_scoped(households):
    client, db, users, login = households
    rid = _recipe(db, "Global", "https://recipes.example/global")
    login("anna")
    assert client.put(f"/api/recipes/{rid}/steps", json={"steps": []}).status_code == 403
    assert client.post(f"/api/recipes/{rid}/favorite").json()["is_favorite"] is True
    assert client.post(f"/api/recipes/{rid}/rating?value=5").status_code == 200
    assert client.get("/api/recipes/count?favorite_only=true").json()["total"] == 1
    for name in ("bert", "guest"):
        login(name)
        detail = client.get(f"/api/recipes/{rid}").json()
        assert detail["rating"] == 0 and detail["is_favorite"] is False
        assert client.get("/api/recipes/count?favorite_only=true").json()["total"] == 0
    assert db.recipe_get(rid)["rating"] == 0


def test_cart_plan_recurring_guessed_ids_cannot_cross_households(households):
    client, db, users, login = households
    rid = _recipe(db, "Global", "https://recipes.example/global")
    login("anna")
    added = client.post("/api/cart/add", json={"name": "Nur Annas Apfel", "amount": 2, "unit": "Stück"})
    assert added.status_code == 200, added.text
    item = client.get("/api/cart").json()["items"][0]
    planned = client.post("/api/meal-plan/items", json={"recipe_id": rid, "planned_for": "2026-10-05", "planned_servings": 2})
    assert planned.status_code == 200, planned.text
    plan_id = planned.json()["item"]["id"]
    assert client.post("/api/cart/recurring", json={"name": "Annas Regel", "amount": 1, "unit": "Stück", "interval_days": 7, "next_due_on": "2099-01-01"}).status_code == 200
    rule = client.get("/api/cart/recurring").json()["items"][0]
    login("bert")
    assert client.get("/api/cart").json()["items"] == []
    assert client.get("/api/cart/recurring").json()["items"] == []
    assert client.get("/api/meal-plan?week_start=2026-10-05").json()["days"][0]["items"] == []
    assert client.patch(f"/api/meal-plan/items/{plan_id}", json={"planned_servings": 6}).status_code == 404
    assert client.delete(f"/api/cart/recurring/{rule['id']}").status_code == 404
    client.patch(f"/api/cart/{item['id']}", json={"checked": True})
    client.delete(f"/api/cart/{item['id']}")
    client.post("/api/cart/clear", json={})
    login("anna")
    assert client.get("/api/cart").json()["items"][0]["checked"] is False
    assert client.get("/api/meal-plan?week_start=2026-10-05").json()["days"][0]["items"][0]["planned_servings"] == 2


def test_guest_cart_reads_do_not_materialize_any_recurring_rule(households):
    client, db, users, login = households
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    scoped.recurring_create(name="Privater Einkauf", canonical_name="geheim", amount=1, unit="Stück", category="Sonstiges",
                            interval_days=7, next_due_on="2020-01-01")
    login("guest")
    assert client.get("/api/cart").json()["items"] == []
    assert scoped.cart_list() == []
    assert scoped.recurring_list()[0]["next_due_on"] == "2020-01-01"


def test_private_import_jobs_and_pending_status_are_separate(households, monkeypatch):
    client, db, users, login = households
    from app.routes import api_pending
    queued = []
    monkeypatch.setattr(api_pending, "enqueue", lambda kind, payload, **kwargs: queued.append((payload, kwargs)) or 42)
    for name in ("anna", "bert"):
        login(name)
        result = client.post("/api/pending/import-url", json={"url": "https://recipes.example/new"})
        assert result.status_code == 200, result.text
        assert queued[-1][0]["account_id"] == users[name][1]
        assert len(client.get("/api/account/imports").json()["items"]) == 1
        assert client.post("/api/pending/import-url", json={"url": "https://recipes.example/new", "visibility": "global"}).status_code == 403
    assert queued[0][1]["dedupe_key"] != queued[1][1]["dedupe_key"]
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 2


def test_queued_private_import_reuses_global_added_after_enqueue(households, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "ZwischenzeitlichGlobal", "https://recipes.example/late")
    from app.routes import api_share
    from app import tenancy
    monkeypatch.setattr(tenancy, "scoped_scraper", lambda *a: pytest.fail("Must not initialize/download existing global recipe"))
    result = api_share.run_share_ingest_task({"url": "https://recipes.example/late", "type": "recipe", "account_id": users["anna"][1]})
    assert result["status"] == "linked_global" and result["recipe_id"] == rid
    login("anna")
    assert client.get("/api/recipes?library=mine").json()["total"] == 1


def test_invited_existing_member_keeps_private_recipes_cart_plan_and_state(households):
    client, db, users, login = households
    global_id = _recipe(db, "Global", "https://recipes.example/global")
    private_id = _recipe(db, "BertsRezept", "https://recipes.example/private", owner=users["bert"][1])
    bert = HouseholdDatabase(db, HouseholdScope(users["bert"][1]))
    bert.cart_add_or_merge(name="Hafer", canonical_name="hafer", amount=200, unit="g", source_recipe_id=private_id)
    bert.meal_plan_add(planned_for="2026-10-05", recipe_id=private_id, planned_servings=2)
    bert.save_recipe(global_id, field="is_favorite", value=1)
    invite = accounts.invite(db, users["anna"][0])
    login("bert")
    response = client.post("/api/account/invitations/accept", json={"token": invite["token"]})
    assert response.status_code == 200, response.text
    assert client.get("/api/account").json()["id"] == users["anna"][1]
    for name in ("anna", "bert"):
        login(name)
        assert client.get(f"/api/recipes/{private_id}").status_code == 200
        assert len(client.get("/api/cart").json()["items"]) == 1
        assert client.get("/api/recipes?favorite_only=true").json()["total"] == 1
        assert len(client.get("/api/meal-plan?week_start=2026-10-05").json()["days"][0]["items"]) == 1
    with db.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_household_context_does_not_escape_after_request(households):
    client, db, users, login = households
    login("anna")
    assert client.get("/api/account").status_code == 200
    from app.tenancy import CURRENT_HOUSEHOLD
    assert CURRENT_HOUSEHOLD.get() is None
    from app.db import get_db
    with household_context(HouseholdScope(users["anna"][1])):
        assert get_db().account_id == users["anna"][1]
    assert get_db() is db


def test_legacy_data_is_claimed_once_by_operator_not_first_signup(households):
    client, db, users, login = households
    db.cart_add_or_merge(name="Altbestand", canonical_name="altbestand", amount=1, unit="Stück", source_recipe_id=None)
    login("anna")
    assert client.get("/api/cart").json()["items"] == []

    login("operator")
    assert client.get("/api/cart").json()["items"][0]["name"] == "Altbestand"
    login("bert")
    assert client.get("/api/cart").json()["items"] == []


def test_global_shortlink_alias_reuses_recipe_without_enqueue(households, monkeypatch):
    client, db, users, login = households
    rid = _recipe(db, "MitAlias", "https://recipes.example/canonical")
    db.history_add("https://recipes.example/short", name="MitAlias", target_dir=db.recipe_get(rid)["folder_path"], content_type="recipe")
    from app.routes import api_pending
    monkeypatch.setattr(api_pending, "enqueue", lambda *a, **k: pytest.fail("Alias must reuse global recipe"))
    login("anna")
    response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/short"})
    assert response.status_code == 200 and response.json()["recipe_id"] == rid
    assert response.json()["downloaded"] is False
    login("operator")
    response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/short", "visibility": "global"})
    assert response.status_code == 200 and response.json()["status"] == "duplicate"


def test_private_file_upload_retries_are_scoped_to_household(households, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from app.routes import api_pending
    client, db, users, login = households
    root = Path(get_config().get("paths", "recipe_dir"))
    temp = Path(get_config().get("paths", "temp_dir"))
    processed = []

    class ImportJob:
        recipe_dir = root
        temp_dir = temp
        def process_attachment(self, attachment, url):
            processed.append((self.db.account_id, url, self.recipe_dir, self.temp_dir))
            self.db.pending_add(url=url, content_type="recipe", description=None, video_path=None,
                                frame_path=None, ai_suggestion={"name": "Privates Bild"})
            return {"status": "pending", "url": url}

    original = ImportJob()
    original.db = db
    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: original)
    buffer = BytesIO()
    Image.new("RGB", (24, 24), "blue").save(buffer, format="JPEG")
    for name in ("anna", "bert"):
        login(name)
        for repeat in range(2):
            response = client.post("/api/pending/import-file", data={"client_request_id": "same-mobile-upload", "visibility": "private"},
                                   files={"file": ("rezept.jpg", buffer.getvalue(), "image/jpeg")})
            assert response.status_code == 200, response.text
            assert response.json()["idempotent_replay"] is bool(repeat)
    assert len(processed) == 2 and processed[0][1] != processed[1][1]
    assert {item[0] for item in processed} == {users["anna"][1], users["bert"][1]}
    for aid, _url, recipe_dir, temp_dir in processed:
        assert recipe_dir == root.resolve() / ".households" / str(aid)
        assert temp_dir == temp / "households" / str(aid)
    assert original.recipe_dir == root and original.temp_dir == temp and original.db is db


def test_member_can_approve_own_pending_but_cannot_read_or_approve_foreign_pending(households, monkeypatch):
    from app.routes import api_pending
    client, db, users, login = households
    own = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    url = "https://recipes.example/own-pending"
    own.pending_add(url=url, content_type="recipe", description="Privat", video_path=None,
                    frame_path=None, ai_suggestion={"name": "Privat"})
    calls = []

    class ImportJob:
        recipe_dir = Path(get_config().get("paths", "recipe_dir"))
        temp_dir = Path(get_config().get("paths", "temp_dir"))
        def resolve_pending(self, source, decision):
            calls.append(self.db.account_id)
            rid = self.db.recipe_upsert(url=source, name=decision["name"], type="Hauptgericht", category="Privat",
                                        folder_path=str(self.recipe_dir / "MeinRezept"), description="Privat", thumb_filename=None,
                                        video_filename=None, source_added_at=time.time())
            self.db.pending_resolve(source)
            return {"ok": True, "recipe_id": rid}
    monkeypatch.setattr(api_pending, "get_scraper_job", ImportJob)
    login("bert")
    assert client.get("/api/pending").json() == []
    assert client.get("/api/pending/file", params={"url": url}).status_code == 404
    assert client.post("/api/pending", json={"url": url, "action": "save", "name": "Fremd"}).status_code == 404
    assert client.post("/api/pending/reanalyze", json={"url": url}).status_code == 404
    assert calls == []
    login("anna")
    response = client.post("/api/pending", json={"url": url, "visibility": "private", "action": "save", "name": "MeinRezept"})
    assert response.status_code == 200, response.text
    rid = response.json()["recipe_id"]
    assert db.recipe_get(rid)["owner_account_id"] == users["anna"][1]
    assert db.recipe_count() == 1 and calls == [users["anna"][1]]
    login("guest")
    assert client.get(f"/api/recipes/{rid}").status_code == 404


def test_global_and_private_pending_for_same_url_resolve_in_correct_scope(households, monkeypatch):
    client, db, users, login = households
    from app.routes import api_pending
    url = "https://recipes.example/two-pending"
    own = HouseholdDatabase(db, HouseholdScope(users["operator"][1], is_admin=True))
    own.pending_add(url=url, content_type="recipe", description=None, video_path=None, frame_path=None, ai_suggestion={"name": "Privat"})
    db.pending_add(url=url, content_type="recipe", description=None, video_path=None, frame_path=None, ai_suggestion={"name": "Global"})

    class ImportJob:
        recipe_dir = Path(get_config().get("paths", "recipe_dir"))
        temp_dir = Path(get_config().get("paths", "temp_dir"))
        def resolve_pending(self, source, decision):
            entry = self.db.pending_get(source)
            self.db.pending_resolve(source)
            return {"ok": True, "name": entry["ai_suggestion"]["name"], "owner": self.db.import_owner}
    monkeypatch.setattr(api_pending, "get_scraper_job", ImportJob)
    login("operator")
    response = client.post("/api/pending", json={"url": url, "visibility": "global", "action": "skip"})
    assert response.status_code == 200 and response.json()["name"] == "Global"
    assert response.json()["owner"] is None
    assert own.pending_get(url)["status"] == "pending"
    response = client.post("/api/pending", json={"url": url, "visibility": "private", "action": "skip"})
    assert response.status_code == 200 and response.json()["name"] == "Privat"


def test_ai_hints_and_nutrition_claims_cannot_include_foreign_private_recipes(households):
    client, db, users, login = households
    from app.recipes.pdf_recipe_extract import existing_hints
    ids = [_recipe(db, name, f"https://recipes.example/hints-{name}", owner=owner)
           for name, owner in (("global", None), ("anna-secret", users["anna"][1]), ("bert-secret", users["bert"][1]))]
    for rid, tag in zip(ids, ("global-tag", "anna-secret-tag", "bert-secret-tag")):
        db.recipe_tags_set(rid, [tag])
        db.recipe_set_extraction_result(rid, ingredients=[{"name": f"{tag}-{i}", "canonical_name": f"{tag}-{i}"} for i in range(3)], status="ok")
    assert set(existing_hints(db)[0]) == {"global-tag"}
    anna = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    assert set(existing_hints(anna)[0]) == {"global-tag", "anna-secret-tag"}
    assert set(existing_hints(anna, ids[0])[0]) == {"global-tag"}
    assert set(anna.search_vocabulary()).isdisjoint({"bert", "bert-secret-tag", "bert-secret-tag-0"})
    admin = HouseholdDatabase(db, HouseholdScope(users["operator"][1], is_admin=True))
    assert admin.recipes_pending_nutrition_count() == 1
    assert [row["id"] for row in admin.recipes_claim_pending_nutrition(limit=10, owner="scoped-test")] == [ids[0]]
    for rid in ids[1:]:
        assert db.recipe_get(rid)["nutrition_claim_owner"] is None


def test_cooking_history_is_private_while_recipe_remains_global(households):
    client, db, users, login = households
    rid = _recipe(db, "GlobalKochen", "https://recipes.example/cooking")
    login("anna")
    result = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 3})
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["count"] == 1
    for name in ("bert", "guest"):
        login(name)
        result = client.get(f"/api/recipes/{rid}/cook-history").json()
        assert result["items"] == [] and result["summary"]["count"] == 0


def test_optimizer_preview_cannot_be_applied_in_other_household(households):
    client, db, users, login = households
    from app.routes import api_shopping
    from app.recipes.shopping_optimizer import cart_fingerprint
    anna = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    anna.cart_add_or_merge(name="Milch", canonical_name="milch", amount=None, unit=None, source_recipe_id=None)
    anna.cart_update(anna.cart_list()[0]["id"], checked=True)
    items = anna.cart_list()
    with household_context(anna.scope):
        token = api_shopping._store_optimize_preview({"fingerprint": cart_fingerprint(items), "items": items})
    login("bert")
    assert client.post("/api/cart/optimize/apply", json={"preview_id": token}).status_code == 410
    assert token in api_shopping._optimize_previews
    login("anna")
    assert client.post("/api/cart/optimize/apply", json={"preview_id": token}).status_code == 200
    replaced = anna.cart_list()[0]
    for key in ("name", "checked", "amount", "unit", "added_at", "source_recipe_ids", "category", "sort_order"):
        assert replaced[key] == items[0][key]


def test_global_scraper_singleton_cannot_capture_request_household(households, monkeypatch):
    client, db, users, login = households
    from app.jobs import scraper
    from app.db import get_db
    from app.tenancy import scoped_scraper
    class ImportJob:
        recipe_dir = Path(get_config().get("paths", "recipe_dir"))
        temp_dir = Path(get_config().get("paths", "temp_dir"))
        def __init__(self):
            self.db = get_db()
    monkeypatch.setattr(scraper, "ScraperJob", ImportJob)
    monkeypatch.setattr(scraper, "_job_instance", None)
    scope = HouseholdScope(users["anna"][1])
    with household_context(scope):
        original = scraper.get_scraper_job()
        private = scoped_scraper(HouseholdDatabase(db, scope))
    assert original.db.account_id == 0 and original.db.import_owner is None
    assert private.db.account_id == users["anna"][1]
    assert private.recipe_dir != original.recipe_dir and private.temp_dir != original.temp_dir


def test_audit_findings_and_folder_preview_cannot_expose_foreign_household(households):
    import json
    client, db, users, login = households
    rid = _recipe(db, "PrivaterPrüffall", "https://recipes.example/audit-private", owner=users["anna"][1])
    folder = Path(db.recipe_get(rid)["folder_path"])
    (folder / "info.json").write_text(json.dumps({"name": "PrivaterPrüffall"}), encoding="utf-8")
    db.audit_ai_finding_set(rid, "name_mismatch", "PrivaterPrüffall", "GeheimerName", "Privat")
    with db.conn() as c:
        finding = c.execute("SELECT id FROM audit_ai_findings WHERE recipe_id=?", (rid,)).fetchone()[0]
    login("operator")
    response = client.get("/api/audit?refresh=true")
    assert response.status_code == 200, response.text
    assert "PrivaterPrüffall" not in response.text and "GeheimerName" not in response.text
    assert client.get("/api/audit/ai-sanity/findings").json()["items"] == []
    assert client.post(f"/api/audit/finding/{finding}/resolve").status_code == 404
    assert client.post(f"/api/audit/finding/{finding}/apply").status_code == 404
    assert client.get("/api/audit/folder-preview", params={"path": str(folder)}).status_code == 404
    with db.conn() as c:
        assert c.execute("SELECT resolved FROM audit_ai_findings WHERE id=?", (finding,)).fetchone()[0] == 0


def test_global_pdf_scan_and_filesystem_sync_skip_private_trees(households, monkeypatch):
    client, db, users, login = households
    from app.core.pdf_processing import find_recipe_pdfs
    from app.recipes import indexer
    private = _recipe(db, "PrivatesPDF", "https://recipes.example/private-pdf", owner=users["anna"][1])
    global_id = _recipe(db, "GlobalesPDF", "https://recipes.example/global-pdf")
    for rid in (private, global_id):
        folder = Path(db.recipe_get(rid)["folder_path"])
        (folder / "rezept.pdf").write_bytes(b"%PDF-1.4\n")
        (folder / "info.json").write_text('{"name":"Rezept"}', encoding="utf-8")
    root = Path(get_config().get("paths", "recipe_dir"))
    assert all(".households" not in path.relative_to(root).parts for path in find_recipe_pdfs(root))
    assert Path(db.recipe_get(private)["folder_path"]) / "rezept.pdf" not in find_recipe_pdfs(root)
    from tests.conftest import REAL_SYNC_FILESYSTEM
    visited = []
    def record_folder(_db, folder, _type, _category):
        visited.append(folder)
        return "skipped"
    monkeypatch.setattr(indexer, "_index_one", record_folder)
    REAL_SYNC_FILESYSTEM(db)
    assert Path(db.recipe_get(global_id)["folder_path"]) in visited
    assert Path(db.recipe_get(private)["folder_path"]) not in visited
    assert all(".households" not in folder.relative_to(root).parts for folder in visited)


def test_current_server_source_and_variant_routes_keep_household_boundaries(households, monkeypatch):
    client, db, users, login = households
    from app.routes import api_recipes
    from app.recipes.manage import safe_duplicate_recipe
    rid = _recipe(db, "AnnasQuelle", "https://recipes.example/anna-source", owner=users["anna"][1])
    monkeypatch.setattr(api_recipes, "extract_recipe_web_metadata", lambda *_args, **_kwargs: {
        "description_text": "Aktuelle Quellenbeschreibung", "page_title": "Annas Quelle",
    })
    login("anna")
    assert client.post(f"/api/recipes/{rid}/source-integrity/check").status_code == 200
    assert client.get(f"/api/recipes/{rid}/source-integrity").status_code == 200
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    variant = safe_duplicate_recipe(scoped, rid, new_name="AnnasVariante")
    variant_id = variant["recipe_id"]
    assert scoped.recipe_get(variant_id)["owner_account_id"] == users["anna"][1]
    assert ".households" in Path(variant["folder_path"]).parts
    with db.conn() as c:
        c.execute("UPDATE recipes SET ingredients_status='variant_pending' WHERE id=?", (variant_id,))
    assert scoped.recipe_get(variant_id) is None
    assert scoped.recipe_get(variant_id, include_pending=True) is not None
    for username in ("bert", "operator", "guest"):
        login(username)
        for suffix in ("/source-integrity", "/substitutions"):
            assert client.get(f"/api/recipes/{rid}{suffix}").status_code == 404
        if username != "guest":
            assert client.post(f"/api/recipes/{rid}/source-integrity/check").status_code == 404
    assert len(db.recipe_source_snapshot_state(rid, "https://recipes.example/anna-source")["latest"]) > 0


def test_current_server_catalog_and_bulk_allergen_work_stay_scoped(households):
    client, db, users, login = households
    global_id = _recipe(db, "GlobalerReis", "https://recipes.example/catalog-global")
    private_id = _recipe(db, "PrivaterReis", "https://recipes.example/catalog-private", owner=users["anna"][1])
    db.recipe_set_extraction_result(global_id, "ok", [{"name": "Basmatireis", "canonical_name": "basmatireis"}])
    db.recipe_set_extraction_result(private_id, "ok", [{"name": "Annas Privatgemüse", "canonical_name": "annas privatgemüse"}])
    anna = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    bert = HouseholdDatabase(db, HouseholdScope(users["bert"][1]))
    anna.shopping_catalog_rebuild()
    bert.shopping_catalog_rebuild()
    assert "Annas Privatgemüse" in {item["name"] for item in anna.shopping_product_suggestions("", 25)}
    assert "Annas Privatgemüse" not in {item["name"] for item in bert.shopping_product_suggestions("", 25)}
    assert "Annas Privatgemüse" not in set(bert.ingredient_name_hints())
    assert bert.shopping_product_suggestions("ei") == []
    from app.recipes.auto_tags import backfill_diet_auto_tags
    db.recipe_auto_tags_set(private_id, ["anna-secret-tag", "vegan"])
    before = db.recipe_tags_get(private_id)
    result = backfill_diet_auto_tags(HouseholdDatabase(db, HouseholdScope(users["operator"][1], is_admin=True)))
    assert result["recipes_checked"] == 1
    assert db.recipe_tags_get(private_id) == before


@pytest.mark.parametrize("legacy_version", [231, 232, 260])
def test_upgrade_keeps_legacy_rows_ids_and_creates_backup(tmp_path, monkeypatch, legacy_version):
    import sqlite3
    from app import tenancy
    from app.db import Database
    path = tmp_path / "legacy-household.db"
    current_migrate = Database._migrate

    def build_legacy_fixture(connection):
        # Diese Fixture lässt die Haushaltsmigration bewusst aus. Folgemigration
        # 265 benötigt deren source_url-Spalte und darf hier noch nicht laufen.
        connection.execute("INSERT INTO schema_migrations(version,name,applied_at) "
                           "VALUES(265,'synthetic-fixture-skip',0)")
        current_migrate(connection)

    with monkeypatch.context() as patch:
        patch.setattr(tenancy, "migrate_schema", lambda _connection: None)
        patch.setattr(tenancy, "migrate_share_ownership", lambda _connection: None)
        patch.setattr(Database, "_migrate", staticmethod(build_legacy_fixture))
        legacy = Database(path)
    uid = legacy.user_create("AltAdmin", "unused", role="admin")
    with legacy.conn() as c:
        assert "owner_account_id" not in {row[1] for row in c.execute("PRAGMA table_info(recipes)")}
        c.execute("INSERT INTO recipes(id,url,name,folder_path,indexed_at,is_favorite,rating) VALUES(44,?,?,?,?,1,4)",
                  ("https://recipes.example/old?utm_source=legacy", "AltRezept", str(tmp_path / "old"), 1.0))
        c.execute("INSERT INTO shopping_cart(id,name,canonical_name,amount,unit,checked,added_at) VALUES(65,'AltMilch','milch',2,'l',1,1.0)")
        c.execute("INSERT INTO meal_plan_entries(id,planned_for,recipe_id,planned_servings,created_at,updated_at) VALUES(73,'2026-10-05',44,3,1,1)")
        c.execute("INSERT INTO shopping_products(canonical_name,display_name,updated_at,usage_count) VALUES('milch','AltMilch',1,9)")
        c.execute("INSERT INTO shopping_exclusions(canonical_name,created_at) VALUES('salz',1)")
        c.execute("DELETE FROM schema_migrations WHERE version>?", (legacy_version,))
        c.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(?, 'legacy-fixture', 0)", (legacy_version,))
    migrate = tenancy.migrate_schema
    factories = []

    def checked_migration(connection):
        assert connection.row_factory is None
        migrate(connection)
        assert connection.row_factory is None
        factories.append(connection.row_factory)

    with monkeypatch.context() as patch:
        patch.setattr(tenancy, "migrate_schema", checked_migration)
        upgraded = Database(path)
    assert factories == [None]
    aid = accounts.view(upgraded, uid)["id"]
    scoped = HouseholdDatabase(upgraded, HouseholdScope(aid, is_admin=True))
    assert scoped.cart_list()[0]["id"] == 65 and scoped.cart_list()[0]["checked"] == 1
    assert scoped.meal_plan_entries("2026-10-05", "2026-10-05")[0]["id"] == 73
    assert scoped.shopping_excluded_canonicals() == {"salz"}
    assert scoped.recipe_get(44)["rating"] == 4 and scoped.recipe_get(44)["is_favorite"]
    assert scoped.recipe_get(44)["url"] == "https://recipes.example/old"
    other = upgraded.user_create("ZweiterHaushalt", "unused")
    other_scope = HouseholdDatabase(upgraded, HouseholdScope(accounts.view(upgraded, other)["id"]))
    other_scope.meal_plan_add(planned_for="2026-10-05", recipe_id=44, planned_servings=2)
    assert scoped.meal_plan_entries("2026-10-05", "2026-10-05")[0]["planned_servings"] == 3
    from app.db import CURRENT_SCHEMA_VERSION
    backups = list((tmp_path / "backups").glob(f"pre-migration-v{legacy_version}-to-v{CURRENT_SCHEMA_VERSION}-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert "owner_account_id" not in {row[1] for row in c.execute("PRAGMA table_info(recipes)")}
        assert c.execute("SELECT name FROM shopping_cart WHERE id=65").fetchone()[0] == "AltMilch"
    Database(path)
    with upgraded.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_private_import_without_household_is_rejected_instead_of_published_globally(households, monkeypatch):
    client, db, users, login = households
    login("operator")
    monkeypatch.setattr(auth, "auth_disabled", lambda: True)
    from app import security
    monkeypatch.setattr(security, "request_is_from_trusted_proxy", lambda _request: True)
    response = client.post("/api/pending/import-url", json={"url": "https://recipes.example/must-remain-private", "visibility": "private"})
    assert response.status_code == 409
    assert db.pending_list() == [] and db.recipe_count() == 0


def test_deleting_household_owner_preserves_partner_access_and_data(households):
    client, db, users, login = households
    rid = _recipe(db, "BleibtBeimPartner", "https://recipes.example/partner", owner=users["anna"][1])
    aid = users["anna"][1]
    anna = HouseholdDatabase(db, HouseholdScope(aid))
    anna.cart_add_or_merge(name="BleibtImEinkauf", canonical_name="partner", amount=1, unit="Stück", source_recipe_id=rid)
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    assert db.user_delete(users["anna"][0])
    login("bert")
    account = client.get("/api/account").json()
    assert account["id"] == aid and account["is_owner"] is True
    assert client.get(f"/api/recipes/{rid}").status_code == 200
    assert client.get("/api/cart").json()["items"][0]["name"] == "BleibtImEinkauf"
    with db.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
