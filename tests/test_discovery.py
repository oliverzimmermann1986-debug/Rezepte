"""Suggestions, strict source constraints and household boundaries."""
from __future__ import annotations

import time
from uuid import uuid4

import pytest
from itsdangerous import URLSafeTimedSerializer

from app import accounts, auth
from app.recipes.canonical import canonical_name
from app.recipes.discovery import explicit_total_minutes


def _recipe(db, name, ingredients=("Tomaten", "Nudeln"), *, description="Gesamtzeit: 25 Minuten",
            tags=(), owner=None, complete=True, status="ok", servings=2):
    rid = db.recipe_upsert(
        url=f"https://discovery.example/{name}", name=name, type="Hauptgericht", category=name,
        folder_path=f"/discovery-tests/{name}", description=description, thumb_filename=None,
        video_filename=None, source_added_at=time.time(), owner_account_id=owner,
    )
    db.recipe_set_extraction_result(rid, status="ok", ingredients=[
        {"name": name, "canonical_name": canonical_name(name), "amount": 100, "unit": "g"}
        for name in ingredients
    ])
    if status != "ok":
        db.recipe_set_extraction_result(rid, status=status)
    if complete:
        db.recipe_steps_set(rid, [{"instruction": "Zutaten zubereiten.", "timer_seconds": 300}])
    db.recipe_tags_set(rid, list(tags))
    db.recipe_set_servings(rid, servings)
    return rid


@pytest.fixture
def discovery_accounts(client, test_db, monkeypatch):
    from app.main import app
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("discovery-tests-secret" * 3))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    users = {}
    for name in ("anna-discovery", "bert-discovery"):
        uid = test_db.user_create(name, "unused-test-password", role="user")
        users[name] = accounts.view(test_db, uid)["id"]

    def login(name):
        token = auth.create_guest_session() if name == "guest" else auth.create_session(name)
        client.headers["Authorization"] = "Bearer " + token
        return client

    yield users, login
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def test_ingredient_matches_normalize_synonyms_and_rank_full_coverage(client, test_db):
    complete = _recipe(test_db, "Karottensalat", ("Möhren", "Cherry-Tomaten"))
    partial = _recipe(test_db, "Pasta")
    _recipe(test_db, "FremdeZutaten", ("Haferflocken",))
    response = client.post("/api/discovery/ingredients", json={"ingredients": ["Karotten", "Tomate", "Tomaten"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["recipe_id"] for item in result["items"]] == [complete, partial]
    assert result["items"][0]["coverage"] == 1
    assert result["items"][0]["missing_ingredients"] == []
    assert result["items"][1]["coverage"] == .5
    assert result["items"][1]["missing_ingredients"] == [{"name": "Nudeln", "amount": 100, "unit": "g"}]
    assert "keine Vorratsmengen" in result["warnings"][0]


def test_discovery_omits_incomplete_failed_and_deleted_recipes(client, test_db):
    valid = _recipe(test_db, "Vollständig")
    _recipe(test_db, "KeineZutaten", ())
    _recipe(test_db, "KeineSchritte", complete=False)
    _recipe(test_db, "FehlerhafteExtraktion", status="error")
    deleted = _recipe(test_db, "Gelöscht")
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time(), deleted))
    for endpoint, payload in (("ingredients", {"ingredients": ["Tomaten"]}), ("meal-plan", {"count": 7})):
        response = client.post(f"/api/discovery/{endpoint}", json=payload)
        assert response.status_code == 200, response.text
        assert [item["recipe_id"] for item in response.json()["items"]] == [valid]


@pytest.mark.parametrize("description,expected", [
    ("Gesamtzeit: 30 Minuten", 30),
    ("Beschreibung\nGesamtdauer: 1 h 15 min\nGuten Appetit!", 75),
    ("Total time: 1.5 hours", None),
    ("Total time: 1,5 h", 90),
    ("Gesamtzeit: 20,5 min", 21),
    ("Schnell, in 10 Minuten fertig", None),
    ("Kochzeit: 10 Minuten\nRuhezeit: 60 Minuten", None),
    ("Gesamtzeit: 20-40 Minuten", None),
    ("Gesamtzeit: 20 Minuten plus Ruhezeit", None),
    ("Gesamtzeit: 20 Min.\nGesamtzeit: 30 Min.", None),
    ("Gesamtzeit: 0 Minuten", None),
])
def test_duration_requires_unambiguous_explicit_total(description, expected):
    assert explicit_total_minutes({"description": description}) == expected


def test_meal_plan_meets_time_and_vegetarian_minimum_without_repeats(client, test_db):
    veggie = _recipe(test_db, "Vegetarisch", tags=("Vegetarisch",))
    vegan = _recipe(test_db, "Vegan", tags=("vegan",))
    normal = _recipe(test_db, "Normal", ingredients=("Huhn",))
    excluded = _recipe(test_db, "Ausgeschlossen", tags=("vegan",))
    _recipe(test_db, "Langsam", description="Gesamtzeit: 90 Minuten", tags=("vegan",))
    _recipe(test_db, "UnbekannteZeit", description="10 Minuten köcheln lassen", tags=("vegan",))
    response = client.post("/api/discovery/meal-plan", json={
        "count": 3, "vegetarian_count": 2, "max_minutes": 30, "exclude_recipe_ids": [excluded],
    })
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["recipe_id"] for item in items} == {veggie, vegan, normal}
    assert len(items) == len({item["recipe_id"] for item in items})
    assert sum(item["vegetarian"] for item in items) == 2
    assert all(item["total_minutes"] <= 30 and item["servings"] == 2 for item in items)
    assert any("Gesamtzeit" in warning for warning in response.json()["warnings"])


def test_missing_vegetarian_slots_stay_empty_and_unknown_time_requires_no_bound(client, test_db):
    vegetarian = _recipe(test_db, "EinVegetarisches", tags=("vegan",), description="Ohne Zeitangabe")
    _recipe(test_db, "NichtMarkiert")
    _recipe(test_db, "Fleisch", ingredients=("Huhn",))
    response = client.post("/api/discovery/meal-plan", json={"count": 3, "vegetarian_count": 2})
    assert response.status_code == 200
    result = response.json()
    assert len(result["items"]) == 2
    assert result["items"][0]["recipe_id"] == vegetarian
    assert result["items"][0]["total_minutes"] is None
    assert any("1 von 2" in warning for warning in result["warnings"])


def test_missing_to_cart_recomputes_missing_and_merges_only_those_scaled(client, test_db):
    recipe = _recipe(test_db, "Einkaufen", ingredients=("Tomaten", "Nudeln", "Salz"))
    test_db.shopping_exclusion_set("salz", True)
    test_db.cart_add_or_merge(name="Nudeln", canonical_name="nudeln", amount=50, unit="g", source_recipe_id=None)
    response = client.post("/api/discovery/missing-to-cart", json={
        "recipe_id": recipe, "ingredients": ["Cherry-Tomaten"], "servings": 4, "request_id": str(uuid4()),
    })
    assert response.status_code == 200, response.text
    assert response.json()["merged"] == 1 and response.json()["skipped"] == 2
    cart = test_db.cart_list()
    assert len(cart) == 1
    assert cart[0]["canonical_name"] == "nudeln" and cart[0]["amount"] == 250


def test_missing_to_cart_all_present_is_noop_and_missing_servings_is_conflict(client, test_db):
    recipe = _recipe(test_db, "OhnePortionen", servings=None)
    assert client.post("/api/discovery/missing-to-cart", json={
        "recipe_id": recipe, "ingredients": ["Tomaten", "Pasta"], "request_id": str(uuid4()),
    }).json()["added"] == 0
    assert test_db.cart_list() == []
    assert client.post("/api/discovery/missing-to-cart", json={
        "recipe_id": recipe, "ingredients": ["Tomaten"], "servings": 4, "request_id": str(uuid4()),
    }).status_code == 409
    assert test_db.cart_list() == []


def test_lost_cart_response_replays_once_even_after_recipe_changes(client, test_db):
    recipe = _recipe(test_db, "VerloreneAntwort")
    payload = {"recipe_id": recipe, "ingredients": ["Tomaten"], "request_id": str(uuid4())}
    first = client.post("/api/discovery/missing-to-cart", json=payload)
    assert first.status_code == 200, first.text
    # The client did not receive its response. Content subsequently changes;
    # replay must return the receipt instead of reevaluating the changed recipe.
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time(), recipe))
    retry = client.post("/api/discovery/missing-to-cart", json={**payload, "ingredients": ["Cherry-Tomaten"]})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert test_db.cart_list()[0]["amount"] == 100
    mismatch = client.post("/api/discovery/missing-to-cart", json={**payload, "ingredients": []})
    assert mismatch.status_code == 409
    assert test_db.cart_list()[0]["amount"] == 100


def test_cart_retry_receipts_are_scoped_to_the_household(client, test_db, discovery_accounts):
    _, login = discovery_accounts
    recipe = _recipe(test_db, "GemeinsamesReplay")
    payload = {"recipe_id": recipe, "ingredients": ["Tomaten"], "request_id": str(uuid4())}
    for username in ("anna-discovery", "bert-discovery"):
        login(username)
        first = client.post("/api/discovery/missing-to-cart", json=payload)
        retry = client.post("/api/discovery/missing-to-cart", json=payload)
        assert first.status_code == 200 and retry.json() == first.json()
        items = client.get("/api/cart").json()["items"]
        assert len(items) == 1 and items[0]["amount"] == 100


def test_first_add_reopens_checked_ingredient_but_receipt_retry_does_not(client, test_db):
    recipe = _recipe(test_db, "NochEinmalKaufen")
    cart_id = test_db.cart_add_or_merge(name="Nudeln", canonical_name="nudeln", amount=50, unit="g", source_recipe_id=None)
    test_db.cart_update(cart_id, checked=True)
    payload = {"recipe_id": recipe, "ingredients": ["Tomaten"], "request_id": str(uuid4())}
    first = client.post("/api/discovery/missing-to-cart", json=payload)
    assert first.status_code == 200
    assert test_db.cart_list()[0]["checked"] == 0
    test_db.cart_update(cart_id, checked=True)
    replay = client.post("/api/discovery/missing-to-cart", json=payload)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert test_db.cart_list()[0]["checked"] == 1
    assert test_db.cart_list()[0]["amount"] == 150


def test_plan_apply_retry_uses_existing_unique_date_and_recipe(client, test_db):
    recipe = _recipe(test_db, "PlanReplay")
    payload = {"recipe_id": recipe, "planned_for": "2026-10-12", "planned_servings": 4}
    first = client.post("/api/meal-plan/items", json=payload)
    retry = client.post("/api/meal-plan/items", json=payload)
    assert first.status_code == 200 and retry.json() == first.json()
    entries = test_db.meal_plan_entries("2026-10-12", "2026-10-18")
    assert len(entries) == 1 and entries[0]["planned_servings"] == 4


def test_private_recipes_and_cart_are_household_scoped(client, test_db, discovery_accounts):
    users, login = discovery_accounts
    global_id = _recipe(test_db, "GlobalDiscovery", tags=("vegan",))
    private_id = _recipe(test_db, "PrivatDiscovery", owner=users["anna-discovery"], tags=("vegan",))
    for username, expected in (("anna-discovery", {global_id, private_id}), ("bert-discovery", {global_id})):
        login(username)
        for endpoint, payload in (("ingredients", {"ingredients": ["Tomate"]}), ("meal-plan", {"count": 7})):
            response = client.post(f"/api/discovery/{endpoint}", json=payload)
            assert response.status_code == 200, response.text
            assert {item["recipe_id"] for item in response.json()["items"]} == expected
    assert client.post("/api/discovery/missing-to-cart", json={"recipe_id": private_id, "ingredients": [], "request_id": str(uuid4())}).status_code == 404
    login("anna-discovery")
    assert client.post("/api/discovery/missing-to-cart", json={"recipe_id": private_id, "ingredients": ["Tomate"], "request_id": str(uuid4())}).status_code == 200
    assert len(client.get("/api/cart").json()["items"]) == 1
    login("bert-discovery")
    assert client.get("/api/cart").json()["items"] == []


def test_guests_and_anonymous_requests_cannot_use_post_endpoints(client, test_db, discovery_accounts):
    _, login = discovery_accounts
    recipe = _recipe(test_db, "AuthDiscovery")
    requests = (("ingredients", {"ingredients": ["Tomaten"]}), ("meal-plan", {"count": 2}),
                ("missing-to-cart", {"recipe_id": recipe, "ingredients": []}))
    client.headers.pop("Authorization", None)
    for endpoint, payload in requests:
        assert client.post(f"/api/discovery/{endpoint}", json=payload).status_code == 401
    login("guest")
    for endpoint, payload in requests:
        assert client.post(f"/api/discovery/{endpoint}", json=payload).status_code == 403
    assert test_db.cart_list() == []


@pytest.mark.parametrize("endpoint,payload", [
    ("ingredients", {"ingredients": []}),
    ("ingredients", {"ingredients": ["  "]}),
    ("ingredients", {"ingredients": ["Tomate"] * 101}),
    ("meal-plan", {"count": 2, "vegetarian_count": 3}),
    ("meal-plan", {"count": 8}),
    ("meal-plan", {"max_minutes": 0}),
    ("meal-plan", {"exclude_recipe_ids": [-1]}),
    ("missing-to-cart", {"recipe_id": 1, "servings": 0}),
])
def test_invalid_discovery_requests_are_rejected(client, endpoint, payload):
    assert client.post(f"/api/discovery/{endpoint}", json=payload).status_code == 422


@pytest.mark.parametrize("unit,servings", [("g", 4), ("kg", 2)])
def test_missing_cart_overflow_rolls_back_items_and_receipt(client, test_db, unit, servings):
    rid = _recipe(test_db, "OverflowDiscovery", servings=2)
    test_db.recipe_set_extraction_result(rid, status="ok", ingredients=[
        {"name": "Mehl", "amount": 2, "unit": "g"},
        {"name": "Reis", "amount": 1e308, "unit": unit},
    ])
    with test_db.conn() as c:
        products = [tuple(row) for row in c.execute("SELECT * FROM shopping_products ORDER BY canonical_name")]
    response = client.post("/api/discovery/missing-to-cart", json={
        "recipe_id": rid, "ingredients": [], "servings": servings, "request_id": str(uuid4()),
    })
    assert response.status_code == 422, response.text
    assert test_db.cart_list() == []
    with test_db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM shopping_sync_operations").fetchone()[0] == 0
        assert [tuple(row) for row in c.execute("SELECT * FROM shopping_products ORDER BY canonical_name")] == products
