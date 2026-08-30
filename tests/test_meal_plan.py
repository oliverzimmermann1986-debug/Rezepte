"""Wochenplan: Planung, Portionsskalierung und gemeinsame Einkaufsliste."""

from io import BytesIO

import pdfplumber

from app.db import Database
from tests.conftest import _create_recipe


def _meal_recipe(
    db: Database,
    *,
    name: str,
    folder: str,
    servings: int,
    pasta_grams: float,
) -> int:
    recipe = _create_recipe(db, name=name, folder_path=folder)
    recipe_id = int(recipe["id"])
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Pasta",
                "canonical_name": "pasta",
                "amount": pasta_grams,
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
    db.recipe_set_servings(recipe_id, servings)
    return recipe_id


def test_week_normalizes_to_monday_and_aggregates_scaled_ingredients(
    client,
    test_db: Database,
):
    first = _meal_recipe(
        test_db,
        name="Pasta eins",
        folder="/tmp/meal-one",
        servings=2,
        pasta_grams=100,
    )
    second = _meal_recipe(
        test_db,
        name="Pasta zwei",
        folder="/tmp/meal-two",
        servings=4,
        pasta_grams=200,
    )
    test_db.shopping_exclusion_set("salz", True)

    one = client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": first,
            "planned_servings": 4,
        },
    )
    two = client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-28",
            "recipe_id": second,
            "planned_servings": 2,
        },
    )
    assert one.status_code == 200
    assert two.status_code == 200

    response = client.get("/api/meal-plan?week_start=2026-07-29")
    assert response.status_code == 200
    week = response.json()
    assert week["week_start"] == "2026-07-27"
    assert week["week_end"] == "2026-08-02"
    assert len(week["days"]) == 7
    assert week["summary"] == {
        "planned_meals": 2,
        "planned_days": 2,
        "shopping_items": 1,
    }
    assert week["days"][0]["items"][0]["multiplier"] == 2
    assert week["days"][1]["items"][0]["multiplier"] == 0.5

    pasta = week["shopping_preview"][0]
    assert pasta["canonical_name"] == "nudeln"
    assert pasta["amount"] == 300
    assert pasta["unit"] == "g"
    assert pasta["source_recipe_ids"] == [first, second]


def test_update_delete_and_duplicate_day_recipe_are_deterministic(
    client,
    test_db: Database,
):
    recipe_id = _meal_recipe(
        test_db,
        name="Auflauf",
        folder="/tmp/meal-update",
        servings=2,
        pasta_grams=150,
    )
    payload = {
        "planned_for": "2026-07-27",
        "recipe_id": recipe_id,
        "planned_servings": 2,
    }
    created = client.post("/api/meal-plan/items", json=payload)
    item_id = created.json()["item"]["id"]

    payload["planned_servings"] = 5
    duplicate = client.post("/api/meal-plan/items", json=payload)
    assert duplicate.status_code == 200
    week = client.get("/api/meal-plan?week_start=2026-07-27").json()
    assert week["summary"]["planned_meals"] == 1
    assert week["days"][0]["items"][0]["planned_servings"] == 5

    updated = client.patch(
        f"/api/meal-plan/items/{item_id}",
        json={"planned_servings": 3},
    )
    assert updated.status_code == 200
    assert client.delete(f"/api/meal-plan/items/{item_id}").status_code == 200
    assert client.delete(f"/api/meal-plan/items/{item_id}").status_code == 404


def test_conductor_builds_resource_aware_timeline_without_persisting(
    client,
    test_db: Database,
):
    first = _meal_recipe(
        test_db,
        name="Ofengemuese",
        folder="/tmp/conductor-oven-one",
        servings=2,
        pasta_grams=100,
    )
    second = _meal_recipe(
        test_db,
        name="Auflauf",
        folder="/tmp/conductor-oven-two",
        servings=2,
        pasta_grams=100,
    )
    test_db.recipe_steps_set(first, [
        {"instruction": "Gemüse schneiden"},
        {"instruction": "Im Backofen garen", "timer_seconds": 1_800},
    ])
    test_db.recipe_steps_set(second, [
        {"instruction": "Sauce verrühren"},
        {"instruction": "Auflauf im Ofen backen", "timer_seconds": 1_200},
    ])
    for recipe_id in (first, second):
        response = client.post(
            "/api/meal-plan/items",
            json={
                "planned_for": "2026-07-27",
                "recipe_id": recipe_id,
                "planned_servings": 4,
            },
        )
        assert response.status_code == 200

    response = client.post(
        "/api/meal-plan/conductor/preview",
        json={
            "planned_for": "2026-07-27",
            "serve_at": "19:00",
            "burners": 2,
            "oven_slots": 1,
        },
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["serve_time"] == "19:00"
    assert plan["summary"]["recipes"] == 2
    assert plan["summary"]["steps"] == 4
    assert plan["summary"]["estimated_steps"] == 2
    assert plan["summary"]["resource_adjustments"] >= 1
    assert any("5 Minuten" in warning for warning in plan["warnings"])
    oven_events = [event for event in plan["events"] if event["resource"] == "oven"]
    assert len(oven_events) == 2
    assert (
        oven_events[0]["end_at"] <= oven_events[1]["start_at"]
        or oven_events[1]["end_at"] <= oven_events[0]["start_at"]
    )
    assert test_db.meal_plan_entries("2026-07-27", "2026-07-27")[0][
        "planned_servings"
    ] == 4


def test_conductor_requires_a_planned_recipe(client):
    response = client.post(
        "/api/meal-plan/conductor/preview",
        json={"planned_for": "2026-07-27", "serve_at": "19:00"},
    )

    assert response.status_code == 400
    assert "keine Gerichte" in response.json()["detail"]


def test_week_cart_merges_into_the_one_local_list(
    client,
    test_db: Database,
):
    recipe_id = _meal_recipe(
        test_db,
        name="Wochenpasta",
        folder="/tmp/meal-cart",
        servings=2,
        pasta_grams=250,
    )
    client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": recipe_id,
            "planned_servings": 4,
        },
    )
    test_db.shopping_exclusion_set("salz", True)
    test_db.cart_add_or_merge(
        name="Alter Artikel",
        canonical_name="alt",
        amount=1,
        unit=None,
        source_recipe_id=None,
    )

    response = client.post(
        "/api/meal-plan/cart",
        json={"week_start": "2026-07-27"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "target": "local",
        "added": 1,
        "merged": 0,
        "replaced": False,
        "week_start": "2026-07-27",
    }
    cart = test_db.cart_list()
    assert len(cart) == 2
    by_canonical = {item["canonical_name"]: item for item in cart}
    assert by_canonical["alt"]["amount"] == 1
    assert by_canonical["nudeln"]["amount"] == 500
    assert by_canonical["nudeln"]["unit"] == "g"

    repeated = client.post(
        "/api/meal-plan/cart",
        json={"week_start": "2026-07-27"},
    )
    assert repeated.json()["added"] == 0
    assert repeated.json()["merged"] == 1
    by_canonical = {item["canonical_name"]: item for item in test_db.cart_list()}
    assert by_canonical["nudeln"]["amount"] == 1000


def test_meal_plan_rejects_invalid_input(client):
    assert client.get("/api/meal-plan?week_start=kein-datum").status_code == 422
    assert client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": 999999,
            "planned_servings": 2,
        },
    ).status_code == 404
    assert client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": 1,
            "planned_servings": 0,
        },
    ).status_code == 422


def test_week_pdf_contains_plan_and_shopping_list(client, test_db: Database):
    recipe_id = _meal_recipe(
        test_db,
        name="PDF Zitronenpasta",
        folder="/tmp/meal-pdf",
        servings=2,
        pasta_grams=240,
    )
    test_db.shopping_exclusion_set("salz", True)
    client.post(
        "/api/meal-plan/items",
        json={
            "planned_for": "2026-07-27",
            "recipe_id": recipe_id,
            "planned_servings": 3,
        },
    )

    response = client.get("/api/meal-plan/pdf?week_start=2026-07-27")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="wochenplan-2026-07-27.pdf"'
    )
    assert response.content.startswith(b"%PDF-")

    with pdfplumber.open(BytesIO(response.content)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    assert "Gemeinsam planen" in text
    assert "PDF Zitronenpasta" in text
    assert "Gemeinsame Einkaufsliste" in text
    assert "360 g Pasta" in text
    assert "Salz" not in text
