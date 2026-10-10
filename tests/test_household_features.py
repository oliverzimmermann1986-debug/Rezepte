"""Real-session household boundaries, retries, image normalization and merges."""
from __future__ import annotations

import io
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from itsdangerous import URLSafeTimedSerializer
from fastapi import HTTPException
from PIL import Image

from app import accounts, auth
from app.recipes import household_features as features
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, merge_households


@pytest.fixture
def household_api(client, test_db, monkeypatch):
    from app.main import app
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("household-feature-tests-" * 3))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    users = {}
    for name, role in (("anna", "user"), ("bert", "user"), ("cara", "user"), ("admin", "admin")):
        uid = test_db.user_create(name, "unused-password", role=role)
        users[name] = (uid, accounts.view(test_db, uid)["id"])
    # Cara and the operator share Anna's household; Bert stays separate.
    with test_db.conn() as c:
        for name in ("cara", "admin"):
            c.execute("UPDATE account_members SET account_id=? WHERE user_id=?", (users["anna"][1], users[name][0]))
            c.execute("DELETE FROM user_accounts WHERE id=?", (users[name][1],))
            users[name] = (users[name][0], users["anna"][1])

    def login(name):
        client.headers["Authorization"] = "Bearer " + (auth.create_guest_session() if name == "guest" else auth.create_session(name))
        return client

    yield client, test_db, users, login
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def recipe(db, name="Pasta", owner=None):
    rid = db.recipe_upsert(url=f"https://household.example/{name}/{owner}", name=name, type="Hauptgericht", category="Tests",
                           folder_path=f"/household-tests/{name}-{owner}", description="Testgericht", thumb_filename=None,
                           video_filename=None, source_added_at=time.time(), owner_account_id=owner)
    db.recipe_set_extraction_result(rid, status="ok", ingredients=[{"name": "Nudeln", "amount": 100, "unit": "g"}])
    db.recipe_steps_set(rid, [{"instruction": "Kochen."}])
    return rid


def history(db, household, rid, username="anna"):
    with db.conn() as c:
        return c.execute("INSERT INTO recipe_cook_history(account_id,recipe_id,cooked_at,cooked_by,servings,cooked_by_user_id) VALUES(?,?,?,?,?,?)",
                         (household, rid, time.time(), username, 2, db._cooking_user_id(c, username))).lastrowid


def png():
    out = io.BytesIO()
    Image.new("RGBA", (80, 60), (20, 120, 60, 255)).save(out, format="PNG")
    return out.getvalue()


def test_cookbooks_roundtrip_casefold_retry_and_many_to_many(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    login("anna")
    first = client.post("/api/cookbooks", json={"name": "  Feierabend  "}).json()["item"]
    assert first == {"id": first["id"], "name": "Feierabend", "recipe_count": 0, "contains_recipe": False}
    assert client.post("/api/cookbooks", json={"name": "FEIERABEND"}).json()["item"]["id"] == first["id"]
    second = client.post("/api/cookbooks", json={"name": "Für Gäste"}).json()["item"]
    for bid in (first["id"], second["id"]):
        for _ in range(2):
            result = client.put(f"/api/cookbooks/{bid}/recipes/{rid}")
            assert result.status_code == 200, result.text
            assert result.json()["item"]["recipe_count"] == 1
        listed = client.get(f"/api/cookbooks/{bid}/recipes").json()["items"]
        assert listed[0]["id"] == rid and listed[0]["ingredients_count"] == listed[0]["steps_count"] == 1
        assert listed[0]["is_favorite"] is False and listed[0]["needs_manual_care"] is False
    assert all(book["contains_recipe"] for book in client.get(f"/api/cookbooks?recipe_id={rid}").json()["items"])
    assert client.patch(f"/api/cookbooks/{first['id']}", json={"name": "für GÄSTE"}).status_code == 409
    assert client.patch(f"/api/cookbooks/{first['id']}", json={"name": "Schnell"}).status_code == 200
    for _ in range(2):
        assert client.delete(f"/api/cookbooks/{first['id']}/recipes/{rid}").status_code == 200
    assert client.get(f"/api/cookbooks/{second['id']}/recipes").json()["items"][0]["id"] == rid
    for _ in range(2):
        assert client.delete(f"/api/cookbooks/{first['id']}").status_code == 200
    assert len(client.get("/api/cookbooks").json()["items"]) == 1


@pytest.mark.parametrize("name", ["", " \n ", "x" * 81])
def test_cookbook_name_bounds(household_api, name):
    client, _, _, login = household_api
    login("anna")
    assert client.post("/api/cookbooks", json={"name": name}).status_code == 422


def test_cookbooks_cross_household_recipe_visibility_and_deleted_filter(household_api):
    client, db, users, login = household_api
    rid, secret = recipe(db), recipe(db, "Geheim", users["bert"][1])
    login("anna")
    book = client.post("/api/cookbooks", json={"name": "Meins"}).json()["item"]["id"]
    assert client.put(f"/api/cookbooks/{book}/recipes/{secret}").status_code == 404
    assert client.get(f"/api/cookbooks?recipe_id={secret}").status_code == 404
    client.put(f"/api/cookbooks/{book}/recipes/{rid}")
    login("bert")
    assert client.get("/api/cookbooks").json()["items"] == []
    assert client.get(f"/api/cookbooks/{book}/recipes").status_code == 404
    assert client.patch(f"/api/cookbooks/{book}", json={"name": "Gestohlen"}).status_code == 404
    assert client.put(f"/api/cookbooks/{book}/recipes/{rid}").status_code == 404
    assert client.delete(f"/api/cookbooks/{book}").status_code == 200  # Safe idempotent absent deletion.
    login("anna")
    assert len(client.get("/api/cookbooks").json()["items"]) == 1
    with db.conn() as c:
        c.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time(), rid))
    assert client.get("/api/cookbooks").json()["items"][0]["recipe_count"] == 0
    assert client.get(f"/api/cookbooks/{book}/recipes").json()["items"] == []
    assert client.put(f"/api/cookbooks/{book}/recipes/{rid}").status_code == 404


def test_notes_include_old_history_author_edit_and_photo_independence(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    hid = history(db, users["anna"][1], rid)
    history(db, users["anna"][1], rid, "cara")
    login("anna")
    items = client.get(f"/api/cook-notes?recipe_id={rid}").json()["items"]
    assert len(items) == 2 and [item["can_edit"] for item in items] == [False, True]
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Weniger Salz"}).status_code == 200
    upload = client.post(f"/api/cook-notes/{hid}/photo", files={"file": ("dish.png", png(), "image/png")})
    assert upload.status_code == 200, upload.text
    item = upload.json()["item"]
    assert item["note"] == "Weniger Salz" and item["photo_url"]
    photo = client.get(item["photo_url"])
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert "no-store" in photo.headers["cache-control"]
    with Image.open(io.BytesIO(photo.content)) as image:
        assert image.format == "JPEG" and image.size == (80, 60) and not image.getexif()
    updated = client.put(f"/api/cook-notes/{hid}", json={"note": "Länger backen"}).json()["item"]
    assert updated["photo_url"]
    login("cara")
    assert client.get(item["photo_url"]).status_code == 200
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Fremde Notiz"}).status_code == 403
    assert client.delete(f"/api/cook-notes/{hid}/photo").status_code == 403
    login("admin")
    removed = client.delete(f"/api/cook-notes/{hid}/photo").json()["item"]
    assert removed["note"] == "Länger backen" and removed["photo_url"] is None
    assert client.get(item["photo_url"]).status_code == 404


def test_note_validation_bad_images_and_foreign_history(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    hid = history(db, users["anna"][1], rid)
    login("anna")
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "x" * 4001}).status_code == 422
    assert client.post(f"/api/cook-notes/{hid}/photo", files={"file": ("evil.jpg", b"<script>bad</script>", "image/jpeg")}).status_code == 422
    assert client.post(f"/api/cook-notes/{hid}/photo", files={"file": ("dish.png", png(), "image/png")}).status_code == 200
    login("bert")
    assert client.get(f"/api/cook-notes?recipe_id={rid}").json()["items"] == []
    assert client.get(f"/api/cook-notes/{hid}/photo").status_code == 404
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Guess"}).status_code == 404
    assert client.delete(f"/api/cook-notes/{hid}/photo").status_code == 404


def test_photo_input_limits_and_normalization():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as oversized:
        features.normalize_photo(b"x" * (8 * 1024 * 1024 + 1))
    assert oversized.value.status_code == 413
    out = io.BytesIO()
    Image.new("RGB", (5000, 4001)).save(out, format="PNG")
    with pytest.raises(HTTPException) as pixels:
        features.normalize_photo(out.getvalue())
    assert pixels.value.status_code == 422
    out = io.BytesIO()
    Image.new("RGB", (3000, 1500)).save(out, format="JPEG")
    with Image.open(io.BytesIO(features.normalize_photo(out.getvalue()))) as image:
        assert image.size == (2048, 1024)


def test_wishes_idempotent_votes_server_identity_and_creator_permissions(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    login("anna")
    payload = {"week_start": "2026-10-10", "recipe_id": rid}
    first = client.post("/api/meal-wishes", json=payload).json()["item"]
    wid = first["id"]
    assert first["created_by"] == "anna" and first["votes"] == 0 and first["can_delete"] is True
    assert client.post("/api/meal-wishes", json=payload).json()["item"] == first
    assert client.post("/api/meal-wishes", json={**payload, "created_by": "admin"}).status_code == 422
    for _ in range(2):
        item = client.put(f"/api/meal-wishes/{wid}/vote", json={"voted": True}).json()["item"]
        assert item["votes"] == 1 and item["my_vote"] is True
    login("cara")
    assert client.post("/api/meal-wishes", json=payload).json()["item"]["created_by"] == "anna"
    item = client.put(f"/api/meal-wishes/{wid}/vote", json={"voted": True}).json()["item"]
    assert item["votes"] == 2 and item["can_delete"] is False
    assert client.delete(f"/api/meal-wishes/{wid}").status_code == 403
    for _ in range(2):
        item = client.put(f"/api/meal-wishes/{wid}/vote", json={"voted": False}).json()["item"]
        assert item["votes"] == 1 and item["my_vote"] is False
    listing = client.get("/api/meal-wishes?week_start=2026-10-11").json()
    assert listing["week_start"] == "2026-10-05" and len(listing["items"]) == 1
    login("admin")
    assert client.delete(f"/api/meal-wishes/{wid}").status_code == 200
    assert client.delete(f"/api/meal-wishes/{wid}").status_code == 200


def test_wish_plan_replay_preserves_unrelated_entries_and_rejects_changes(household_api):
    client, db, users, login = household_api
    rid, other = recipe(db), recipe(db, "Salat")
    login("anna")
    client.post("/api/meal-plan/items", json={"planned_for": "2026-10-06", "recipe_id": other, "planned_servings": 3})
    wid = client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": rid}).json()["item"]["id"]
    payload = {"planned_for": "2026-10-06", "planned_servings": 4}
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={**payload, "planned_for": "2026-10-12"}).status_code == 422
    for _ in range(2):
        result = client.post(f"/api/meal-wishes/{wid}/plan", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["item"]["planned_for"] == payload["planned_for"]
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={**payload, "planned_servings": 2}).status_code == 409
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={**payload, "planned_for": "2026-10-07"}).status_code == 409
    with db.conn() as c:
        entries = c.execute("SELECT recipe_id,planned_servings FROM meal_plan_entries WHERE account_id=? ORDER BY recipe_id",
                            (users["anna"][1],)).fetchall()
        assert [(r["recipe_id"], r["planned_servings"]) for r in entries] == [(rid, 4), (other, 3)]
        c.execute("UPDATE meal_plan_entries SET planned_servings=6 WHERE recipe_id=?", (rid,))
    # Lost-response retries cannot undo a later manual edit in the meal planner.
    assert client.post(f"/api/meal-wishes/{wid}/plan", json=payload).status_code == 200
    with db.conn() as c:
        assert c.execute("SELECT planned_servings FROM meal_plan_entries WHERE recipe_id=?", (rid,)).fetchone()[0] == 6
    client.delete(f"/api/meal-wishes/{wid}")
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 2


def test_foreign_wishes_and_guest_mutations_are_denied(household_api):
    client, db, users, login = household_api
    rid, secret = recipe(db), recipe(db, "Privat", users["anna"][1])
    hid = history(db, users["anna"][1], rid)
    login("anna")
    book = client.post("/api/cookbooks", json={"name": "Test"}).json()["item"]["id"]
    wid = client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": rid}).json()["item"]["id"]
    login("bert")
    assert client.get("/api/meal-wishes?week_start=2026-10-05").json()["items"] == []
    assert client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": secret}).status_code == 404
    assert client.put(f"/api/meal-wishes/{wid}/vote", json={"voted": True}).status_code == 404
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={"planned_for": "2026-10-06", "planned_servings": 2}).status_code == 404
    assert client.delete(f"/api/meal-wishes/{wid}").status_code == 200
    login("guest")
    assert client.get("/api/cookbooks").json()["items"] == []
    assert client.get(f"/api/cook-notes?recipe_id={rid}").json()["items"] == []
    assert client.get("/api/meal-wishes?week_start=2026-10-05").json()["items"] == []
    operations = [
        ("post", "/api/cookbooks", {"name": "Gast"}),
        ("patch", f"/api/cookbooks/{book}", {"name": "Gast"}),
        ("delete", f"/api/cookbooks/{book}", None),
        ("put", f"/api/cookbooks/{book}/recipes/{rid}", None),
        ("delete", f"/api/cookbooks/{book}/recipes/{rid}", None),
        ("put", f"/api/cook-notes/{hid}", {"note": "Gast"}),
        ("delete", f"/api/cook-notes/{hid}/photo", None),
        ("post", "/api/meal-wishes", {"week_start": "2026-10-05", "recipe_id": rid}),
        ("put", f"/api/meal-wishes/{wid}/vote", {"voted": True}),
        ("delete", f"/api/meal-wishes/{wid}", None),
        ("post", f"/api/meal-wishes/{wid}/plan", {"planned_for": "2026-10-06", "planned_servings": 2}),
    ]
    for method, path, payload in operations:
        assert client.request(method, path, json=payload).status_code == 403, (method, path)
    assert client.post(f"/api/cook-notes/{hid}/photo", files={"file": ("dish.png", png(), "image/png")}).status_code == 403
    client.headers.pop("Authorization")
    for path in ("/api/cookbooks", f"/api/cook-notes?recipe_id={rid}", "/api/meal-wishes?week_start=2026-10-05", f"/api/cook-notes/{hid}/photo"):
        assert client.get(path).status_code == 401


@pytest.mark.parametrize("date_value", ["bad", "2026-02-30", "20261005", "2026-W41-1", ""])
def test_wish_dates_reject_ambiguous_formats(household_api, date_value):
    client, _, _, login = household_api
    login("anna")
    assert client.get("/api/meal-wishes", params={"week_start": date_value}).status_code == 422


def test_household_merge_unions_collections_votes_and_preserves_photos(household_api):
    _, db, users, _ = household_api
    source, target = users["bert"][1], users["anna"][1]
    rid, private = recipe(db), recipe(db, "BertsPrivat", source)
    hid = history(db, source, private, "bert")
    anna = features.Actor("anna", f"user:{users['anna'][0]}")
    bert = features.Actor("bert", f"user:{users['bert'][0]}")
    source_db, target_db = HouseholdDatabase(db, HouseholdScope(source)), HouseholdDatabase(db, HouseholdScope(target))
    first = features.save_cookbook(source_db, bert, "Lieblinge")["item"]["id"]
    second = features.save_cookbook(target_db, anna, "LIEBLINGE")["item"]["id"]
    features.cookbook_member(source_db, bert, first, private, True)
    features.cookbook_member(target_db, anna, second, rid, True)
    features.save_note(source_db, bert, hid, note="Meine Notiz")
    features.save_note(source_db, bert, hid, photo=features.normalize_photo(png()), change_photo=True)
    for scoped, actor in ((source_db, bert), (target_db, anna)):
        wid = features.add_wish(scoped, actor, "2026-10-05", rid)["item"]["id"]
        features.vote_wish(scoped, actor, wid, True)
        features.plan_wish(scoped, actor, wid, "2026-10-06" if scoped is source_db else "2026-10-07", 2)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        merge_households(c, source, target)
    books = features.cookbooks(target_db)["items"]
    assert len(books) == 1 and books[0]["recipe_count"] == 2
    assert features.cookbooks(source_db)["items"] == []
    notes = features.cook_notes(target_db, bert, private)["items"]
    assert notes[0]["note"] == "Meine Notiz" and notes[0]["photo_url"] and notes[0]["can_edit"]
    assert features.note_photo(target_db, hid)
    wishes = features.meal_wishes(target_db, anna, "2026-10-05")["items"]
    assert len(wishes) == 1 and wishes[0]["votes"] == 2 and wishes[0]["my_vote"]
    assert len(target_db.meal_plan_entries("2026-10-05", "2026-10-11")) == 2


def test_parallel_duplicate_wish_and_plan_requests_commit_once(test_db):
    db = HouseholdDatabase(test_db, HouseholdScope(101))
    actor = features.Actor("anna", "user:anna")
    rid = recipe(test_db)
    with ThreadPoolExecutor(max_workers=4) as executor:
        wishes = list(executor.map(lambda _: features.add_wish(db, actor, "2026-10-05", rid), range(4)))
    assert all(item == wishes[0] for item in wishes)
    wid = wishes[0]["item"]["id"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: features.plan_wish(db, actor, wid, "2026-10-06", 2), range(4)))
    assert all(item == results[0] for item in results)
    assert len(db.meal_plan_entries("2026-10-05", "2026-10-11")) == 1


def test_planning_wish_does_not_overwrite_existing_recipe_portions(household_api):
    client, db, _, login = household_api
    rid = recipe(db)
    login("anna")
    client.post("/api/meal-plan/items", json={"planned_for": "2026-10-06", "recipe_id": rid, "planned_servings": 6})
    wid = client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": rid}).json()["item"]["id"]
    response = client.post(f"/api/meal-wishes/{wid}/plan", json={"planned_for": "2026-10-06", "planned_servings": 2})
    assert response.status_code == 409
    assert client.get("/api/meal-wishes?week_start=2026-10-05").json()["items"][0]["planned_for"] is None
    with db.conn() as c:
        before = dict(c.execute("SELECT * FROM meal_plan_entries WHERE recipe_id=?", (rid,)).fetchone())
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={"planned_for": "2026-10-06", "planned_servings": 6}).status_code == 200
    with db.conn() as c:
        after = dict(c.execute("SELECT * FROM meal_plan_entries WHERE recipe_id=?", (rid,)).fetchone())
    assert before == after


def test_user_deletion_keeps_shared_memories_and_cleans_orphan_household(household_api):
    _, db, users, _ = household_api
    rid = recipe(db)
    anna = features.Actor("anna", f"user:{users['anna'][0]}")
    bert = features.Actor("bert", f"user:{users['bert'][0]}")
    for name, actor in (("anna", anna), ("bert", bert)):
        aid = users[name][1]
        scoped = HouseholdDatabase(db, HouseholdScope(aid))
        bid = features.save_cookbook(scoped, actor, "Fotos")["item"]["id"]
        features.cookbook_member(scoped, actor, bid, rid, True)
        hid = history(db, aid, rid, name)
        features.save_note(scoped, actor, hid, photo=features.normalize_photo(png()), change_photo=True)
        wid = features.add_wish(scoped, actor, "2026-10-05", rid)["item"]["id"]
        features.vote_wish(scoped, actor, wid, True)
    assert db.user_delete(users["anna"][0])
    shared = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    assert len(features.cookbooks(shared)["items"]) == 1
    assert features.cook_notes(shared, features.Actor("admin", "admin", is_admin=True), rid)["items"][0]["photo_url"]
    assert features.meal_wishes(shared, features.Actor("cara", "cara"), "2026-10-05")["items"][0]["votes"] == 0
    with pytest.raises(HTTPException) as rejected:
        db.user_delete(users["bert"][0])
    assert rejected.value.status_code == 409
    # The existing lifecycle refuses implicit data loss; explicit household
    # retirement still runs all new cleanup triggers.
    with db.conn() as c:
        c.execute("DELETE FROM user_accounts WHERE id=?", (users["bert"][1],))
    assert db.user_delete(users["bert"][0])
    with db.conn() as c:
        for table in ("household_cookbooks", "recipe_cook_notes", "household_meal_wishes"):
            assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id=?", (users["bert"][1],)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM household_meal_wish_votes").fetchone()[0] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_features_claim_follows_history_and_only_runs_once(test_db):
    from app.tenancy import claim_legacy_data
    operator = test_db.user_create("operator", "unused-password", role="admin")
    member = test_db.user_create("member", "unused-password", role="user")
    target = accounts.view(test_db, operator)["id"]
    other = accounts.view(test_db, member)["id"]
    rid = recipe(test_db)
    local = HouseholdDatabase(test_db, HouseholdScope(0, is_admin=True))
    actor = features.Actor("local", "legacy:local", is_admin=True)
    bid = features.save_cookbook(local, actor, "Altbestand")["item"]["id"]
    features.cookbook_member(local, actor, bid, rid, True)
    hid = history(test_db, 0, rid, "member")
    features.save_note(local, actor, hid, note="Meine alte Notiz")
    features.add_wish(local, actor, "2026-10-05", rid)
    with test_db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        claim_legacy_data(c, target)
    assert features.cookbooks(HouseholdDatabase(test_db, HouseholdScope(target)))["items"][0]["recipe_count"] == 1
    other_db = HouseholdDatabase(test_db, HouseholdScope(other))
    assert features.cook_notes(other_db, features.Actor("member", f"user:{member}"), rid)["items"][0]["note"] == "Meine alte Notiz"
    with test_db.conn() as c:
        claim_legacy_data(c, other)
    assert features.cookbooks(other_db)["items"] == []


def test_upgrade_269_has_verified_backup_and_preserves_existing_history(tmp_path):
    from app.db import CURRENT_SCHEMA_VERSION, Database
    path = tmp_path / "upgrade.db"
    db = Database(path)
    rid = recipe(db)
    hid = history(db, 0, rid, "local")
    with db.conn() as c:
        c.execute("DROP TRIGGER delete_household_features")
        c.execute("DROP TRIGGER delete_meal_votes_for_user")
        c.execute("ALTER TABLE recipe_cook_history DROP COLUMN cooked_by_user_id")
        for table in ("household_cookbook_recipes", "household_cookbooks", "recipe_cook_notes", "household_meal_wish_votes", "household_meal_wishes"):
            c.execute(f"DROP TABLE {table}")
        c.execute("DELETE FROM schema_migrations WHERE version>269")
    upgraded = Database(path)
    backups = list((tmp_path / "backups").glob(f"pre-migration-v269-to-v{CURRENT_SCHEMA_VERSION}-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 269
        assert c.execute("SELECT id FROM recipe_cook_history").fetchone()[0] == hid
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='household_cookbooks'").fetchone() is None
    with upgraded.conn() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
    actor = features.Actor("local", "legacy:local", is_admin=True)
    assert features.save_note(upgraded, actor, hid, note="Nach Upgrade")["item"]["note"] == "Nach Upgrade"
    Database(path)
    assert list((tmp_path / "backups").glob("pre-migration-*.db")) == backups


def test_pending_variants_are_hidden_from_every_household_feature(household_api):
    from app.db import RECIPE_VARIANT_PENDING_STATUS
    client, db, users, login = household_api
    rid = recipe(db)
    hid = history(db, users["anna"][1], rid)
    login("anna")
    bid = client.post("/api/cookbooks", json={"name": "Varianten"}).json()["item"]["id"]
    assert client.put(f"/api/cookbooks/{bid}/recipes/{rid}").status_code == 200
    wid = client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": rid}).json()["item"]["id"]
    assert client.post(f"/api/cook-notes/{hid}/photo", files={"file": ("dish.png", png(), "image/png")}).status_code == 200
    with db.conn() as c:
        c.execute("UPDATE recipes SET ingredients_status=? WHERE id=?", (RECIPE_VARIANT_PENDING_STATUS, rid))
    assert client.get("/api/cookbooks").json()["items"][0]["recipe_count"] == 0
    assert client.get(f"/api/cookbooks/{bid}/recipes").json()["items"] == []
    assert client.get(f"/api/cookbooks?recipe_id={rid}").status_code == 404
    assert client.put(f"/api/cookbooks/{bid}/recipes/{rid}").status_code == 404
    assert client.get(f"/api/cook-notes?recipe_id={rid}").status_code == 404
    assert client.get(f"/api/cook-notes/{hid}/photo").status_code == 404
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Versteckt"}).status_code == 404
    assert client.get("/api/meal-wishes?week_start=2026-10-05").json()["items"] == []
    assert client.put(f"/api/meal-wishes/{wid}/vote", json={"voted": True}).status_code == 404
    assert client.post(f"/api/meal-wishes/{wid}/plan", json={"planned_for": "2026-10-06", "planned_servings": 2}).status_code == 404


def test_reused_username_cannot_edit_old_notes_or_delete_old_wishes(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    hid = history(db, users["anna"][1], rid)
    login("anna")
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Original"}).status_code == 200
    wid = client.post("/api/meal-wishes", json={"week_start": "2026-10-05", "recipe_id": rid}).json()["item"]["id"]
    assert db.user_delete(users["anna"][0])
    replacement = db.user_create("anna", "unused-password", role="user")
    accounts.view(db, replacement)
    with db.conn() as c:
        c.execute("UPDATE account_members SET account_id=? WHERE user_id=?", (users["anna"][1], replacement))
    login("anna")
    item = client.get(f"/api/cook-notes?recipe_id={rid}").json()["items"][0]
    assert item["note"] == "Original" and item["can_edit"] is False
    assert client.put(f"/api/cook-notes/{hid}", json={"note": "Übernommen"}).status_code == 403
    assert client.delete(f"/api/meal-wishes/{wid}").status_code == 403


def test_cooking_completion_records_stable_note_owner(household_api):
    client, db, users, login = household_api
    rid = recipe(db)
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1], user_id=users["anna"][0]))
    cooked = scoped.recipe_cooking_complete(rid, "anna", servings=2, idempotency_key="stable-notes")
    assert cooked["cooked_by_user_id"] == users["anna"][0]
    login("anna")
    assert client.put(f"/api/cook-notes/{cooked['id']}", json={"note": "Gut gelungen"}).status_code == 200


def test_upgrade_268_adds_features_without_assigning_reused_name_history(tmp_path):
    from app.db import CURRENT_SCHEMA_VERSION, Database
    path = tmp_path / "release-268.db"
    db = Database(path)
    uid = db.user_create("known", "unused-password", role="admin")
    rid = recipe(db)
    known = history(db, 0, rid, "known")
    reused = history(db, 0, rid, "reused")
    with db.conn() as c:
        c.execute("UPDATE recipe_cook_history SET cooked_at=? WHERE id=?", (time.time() - 3600, reused))
    db.user_create("reused", "unused-password", role="user")
    db.cart_add_or_merge(name="Pasta", canonical_name="pasta", amount=500, unit="g", source_recipe_id=rid)
    with db.conn() as c:
        for trigger in ("delete_household_features", "delete_meal_votes_for_user", "delete_shopping_history"):
            c.execute(f"DROP TRIGGER {trigger}")
        for table in ("household_cookbook_recipes", "household_cookbooks", "recipe_cook_notes",
                      "household_meal_wish_votes", "household_meal_wishes", "shopping_deleted_items", "shopping_sync_operations"):
            c.execute(f"DROP TABLE {table}")
        c.execute("ALTER TABLE recipe_cook_history DROP COLUMN cooked_by_user_id")
        c.execute("ALTER TABLE shopping_cart DROP COLUMN source_contributions")
        c.execute("DELETE FROM schema_migrations WHERE version>268")
    upgraded = Database(path)
    with upgraded.conn() as c:
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert c.execute("SELECT cooked_by_user_id FROM recipe_cook_history WHERE id=?", (known,)).fetchone()[0] == uid
        assert c.execute("SELECT cooked_by_user_id FROM recipe_cook_history WHERE id=?", (reused,)).fetchone()[0] is None
        assert c.execute("SELECT amount FROM shopping_cart").fetchone()[0] == 500
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = list((tmp_path / "backups").glob(f"pre-migration-v268-to-v{CURRENT_SCHEMA_VERSION}-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 268
