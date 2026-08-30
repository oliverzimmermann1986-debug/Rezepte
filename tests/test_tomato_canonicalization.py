import json

import pytest

from app.core.analyzer import OpenAIAnalyzer
from app.db import Database
from app.recipes.canonical import canonical_name
from app.recipes.cart_logic import prepare_for_cart


@pytest.mark.parametrize(
    "name",
    [
        "Tomate",
        "Tomaten",
        "Cherrytomate",
        "Cherry Tomaten",
        "Cherry-Tomaten",
        "Cocktailtomate",
        "Cocktail Tomaten",
        "Cocktail-Tomaten",
        "Kirschtomaten",
    ],
)
def test_fresh_tomato_variants_share_one_canonical(name):
    assert canonical_name(name) == "tomate"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tomatenmark", "tomatenmark"),
        ("passierte Tomaten", "passierte tomaten"),
        ("Dosentomaten", "dosentomaten"),
    ],
)
def test_processed_tomato_products_stay_separate(name, expected):
    assert canonical_name(name) == expected


def test_fresh_tomato_variants_use_generic_shopping_name():
    prepared = prepare_for_cart("Cocktailtomaten", 250, "g")

    assert prepared["name"] == "Tomaten"
    assert prepared["canonical_name"] == "tomate"


def test_migration_merges_existing_tomato_data(tmp_path):
    db_path = tmp_path / "recipes.db"
    db = Database(db_path)
    recipe_id = db.recipe_upsert(
        url="https://example.test/tomatoes",
        name="Tomatensalat",
        type="Salat",
        category="Salat",
        folder_path=str(tmp_path / "tomatoes"),
        description="Tomaten",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Cherrytomaten",
                "canonical_name": "cherrytomate",
                "amount": 250,
                "unit": "g",
            },
        ],
    )

    with db.conn() as connection:
        connection.execute(
            "INSERT INTO shopping_cart "
            "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Tomaten", "tomate", 100, "g", 1, 1.0, json.dumps([recipe_id])),
        )
        connection.execute(
            "INSERT INTO shopping_cart "
            "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "Cocktailtomaten",
                "cocktailtomate",
                150,
                "g",
                0,
                2.0,
                json.dumps([recipe_id + 1]),
            ),
        )
        connection.execute(
            "INSERT INTO shopping_exclusions(canonical_name, created_at) VALUES (?, ?)",
            ("cherrytomate", 1.0),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version=130")

    migrated = Database(db_path)

    with migrated.conn() as connection:
        ingredient = connection.execute(
            "SELECT canonical_name FROM recipe_ingredients WHERE recipe_id=?",
            (recipe_id,),
        ).fetchone()
    assert ingredient["canonical_name"] == "tomate"

    cart = migrated.cart_list()
    assert len(cart) == 1
    assert cart[0]["name"] == "Tomaten"
    assert cart[0]["canonical_name"] == "tomate"
    assert cart[0]["amount"] == 250
    assert cart[0]["checked"] == 0
    assert json.loads(cart[0]["source_recipe_ids"]) == [recipe_id, recipe_id + 1]
    assert migrated.shopping_excluded_canonicals() == {"tomate"}


def test_extraction_prompt_preserves_fresh_variety_but_merges_for_shopping():
    analyzer = OpenAIAnalyzer.__new__(OpenAIAnalyzer)
    captured = {}

    def fake_call(system, user):
        captured["system"] = system
        return (
            '{"ingredients":[],"steps":[],"servings":null,"tags":[]}'
        )

    analyzer._call = fake_call
    analyzer.analyze_recipe_content(
        "250 g Cherrytomaten",
        existing_canonical=["Tomaten", "Basmati-Reis", "Crème fraîche"],
    )

    prompt = captured["system"]
    assert "Cherrytomate" in prompt
    assert "Cocktailtomate" in prompt
    assert "gemeinsam als 'tomate' normalisiert" in prompt
    assert "Tomatenmark" in prompt
    assert "BESTEHENDE ZUTATEN in der DB — ZUERST WIEDERVERWENDEN" in prompt
    assert "MUSS name exakt die bestehende Schreibweise" in prompt
    assert "Basmati-Reis" in prompt
    assert "Crème fraîche" in prompt
