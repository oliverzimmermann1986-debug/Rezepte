from tests.conftest import _create_recipe

from app.recipes.source_integrity import normalize_source_text, source_diff


def _complete_recipe(db, recipe_id: int) -> None:
    db.recipe_set_servings(recipe_id, 2)
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [{
            "name": "Tomaten",
            "canonical_name": "tomate",
            "amount": 4,
            "unit": "Stück",
            "raw": "4 Tomaten",
        }],
    )
    db.recipe_steps_set(recipe_id, [{"instruction": "Tomaten schneiden und servieren."}])


def test_source_text_normalization_and_diff_are_stable():
    assert normalize_source_text("  Pasta\r\n\r\n  200   g Mehl  \n") == "Pasta\n\n200 g Mehl"
    comparison = source_diff("Pasta\n200 g Mehl", "Pasta\n250 g Mehl")
    assert comparison["changed"] is True
    assert comparison["added_lines"] == 1
    assert comparison["removed_lines"] == 1
    assert any(line == "+250 g Mehl" for line in comparison["lines"])


def test_source_watcher_detects_change_without_overwriting_recipe(
    client, test_db, monkeypatch
):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(
        test_db,
        name="Quellenpasta",
        folder_path="/missing/quellenpasta",
        url="https://example.test/quellenpasta",
        description="Quellenpasta\n\nZutaten:\n- 200 g Mehl",
    )
    _complete_recipe(test_db, recipe["id"])
    payload = {
        "canonical_url": recipe["url"],
        "description_text": recipe["description"],
        "description_source": "recipe-json-ld",
        "page_title": "Quellenpasta",
    }
    monkeypatch.setattr(
        api_recipes,
        "extract_recipe_web_metadata",
        lambda _url, include_thumbnail=False: dict(payload),
    )

    initial = client.get(f"/api/recipes/{recipe['id']}/source-integrity")
    assert initial.status_code == 200
    assert initial.json()["status"] == "unchecked"

    checked = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "current"
    assert checked.json()["baseline"]["is_baseline"] is True
    assert checked.json()["automatic_overwrite"] is False
    assert client.post(
        f"/api/recipes/{recipe['id']}/source-integrity/accept"
    ).status_code == 409

    payload["description_text"] = "Quellenpasta\n\nZutaten:\n- 250 g Mehl\n- 2 Eier"
    changed = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["status"] == "changed"
    assert body["diff"]["changed"] is True
    assert body["diff"]["added_lines"] == 2
    assert any(issue["id"] == "source-changed" for issue in body["quality"]["issues"])
    assert test_db.recipe_get(recipe["id"])["description"] == recipe["description"]

    accepted = client.post(f"/api/recipes/{recipe['id']}/source-integrity/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "current"
    assert accepted.json()["baseline"]["accepted_by"] == "unknown"
    assert test_db.recipe_get(recipe["id"])["description"] == recipe["description"]


def test_source_watcher_records_unavailable_source(client, test_db, monkeypatch):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(
        test_db,
        name="Offlinequelle",
        folder_path="/missing/offlinequelle",
        url="https://example.test/offlinequelle",
        description="Gespeicherter Inhalt",
    )

    def fail(_url, *, include_thumbnail=False):
        raise ValueError("HTTP 503")

    monkeypatch.setattr(api_recipes, "extract_recipe_web_metadata", fail)
    response = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["latest"]["error"] == "HTTP 503"
    assert test_db.recipe_get(recipe["id"])["description"] == "Gespeicherter Inhalt"


def test_recipe_quality_report_flags_duplicates_and_incomplete_sections(client, test_db):
    recipe = _create_recipe(
        test_db,
        name="Prüffall",
        folder_path="/missing/prueffall",
        url="attachment://scan/prueffall",
    )
    with test_db.conn() as connection:
        connection.executemany(
            "INSERT INTO recipe_ingredients "
            "(recipe_id, name, canonical_name, amount, unit, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (recipe["id"], "Tomate", "tomate", None, None, 0),
                (recipe["id"], "Tomaten", "tomate", 2, "Stück", 1),
            ],
        )

    response = client.get(f"/api/recipes/{recipe['id']}/source-integrity")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "local"
    issue_ids = {issue["id"] for issue in body["quality"]["issues"]}
    assert {"steps-missing", "servings-missing", "ingredients-duplicate", "amounts-missing"} <= issue_ids
    assert "source-missing" not in issue_ids
