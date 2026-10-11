"""Real-session import comparison, private variants and loss-safe retry contracts."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app import auth
from app.recipes import manage
from app.routes import api_import_comparison
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.test_tenants import households as households


@pytest.fixture
def comparison(households, tmp_path, monkeypatch):
    client, db, users, login = households
    for username in ("anna", "bert"):
        db.user_set_role(users[username][0], auth.ROLE_FULL_USER)
    root = tmp_path / "recipe-files"
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    def recipe(name, url, owner=None):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "description.txt").write_text("Originalbeschreibung", encoding="utf-8")
        rid = db.recipe_upsert(url=url, name=name, type="Hauptgericht", category="Test",
            folder_path=str(folder), description="Originalbeschreibung", thumb_filename=None,
            video_filename=None, source_added_at=1, owner_account_id=owner)
        db.recipe_set_extraction_result(rid, "ok", [{"name": "Hafer", "canonical_name": "hafer", "amount": 100, "unit": "g"}])
        db.recipe_steps_set(rid, [{"instruction": "Alles vermischen."}])
        db.recipe_set_servings(rid, 2)
        return rid

    original = recipe("Haferbrei", "https://original.example/porridge")
    foreign = recipe("Haferbrei Geheim", "https://foreign.example/porridge", owner=users["bert"][1])
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1]))
    url = "https://new.example/porridge"
    draft = {
        "name": "Haferbrei mit Apfel", "description": "Eigene Importbeschreibung",
        "servings": 4,
        "ingredients": [{"name": "Hafer", "amount": 250, "unit": "g", "raw": None},
                        {"name": "Apfel", "amount": 2, "unit": "Stück", "raw": None}],
        "steps": [{"instruction": "Hafer mit Apfel kochen.", "timer_seconds": 120}],
    }
    scoped.pending_add(url, content_type="recipe", ai_suggestion=draft)
    login("anna")
    payload = {"url": url, "visibility": "private", "draft": draft}
    return client, db, users, login, scoped, original, foreign, payload


def resolution(client, payload, **values):
    result = client.post("/api/import-comparison/preview", json=payload)
    assert result.status_code == 200, result.text
    candidate = result.json()["items"][0]
    return {**payload, "request_id": str(uuid4()), "recipe_id": candidate["id"],
            "snapshot": candidate["snapshot"], "action": "merge", "name": "Mein Haferbrei",
            "ingredients_from": "import", "steps_from": "existing", **values}


def test_cross_source_preview_is_household_scoped_and_explains_differences(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    response = client.post("/api/import-comparison/preview", json=payload)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [original]
    assert items[0]["url"] == "https://original.example/porridge"
    assert items[0]["shared_ingredients"] == ["hafer"]
    assert items[0]["import_only_ingredients"] == ["apfel"]
    assert items[0]["ingredients"][0]["amount"] == 100
    assert len(items[0]["snapshot"]) == 64
    login("bert")
    assert client.post("/api/import-comparison/preview", json=payload).status_code == 404


def test_authentication_guest_and_guessed_private_id_are_rejected(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload, recipe_id=foreign)
    assert client.post("/api/import-comparison/resolve", json=body).status_code == 404
    for endpoint, request in (("preview", payload), ("resolve", body)):
        login("guest")
        assert client.post(f"/api/import-comparison/{endpoint}", json=request).status_code == 403
        client.headers.pop("Authorization")
        assert client.post(f"/api/import-comparison/{endpoint}", json=request).status_code == 401
    db.user_set_role(users["anna"][0], auth.ROLE_USER)
    login("anna")
    assert client.post("/api/import-comparison/preview", json=payload).status_code == 403
    assert client.post("/api/import-comparison/resolve", json=body).status_code == 403


def test_keep_saves_existing_recipe_resolves_import_and_replays(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload, action="keep")
    for attempt in range(2):
        response = client.post("/api/import-comparison/resolve", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["recipe_id"] == original
    assert scoped.pending_get(payload["url"])["status"] == "resolved"
    assert scoped.recipe_get(original)["in_library"]
    assert db.recipe_count() == 2
    assert client.post("/api/import-comparison/resolve", json={**body, "action": "variant"}).status_code == 409


@pytest.mark.parametrize("action", ["merge", "variant"])
def test_private_variant_preserves_source_and_uses_selected_complete_lists(comparison, action):
    client, db, users, login, scoped, original, foreign, payload = comparison
    before = db.recipe_get(original)
    original_ingredients = db.recipe_ingredients_get(original)
    original_steps = db.recipe_steps_get(original)
    db.recipe_tags_set(original, ["glutenfrei", "Favorit"])
    assert client.post("/api/meal-plan/items", json={
        "recipe_id": original, "planned_for": "2026-10-12", "planned_servings": 2,
    }).status_code == 200
    scoped.cart_add_or_merge(name="Hafer", canonical_name="hafer", amount=100, unit="g", source_recipe_id=original)
    plan_before = scoped.meal_plan_entries("2026-10-12", "2026-10-18")
    cart_before = scoped.cart_list()
    body = resolution(client, payload, action=action)
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 200, response.text
    rid = response.json()["recipe_id"]
    assert rid != original
    variant = db.recipe_get(rid)
    assert variant["owner_account_id"] == users["anna"][1]
    assert variant["servings"] == 4
    assert variant["user_verified"] == 0 and variant["calories_per_serving"] is None
    assert [(row["name"], row["amount"]) for row in db.recipe_ingredients_get(rid)] == [("Hafer", 250), ("Apfel", 2)]
    assert [step["instruction"] for step in db.recipe_steps_get(rid)] == (
        ["Hafer mit Apfel kochen."] if action == "variant" else ["Alles vermischen."])
    assert db.recipe_tags_get(rid) == []
    assert db.recipe_get(original) == before
    assert db.recipe_ingredients_get(original) == original_ingredients
    assert db.recipe_steps_get(original) == original_steps
    assert scoped.meal_plan_entries("2026-10-12", "2026-10-18") == plan_before
    assert scoped.cart_list() == cart_before
    assert scoped.pending_get(payload["url"])["status"] == "resolved"
    info = json.loads((Path(variant["folder_path"]) / "info.json").read_text(encoding="utf-8"))
    assert info["variant_provenance"]["import_url"] == payload["url"]
    assert info["variant_provenance"]["import_draft"]["ingredients"][0]["amount"] == 250
    assert client.post("/api/import-comparison/resolve", json=body).json()["recipe_id"] == rid
    login("bert")
    assert client.get(f"/api/recipes/{rid}").status_code == 404


def test_existing_ingredients_keep_their_own_servings(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload, ingredients_from="existing", steps_from="import")
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 200, response.text
    rid = response.json()["recipe_id"]
    assert db.recipe_get(rid)["servings"] == 2
    assert [row["amount"] for row in db.recipe_ingredients_get(rid)] == [100]
    assert db.recipe_steps_get(rid)[0]["instruction"] == "Hafer mit Apfel kochen."


def test_parallel_changes_require_a_fresh_comparison(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload)
    db.recipe_steps_set(original, [{"instruction": "Inzwischen geändert"}])
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 409
    assert db.recipe_count() == 2
    assert scoped.pending_get(payload["url"])["status"] == "pending"


def test_finalized_variant_recovers_if_pending_ack_failed(comparison, monkeypatch):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload)
    finish = api_import_comparison.import_comparison.finish_pending
    monkeypatch.setattr(api_import_comparison.import_comparison, "finish_pending",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted")))
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 409
    assert scoped.pending_get(payload["url"])["status"] == "pending"
    monkeypatch.setattr(api_import_comparison.import_comparison, "finish_pending", finish)
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 200, response.text
    assert db.recipe_count() == 3
    assert scoped.pending_get(payload["url"])["status"] == "resolved"


@pytest.mark.parametrize("failure_point", ["steps", "finalize"])
def test_incomplete_variant_is_never_acknowledged_and_retry_completes_it(comparison, monkeypatch, failure_point):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload, action="variant")
    with monkeypatch.context() as patch:
        if failure_point == "steps":
            patch.setattr(HouseholdDatabase, "recipe_steps_set",
                          lambda *args: (_ for _ in ()).throw(RuntimeError("before steps")))
        else:
            patch.setattr(manage, "finalize_recipe_variant",
                          lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("before finalize")))
        response = client.post("/api/import-comparison/resolve", json=body)
        assert response.status_code == 409
    entry = scoped.pending_get(payload["url"])
    assert entry["status"] == "pending"
    pending_id = entry["ai_suggestion"]["comparison_pending"]["recipe_id"]
    assert db.recipe_get(pending_id) is None
    assert db.recipe_get(pending_id, include_pending=True)["ingredients_status"] == "variant_pending"
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 200, response.text
    rid = response.json()["recipe_id"]
    assert db.recipe_steps_get(rid)[0]["instruction"] == "Hafer mit Apfel kochen."
    assert scoped.pending_get(payload["url"])["status"] == "resolved"
    assert db.recipe_count() == 3


@pytest.mark.parametrize("change", [
    {"name": "x" * 201}, {"ingredients": [{"name": "x", "amount": -1}]},
    {"ingredients": [{"name": "x"}] * 301},
    {"steps": [{"instruction": "x", "timer_seconds": 999999}]},
])
def test_untrusted_payload_limits_are_enforced(comparison, change):
    client, db, users, login, scoped, original, foreign, payload = comparison
    response = client.post("/api/import-comparison/preview", json={**payload, "draft": {**payload["draft"], **change}})
    assert response.status_code == 422


def test_variant_name_cannot_escape_recipe_folder(comparison):
    client, db, users, login, scoped, original, foreign, payload = comparison
    body = resolution(client, payload, name="../../outside")
    response = client.post("/api/import-comparison/resolve", json=body)
    assert response.status_code == 400
    assert db.recipe_count() == 2
