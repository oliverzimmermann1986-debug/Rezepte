"""API-Endpoint-Tests via FastAPI TestClient.
Auth ist überschrieben (siehe conftest.py), get_db() returnt Test-DB.
"""
from tests.conftest import _create_recipe


# ─── Liste + Filter ─────────────────────────────────────────────────────────

def test_list_empty(client, test_db):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_returns_inserted_recipes(client, test_db):
    _create_recipe(test_db, name="Spargel", folder_path="/tmp/sp")
    _create_recipe(test_db, name="Tomate", folder_path="/tmp/to")
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    names = sorted(it["name"] for it in body["items"])
    assert names == ["Spargel", "Tomate"]


def test_list_includes_completeness_counts_and_manual_care_state(client, test_db):
    recipe = _create_recipe(test_db, name="Pasta", folder_path="/tmp/pasta")
    test_db.recipe_set_servings(recipe["id"], 4)
    with test_db.conn() as connection:
        connection.executemany(
            """
            INSERT INTO recipe_ingredients
            (recipe_id, name, canonical_name, amount, unit, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (recipe["id"], "Pasta", "pasta", 500, "g", 0),
                (recipe["id"], "Tomaten", "tomaten", 6, "Stück", 1),
            ],
        )

    item = client.get("/api/recipes").json()["items"][0]

    assert item["servings"] == 4
    assert item["ingredients_count"] == 2
    assert item["steps_count"] == 0
    assert item["needs_manual_care"] is True

    test_db.recipe_steps_set(recipe["id"], [{"instruction": "Alles kochen."}])
    complete = client.get("/api/recipes").json()["items"][0]

    assert complete["steps_count"] == 1
    assert complete["needs_manual_care"] is False


def test_list_filter_needs_manual_care_is_server_side(client, test_db):
    """Filter und total müssen zusammenpassen — auch über die Seitengrenze.

    Die iOS-App filterte früher erst nach dem LIMIT und zählte damit nur
    innerhalb der geladenen Seite; ab Seite 2 fehlten Treffer lautlos.
    """
    lueckenhaft = _create_recipe(test_db, name="Ohne Schritte", folder_path="/tmp/a")
    with test_db.conn() as connection:
        connection.execute(
            """
            INSERT INTO recipe_ingredients
            (recipe_id, name, canonical_name, amount, unit, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lueckenhaft["id"], "Mehl", "mehl", 200, "g", 0),
        )
    leer = _create_recipe(test_db, name="Ganz leer", folder_path="/tmp/b")
    vollstaendig = _create_recipe(test_db, name="Komplett", folder_path="/tmp/c")
    with test_db.conn() as connection:
        connection.execute(
            """
            INSERT INTO recipe_ingredients
            (recipe_id, name, canonical_name, amount, unit, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vollstaendig["id"], "Reis", "reis", 300, "g", 0),
        )
    test_db.recipe_steps_set(vollstaendig["id"], [{"instruction": "Kochen."}])

    offen = client.get("/api/recipes", params={"needs_manual_care": "true"}).json()
    assert offen["total"] == 2
    assert sorted(it["name"] for it in offen["items"]) == ["Ganz leer", "Ohne Schritte"]
    assert all(it["needs_manual_care"] for it in offen["items"])
    assert leer["id"] in {it["id"] for it in offen["items"]}

    fertig = client.get("/api/recipes", params={"needs_manual_care": "false"}).json()
    assert fertig["total"] == 1
    assert fertig["items"][0]["name"] == "Komplett"

    # total bleibt der Filter-Treffer, auch wenn die Seite kleiner ist
    seite = client.get(
        "/api/recipes", params={"needs_manual_care": "true", "limit": 1}
    ).json()
    assert seite["total"] == 2
    assert len(seite["items"]) == 1

    zweite = client.get(
        "/api/recipes",
        params={"needs_manual_care": "true", "limit": 1, "offset": 1},
    ).json()
    assert zweite["total"] == 2
    assert len(zweite["items"]) == 1
    assert zweite["items"][0]["id"] != seite["items"][0]["id"]

    ohne_filter = client.get("/api/recipes").json()
    assert ohne_filter["total"] == 3


def test_ingredient_verification_does_not_hide_other_manual_care(client, test_db):
    recipe = _create_recipe(
        test_db,
        name="Geprüfte Zutaten ohne Schritte",
        folder_path="/tmp/verified-without-steps",
        description="Eine ausreichend lange Rezeptbeschreibung für den Test.",
    )
    test_db.recipe_set_extraction_result(
        recipe["id"],
        "ok",
        [{"name": "Mehl", "canonical_name": "mehl", "amount": 200, "unit": "g"}],
    )
    test_db.recipe_set_verified(recipe["id"], True, "anna")

    listed = client.get("/api/recipes", params={"needs_manual_care": "true"}).json()
    assert recipe["id"] in {item["id"] for item in listed["items"]}

    audit = client.get("/api/audit").json()
    assert recipe["id"] in {item["id"] for item in audit["data_gaps"]["no_steps"]}
    assert recipe["id"] not in {item["id"] for item in audit["data_gaps"]["unverified"]}


def test_verify_uses_request_actor_for_bearer_sessions(client, test_db, monkeypatch):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(test_db, name="Attribution", folder_path="/tmp/actor")
    test_db.recipe_set_extraction_result(
        recipe["id"],
        "ok",
        [{"name": "Mehl", "canonical_name": "mehl", "amount": 200, "unit": "g"}],
    )
    monkeypatch.setattr(api_recipes, "_actor", lambda _request: "anna")

    response = client.post(f"/api/recipes/{recipe['id']}/verify?verified=true")

    assert response.status_code == 200
    assert response.json()["by"] == "anna"
    assert test_db.recipe_get(recipe["id"])["verified_by"] == "anna"


def test_empty_ingredients_cannot_be_marked_verified(client, test_db):
    recipe = _create_recipe(test_db, name="Leer", folder_path="/tmp/empty-verify")

    response = client.post(f"/api/recipes/{recipe['id']}/verify?verified=true")

    assert response.status_code == 409
    assert test_db.recipe_get(recipe["id"])["user_verified"] == 0


def test_recipe_text_translation_uses_selected_language_without_overwriting_source(
    client, test_db, monkeypatch
):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(
        test_db,
        name="Pasta",
        folder_path="/tmp/translate",
        description="Ein deutscher Rezepttext, der unverändert gespeichert bleiben muss.",
    )

    class FakeAnalyzer:
        def translate_text(self, text, target_language):
            assert text == "Kommentar mit 200 g Mehl"
            assert target_language == "en"
            return "Comment with 200 g flour"

    monkeypatch.setattr(api_recipes, "build_analyzer", lambda _config: FakeAnalyzer())

    response = client.post(
        f"/api/recipes/{recipe['id']}/translate",
        json={"target_language": "en", "text": "Kommentar mit 200 g Mehl"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "translation": "Comment with 200 g flour",
        "target_language": "en",
    }
    assert test_db.recipe_get(recipe["id"])["description"].startswith("Ein deutscher")


def test_recipe_text_translation_rejects_unknown_language(client, test_db):
    recipe = _create_recipe(test_db, name="Pasta", folder_path="/tmp/translate-invalid")

    response = client.post(
        f"/api/recipes/{recipe['id']}/translate",
        json={"target_language": "xx", "text": "Kommentar"},
    )

    assert response.status_code == 422


def test_step_timer_requires_whole_seconds(client, test_db):
    recipe = _create_recipe(test_db, name="Timer", folder_path="/tmp/timer")

    response = client.put(
        f"/api/recipes/{recipe['id']}/steps",
        json={"steps": [{"instruction": "Warten", "timer_seconds": 2.5}]},
    )

    assert response.status_code == 422


def test_metadata_rejects_url_with_embedded_credentials(client, test_db):
    recipe = _create_recipe(test_db, name="Metadaten", folder_path="/missing/metadata")

    response = client.put(
        f"/api/recipes/{recipe['id']}/metadata",
        json={
            "name": "Metadaten",
            "type": "Hauptgericht",
            "category": "Test",
            "description": "",
            "servings": 2,
            "url": "https://rezepte.example@evil.test/post",
        },
    )

    assert response.status_code == 400


def test_list_filter_by_type(client, test_db):
    _create_recipe(test_db, name="Suppe", folder_path="/tmp/su", type="Vorspeise")
    _create_recipe(test_db, name="Pasta", folder_path="/tmp/pa", type="Hauptgericht")
    r = client.get("/api/recipes?type=Vorspeise")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Suppe"


def test_list_combines_multiple_allergen_free_tags_with_and(client, test_db):
    both = _create_recipe(test_db, name="Beides frei", folder_path="/tmp/allergen-both")
    gluten = _create_recipe(test_db, name="Nur glutenfrei", folder_path="/tmp/allergen-gluten")
    nuts = _create_recipe(test_db, name="Nur nussfrei", folder_path="/tmp/allergen-nuts")
    test_db.recipe_tags_set(both["id"], ["glutenfrei", "nussfrei"])
    test_db.recipe_tags_set(gluten["id"], ["glutenfrei"])
    test_db.recipe_tags_set(nuts["id"], ["nussfrei"])
    tag_ids = {
        tag["name"]: int(tag["id"])
        for tag in test_db.recipe_tags_get(both["id"])
    }

    response = client.get(
        "/api/recipes",
        params=[
            ("tag_id", tag_ids["glutenfrei"]),
            ("tag_id", tag_ids["nussfrei"]),
        ],
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["name"] for item in response.json()["items"]] == ["Beides frei"]


def test_list_can_include_and_exclude_ingredients(client, test_db):
    recipes = {
        "Knoblauch und Zwiebel": ["knoblauch", "zwiebel"],
        "Nur Knoblauch": ["knoblauch"],
        "Nur Zwiebel": ["zwiebel"],
        "Ohne beide": ["pasta"],
    }
    for index, (name, ingredients) in enumerate(recipes.items()):
        recipe = _create_recipe(
            test_db,
            name=name,
            folder_path=f"/tmp/ingredient-filter-{index}",
        )
        test_db.recipe_set_extraction_result(
            recipe["id"],
            "ok",
            [
                {
                    "name": canonical.title(),
                    "canonical_name": canonical,
                    "amount": 1,
                    "unit": "Stück",
                }
                for canonical in ingredients
            ],
        )
        test_db.recipe_tags_set(
            recipe["id"],
            ["mit-zwiebel" if "zwiebel" in ingredients else "ohne-zwiebel"],
        )

    included = client.get("/api/recipes?ingredient=knoblauch").json()
    assert {item["name"] for item in included["items"]} == {
        "Knoblauch und Zwiebel",
        "Nur Knoblauch",
    }

    excluded = client.get("/api/recipes?exclude_ingredient=zwiebel").json()
    assert {item["name"] for item in excluded["items"]} == {
        "Nur Knoblauch",
        "Ohne beide",
    }

    combined = client.get(
        "/api/recipes?ingredient=knoblauch&exclude_ingredient=zwiebel"
    ).json()
    assert [item["name"] for item in combined["items"]] == ["Nur Knoblauch"]

    excluded_facets = client.get(
        "/api/recipes/facets?exclude_ingredient=zwiebel"
    ).json()
    assert {
        item["name"]: item["n"] for item in excluded_facets["tags"]
    } == {"ohne-zwiebel": 2}

    all_facets = client.get("/api/recipes/facets").json()
    assert {
        item["name"]: item["n"] for item in all_facets["tags"]
    } == {"mit-zwiebel": 2, "ohne-zwiebel": 2}


def test_list_search_in_name(client, test_db):
    _create_recipe(test_db, name="Spargelsalat", folder_path="/tmp/x1")
    _create_recipe(test_db, name="Tomatenpasta", folder_path="/tmp/x2")
    r = client.get("/api/recipes?search=Spargel")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Spargelsalat"


def test_list_search_in_description(client, test_db):
    _create_recipe(test_db, name="Geheim", folder_path="/tmp/g",
                   description="mit viel Knoblauch und Olivenöl")
    r = client.get("/api/recipes?search=Knoblauch")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["description"] == "mit viel Knoblauch und Olivenöl"


def test_list_pagination(client, test_db):
    for i in range(10):
        _create_recipe(test_db, name=f"R{i:02d}", folder_path=f"/tmp/r{i}")
    r = client.get("/api/recipes?limit=3&offset=0")
    body = r.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3

    r2 = client.get("/api/recipes?limit=3&offset=3")
    body2 = r2.json()
    assert len(body2["items"]) == 3
    # Keine Überlappung
    ids_1 = {it["id"] for it in body["items"]}
    ids_2 = {it["id"] for it in body2["items"]}
    assert ids_1.isdisjoint(ids_2)


# ─── Detail-Endpoint ────────────────────────────────────────────────────────

def test_get_detail(client, test_db):
    rec = _create_recipe(test_db, name="Detail-Test", folder_path="/tmp/d")
    r = client.get(f"/api/recipes/{rec['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Detail-Test"
    assert body["folder_path"] == "/tmp/d"
    assert body["user_verified"] is False


def test_get_detail_404(client, test_db):
    r = client.get("/api/recipes/999999")
    assert r.status_code == 404


# ─── Favorite + Rating ──────────────────────────────────────────────────────

def test_toggle_favorite_flow(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    # Initial: nicht Favorit
    r = client.get(f"/api/recipes/{rid}")
    assert r.json().get("is_favorite") in (0, False, None)

    # Toggle ON
    r = client.post(f"/api/recipes/{rid}/favorite")
    assert r.status_code == 200
    assert r.json()["is_favorite"] is True

    # Toggle OFF
    r = client.post(f"/api/recipes/{rid}/favorite")
    assert r.json()["is_favorite"] is False


def test_set_rating(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    r = client.post(f"/api/recipes/{rid}/rating?value=4")
    assert r.status_code == 200
    assert r.json()["rating"] == 4

    # Liste enthält das rating
    r = client.get("/api/recipes")
    assert r.json()["items"][0]["rating"] == 4


def test_set_rating_out_of_range(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    r = client.post(f"/api/recipes/{rec['id']}/rating?value=99")
    assert r.status_code == 422  # FastAPI Validation


def test_min_rating_filter_via_api(client, test_db):
    r1 = _create_recipe(test_db, name="Eins", folder_path="/tmp/1")
    r2 = _create_recipe(test_db, name="Fuenf", folder_path="/tmp/5")
    client.post(f"/api/recipes/{r1['id']}/rating?value=1")
    client.post(f"/api/recipes/{r2['id']}/rating?value=5")

    r = client.get("/api/recipes?min_rating=3")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Fuenf"


def test_multi_category_rating_filter_and_live_count(client, test_db):
    pasta = _create_recipe(
        test_db, name="Pasta Fuenf", folder_path="/tmp/pasta-five", category="Pasta",
    )
    suppe = _create_recipe(
        test_db, name="Suppe Eins", folder_path="/tmp/suppe-one", category="Suppe",
    )
    salat = _create_recipe(
        test_db, name="Salat Drei", folder_path="/tmp/salat-three", category="Salat",
    )
    client.post(f"/api/recipes/{pasta['id']}/rating?value=5")
    client.post(f"/api/recipes/{suppe['id']}/rating?value=1")
    client.post(f"/api/recipes/{salat['id']}/rating?value=3")
    query = "category=Pasta&category=Suppe&rating=1&rating=5"

    response = client.get(f"/api/recipes?{query}")
    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} == {
        "Pasta Fuenf", "Suppe Eins",
    }

    count = client.get(f"/api/recipes/count?{query}")
    assert count.status_code == 200
    assert count.json() == {"total": 2}
    assert client.get("/api/recipes/count?rating=6").status_code == 422


def test_favorite_only_filter_via_api(client, test_db):
    r1 = _create_recipe(test_db, name="A", folder_path="/tmp/a")
    _create_recipe(test_db, name="B", folder_path="/tmp/b")
    client.post(f"/api/recipes/{r1['id']}/favorite")

    r = client.get("/api/recipes?favorite_only=true")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "A"


# ─── Soft-Delete + Trash ────────────────────────────────────────────────────

def test_soft_delete_default(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    # DELETE ohne hard=true → Soft
    r = client.delete(f"/api/recipes/{rid}")
    assert r.status_code == 200
    assert r.json()["soft"] is True

    # Aus Liste verschwunden
    assert client.get("/api/recipes").json()["total"] == 0
    # Im Trash sichtbar
    trash = client.get("/api/recipes/trash/list").json()
    assert trash["total"] == 1
    assert trash["items"][0]["name"] == "X"
    assert trash["items"][0]["days_until_purge"] > 25  # frisch gelöscht


def test_restore_from_trash(client, test_db, tmp_path, monkeypatch):
    import app.recipes.manage as manage

    monkeypatch.setattr(manage, "_recipe_root", lambda: tmp_path.resolve())
    trash_root = tmp_path / "trash"

    class _TestConfig:
        @staticmethod
        def get(*keys, default=None):
            if keys == ("safety", "trash_dir"):
                return str(trash_root)
            return default

    monkeypatch.setattr(manage, "get_config", lambda: _TestConfig())
    folder = tmp_path / "r"
    folder.mkdir()
    rec = _create_recipe(test_db, name="R", folder_path=str(folder))
    rid = rec["id"]
    client.delete(f"/api/recipes/{rid}")

    r = client.post(f"/api/recipes/{rid}/restore")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Wieder in der Liste
    assert client.get("/api/recipes").json()["total"] == 1
    assert client.get("/api/recipes/trash/list").json()["total"] == 0


def test_hard_delete(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    r = client.delete(f"/api/recipes/{rid}?hard=true")
    assert r.status_code == 200
    assert r.json()["soft"] is False

    # Auch nicht im Trash mehr
    assert client.get("/api/recipes/trash/list").json()["total"] == 0
    # Detail-404
    assert client.get(f"/api/recipes/{rid}").status_code == 404


def test_empty_trash(client, test_db):
    for i in range(3):
        rec = _create_recipe(test_db, name=f"R{i}", folder_path=f"/tmp/r{i}")
        client.delete(f"/api/recipes/{rec['id']}")
    assert client.get("/api/recipes/trash/list").json()["total"] == 3

    r = client.delete("/api/recipes/trash/empty?delete_files=false")
    assert r.json()["purged"] == 3
    assert client.get("/api/recipes/trash/list").json()["total"] == 0


# ─── Filter-Kombinationen ───────────────────────────────────────────────────

def test_combined_filter_search_and_favorite(client, test_db):
    r1 = _create_recipe(test_db, name="Spargel-Favorit", folder_path="/tmp/sf")
    _create_recipe(test_db, name="Spargel-Normal", folder_path="/tmp/sn")
    _create_recipe(test_db, name="Pasta", folder_path="/tmp/pa")
    client.post(f"/api/recipes/{r1['id']}/favorite")

    # Search + favorite_only → nur Spargel-Favorit
    r = client.get("/api/recipes?search=Spargel&favorite_only=true")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Spargel-Favorit"
