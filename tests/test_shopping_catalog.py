def test_recipe_ingredients_feed_local_product_autocomplete(test_db):
    recipe_id = test_db.recipe_upsert(
        url="https://koch.example/suppe",
        name="Suppe",
        type="Hauptgericht",
        category="Suppe",
        folder_path="C:/recipes/suppe",
        description="Suppe",
        thumb_filename=None,
        video_filename=None,
        source_added_at=None,
    )
    assert test_db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=[
            {"name": "Kartoffeln", "canonical_name": "kartoffel", "amount": 500, "unit": "g"},
            {"name": "Milch", "canonical_name": "milch", "amount": 200, "unit": "ml"},
        ],
        steps=[],
        servings=2,
        auto_tags=[],
    )

    suggestions = test_db.shopping_product_suggestions("kart", 8)
    assert suggestions == [
        {
            "canonical_name": "kartoffel",
            "name": "Kartoffeln",
            "category": "Obst & Gemüse",
            "icon": "🍎",
            "default_unit": "g",
            "usage_count": 0,
        }
    ]


def test_cart_returns_stored_category_and_icon(client):
    response = client.post(
        "/api/cart/add",
        json={"name": "Milch", "amount": 1, "unit": "l", "category": "Kühlregal"},
    )
    assert response.status_code == 200
    item = client.get("/api/cart").json()["items"][0]
    assert item["category"] == "Kühlregal"
    assert item["icon"] == "🥛"
    suggestion = client.get("/api/cart/suggestions", params={"q": "mil"}).json()["items"][0]
    assert suggestion["name"] == "Milch"
    assert suggestion["icon"] == "🥛"
