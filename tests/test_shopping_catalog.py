import pytest

from app.db import Database
from app.recipes.shopping_catalog import (
    infer_shopping_category,
    is_shopping_catalog_candidate,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Burrata", "Kühlregal"),
        ("Ei", "Vorrat & Konserven"),
        ("Eier", "Vorrat & Konserven"),
        ("Eigelb", "Vorrat & Konserven"),
        ("Eierspätzle", "Vorrat & Konserven"),
        ("Eisbergsalat", "Obst & Gemüse"),
        ("Eis", "Tiefkühl"),
        ("TK Spinat", "Tiefkühl"),
        ("Kartoffelgewürz", "Vorrat & Konserven"),
        ("Avocadoöl", "Vorrat & Konserven"),
        ("Passierte Tomate", "Vorrat & Konserven"),
        ("Fischstäbchen", "Tiefkühl"),
        ("Rahmspinat", "Tiefkühl"),
        ("Hartkäse", "Kühlregal"),
        ("Kräuter-Crème-fraîche", "Kühlregal"),
        ("Frühlingszwiebel", "Obst & Gemüse"),
        ("Burgerbrötchen", "Bäckerei"),
        ("Hähnchenbrustfilet", "Fleisch & Fisch"),
        ("Mineralwasser", "Getränke"),
    ],
)
def test_category_inference_uses_whole_product_words(name, expected):
    assert infer_shopping_category(name) == expected


def test_catalog_excludes_cooking_water_but_keeps_bottled_water():
    assert is_shopping_catalog_candidate("Pastawasser", "pastawasser") is False
    assert is_shopping_catalog_candidate("Wasser", "wasser") is False
    assert is_shopping_catalog_candidate("Mineralwasser", "mineralwasser") is True


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


def test_short_autocomplete_only_matches_product_or_word_prefix(test_db):
    for name, canonical in (
        ("Ei", "ei"),
        ("Eierspätzle", "eierspätzle"),
        ("Weißer Spargel", "weißer spargel"),
        ("Hackfleisch", "hackfleisch"),
        ("Reis", "reis"),
    ):
        test_db.cart_add_or_merge(
            name=name,
            canonical_name=canonical,
            amount=None,
            unit=None,
            source_recipe_id=None,
        )

    names = [item["name"] for item in test_db.shopping_product_suggestions("ei", 8)]

    assert names == ["Ei", "Eierspätzle"]


def test_catalog_rebuild_aggregates_all_active_recipe_ingredients(test_db):
    for index, (display_name, canonical, unit) in enumerate(
        (
            ("Basmati Reis", "basmati reis", "g"),
            ("Basmati-Reis", "basmati-reis", "g"),
            ("Kartoffelgewürz", "kartoffelgewürz", "TL"),
            ("Wasser", "wasser", "ml"),
            ("Zwiebel", "zwiebel", "kleine"),
        )
    ):
        recipe_id = test_db.recipe_upsert(
            url=f"https://koch.example/katalog-{index}",
            name=f"Katalog {index}",
            type="Hauptgericht",
            category="Test",
            folder_path=f"C:/recipes/katalog-{index}",
            description="Test",
            thumb_filename=None,
            video_filename=None,
            source_added_at=None,
        )
        test_db.recipe_set_extraction_result(
            recipe_id,
            "ok",
            [
                {
                    "name": display_name,
                    "canonical_name": canonical,
                    "amount": 1,
                    "unit": unit,
                }
            ],
        )

    summary = test_db.shopping_catalog_rebuild()
    rice = test_db.shopping_product_suggestions("basmati", 8)
    seasoning = test_db.shopping_product_suggestions("kartoffelgew", 8)[0]

    assert summary["ingredient_rows"] == 5
    assert summary["products"] == 3
    assert summary["unassigned"] == 0
    assert [item["canonical_name"] for item in rice] == ["basmatireis"]
    assert rice[0]["default_unit"] == "g"
    assert rice[0]["category"] == "Vorrat & Konserven"
    assert seasoning["category"] == "Vorrat & Konserven"
    assert test_db.shopping_product_suggestions("wasser", 8) == []
    onion = test_db.shopping_product_suggestions("zwiebel", 8)[0]
    assert onion["default_unit"] is None
    with test_db.conn() as connection:
        recipe_count = connection.execute(
            "SELECT recipe_count FROM shopping_products WHERE canonical_name='basmatireis'"
        ).fetchone()[0]
    assert recipe_count == 2


def test_category_migration_repairs_known_legacy_values(tmp_path):
    path = tmp_path / "shopping-repair.db"
    database = Database(path)
    database.cart_add_or_merge(
        name="Burrata",
        canonical_name="burrata",
        amount=1,
        unit="Stück",
        source_recipe_id=None,
        category="Sonstiges",
    )
    database.cart_add_or_merge(
        name="Ei",
        canonical_name="ei",
        amount=6,
        unit="Stück",
        source_recipe_id=None,
        category="Kühlregal",
    )
    with database.conn() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=250")

    migrated = Database(path)
    cart_categories = {item["name"]: item["category"] for item in migrated.cart_list()}
    suggestions = {
        item["name"]: item["category"]
        for item in migrated.shopping_product_suggestions("", 25)
    }

    assert cart_categories == {
        "Burrata": "Kühlregal",
        "Ei": "Vorrat & Konserven",
    }
    assert suggestions["Burrata"] == "Kühlregal"
    assert suggestions["Ei"] == "Vorrat & Konserven"


def test_ingredient_cleanup_migration_recanonicalizes_existing_rows(tmp_path):
    path = tmp_path / "ingredient-cleanup.db"
    database = Database(path)
    recipe_id = database.recipe_upsert(
        url="https://koch.example/cleanup",
        name="Bereinigung",
        type="Hauptgericht",
        category="Test",
        folder_path="C:/recipes/cleanup",
        description="Test",
        thumb_filename=None,
        video_filename=None,
        source_added_at=None,
    )
    database.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "  Basmati   Reis ",
                "canonical_name": "basmati reis",
                "amount": 250,
                "unit": "kleine",
            },
            {
                "name": " Crème   fraîche ",
                "canonical_name": "créme fraîche",
                "amount": 1,
                "unit": "BECHER",
            },
        ],
    )
    with database.conn() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=260")

    migrated = Database(path)
    ingredients = migrated.recipe_ingredients_get(recipe_id)

    assert [item["name"] for item in ingredients] == [
        "Basmati Reis",
        "Crème fraîche",
    ]
    assert [item["canonical_name"] for item in ingredients] == [
        "basmatireis",
        "creme fraiche",
    ]
    assert [item["unit"] for item in ingredients] == [None, "Becher"]
    assert {"Basmati Reis", "Crème fraîche"} <= set(
        migrated.ingredient_name_hints()
    )
    with migrated.conn() as connection:
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=260"
        ).fetchone()
    assert migration["name"] == "clean_recipe_ingredients_and_rebuild_catalog"
    assert list((tmp_path / "backups").glob("pre-migration-v250-to-v260-*.db"))
