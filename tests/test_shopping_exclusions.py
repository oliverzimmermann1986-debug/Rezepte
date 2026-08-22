from pathlib import Path

import pytest

from app.db import Database


def _recipe_with_ingredients(db: Database, tmp_path: Path) -> int:
    folder = tmp_path / "pasta"
    folder.mkdir()
    recipe_id = db.recipe_upsert(
        url="https://example.test/pasta",
        name="Pasta",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Ein Testrezept",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Pasta",
                "canonical_name": "pasta",
                "amount": 250,
                "unit": "g",
            },
            {
                "name": "Salz",
                "canonical_name": "salz",
                "amount": 1,
                "unit": "Prise",
            },
        ],
    )
    return recipe_id


def test_admin_can_exclude_ingredient_from_shopping(
    client,
    test_db: Database,
    tmp_path: Path,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)

    response = client.put(
        "/api/master/canonicals/salz/shopping-exclusion",
        json={"excluded": True},
    )
    assert response.status_code == 200
    assert test_db.shopping_excluded_canonicals() == {"salz"}

    canonicals = client.get("/api/master/canonicals").json()["canonicals"]
    salt = next(item for item in canonicals if item["canonical_name"] == "salz")
    assert salt["shopping_excluded"] == 1

    cooked = client.post(f"/api/cart/cook/{recipe_id}")
    assert cooked.status_code == 200
    assert cooked.json()["target"] == "local"
    assert cooked.json()["skipped"] == 1
    assert [item["canonical_name"] for item in test_db.cart_list()] == ["pasta"]


def test_canonical_rename_keeps_shopping_exclusion(
    client,
    test_db: Database,
    tmp_path: Path,
):
    _recipe_with_ingredients(test_db, tmp_path)
    test_db.shopping_exclusion_set("salz", True)

    response = client.post(
        "/api/master/canonicals/rename",
        json={
            "old_canonical": "salz",
            "new_canonical": "meersalz",
            "update_names": False,
        },
    )
    assert response.status_code == 200
    assert test_db.shopping_excluded_canonicals() == {"meersalz"}


def test_cook_scales_ingredients_to_selected_servings(
    client,
    test_db: Database,
    tmp_path: Path,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)
    test_db.recipe_set_servings(recipe_id, 4)

    response = client.post(
        f"/api/cart/cook/{recipe_id}",
        json={"servings": 6, "multiplier": 1.5},
    )

    assert response.status_code == 200
    assert response.json()["servings"] == 6
    assert response.json()["multiplier"] == 1.5
    cart = {item["canonical_name"]: item for item in test_db.cart_list()}
    assert cart["pasta"]["amount"] == 375
    assert cart["salz"]["amount"] == 1.5


def test_cook_with_servings_requires_recipe_base_servings(
    client,
    test_db: Database,
    tmp_path: Path,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)

    response = client.post(
        f"/api/cart/cook/{recipe_id}",
        json={"servings": 3},
    )

    assert response.status_code == 409
    assert "Portionszahl im Rezept fehlt" in response.json()["detail"]
    assert test_db.cart_list() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"servings": 0},
        {"servings": 51},
        {"multiplier": 0},
        {"multiplier": 101},
    ],
)
def test_cook_rejects_invalid_scaling(
    client,
    test_db: Database,
    tmp_path: Path,
    payload: dict,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)
    test_db.recipe_set_servings(recipe_id, 4)

    response = client.post(f"/api/cart/cook/{recipe_id}", json=payload)

    assert response.status_code == 422
    assert test_db.cart_list() == []


def test_cook_rejects_ambiguous_scaling_payload(
    client,
    test_db: Database,
    tmp_path: Path,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)
    test_db.recipe_set_servings(recipe_id, 4)

    response = client.post(
        f"/api/cart/cook/{recipe_id}",
        json={"servings": 6, "multiplier": 2},
    )

    assert response.status_code == 400
    assert "passen nicht zusammen" in response.json()["detail"]
    assert test_db.cart_list() == []


def test_cook_keeps_legacy_multiplier_support(
    client,
    test_db: Database,
    tmp_path: Path,
):
    recipe_id = _recipe_with_ingredients(test_db, tmp_path)

    response = client.post(
        f"/api/cart/cook/{recipe_id}",
        json={"multiplier": 0.5},
    )

    assert response.status_code == 200
    assert response.json()["servings"] is None
    assert response.json()["multiplier"] == 0.5
    cart = {item["canonical_name"]: item for item in test_db.cart_list()}
    assert cart["pasta"]["amount"] == 125
