from __future__ import annotations

import json

from app.core.analyzer import OpenAIAnalyzer
from app.db import CURRENT_SCHEMA_VERSION, Database
from app.recipes.auto_tags import (
    backfill_diet_auto_tags,
    compute_diet_tags,
)
from app.recipes.video_recipe_extract import _merge_missing
from tests.conftest import _create_recipe


SAFE_INGREDIENTS = ["reis", "tomate", "zwiebel", "salz", "olivenöl"]


def _insert_ingredients(db, recipe_id: int, names: list[str]) -> None:
    with db.conn() as connection:
        connection.executemany(
            "INSERT INTO recipe_ingredients "
            "(recipe_id, name, canonical_name, amount, unit, sort_order) "
            "VALUES (?, ?, ?, 1, 'Stück', ?)",
            [
                (recipe_id, name.title(), name, index)
                for index, name in enumerate(names)
            ],
        )


def test_allergen_info_is_a_second_safety_gate() -> None:
    tags = compute_diet_tags(
        SAFE_INGREDIENTS,
        allergen_info={
            "gluten": "unklar",
            "lactose": "frei",
            "egg": "frei",
            "nuts": "enthält",
        },
    )

    assert "laktosefrei" in tags
    assert "eifrei" in tags
    assert "glutenfrei" not in tags
    assert "nussfrei" not in tags

    # Eine erkannte Quelle kann durch die KI niemals überstimmt werden.
    with_flour = compute_diet_tags(
        [*SAFE_INGREDIENTS[:-1], "weizenmehl type 405"],
        allergen_info={
            "gluten": "frei",
            "lactose": "frei",
            "egg": "frei",
            "nuts": "frei",
        },
    )
    assert "glutenfrei" not in with_flour


def test_backfill_preserves_manual_and_style_tags(test_db) -> None:
    recipe = _create_recipe(test_db, name="Mandel-Reis", folder_path="/tmp/allergen")
    recipe_id = int(recipe["id"])
    _insert_ingredients(
        test_db,
        recipe_id,
        ["reis", "tomate", "zwiebel", "salz", "mandeln"],
    )
    test_db.recipe_tags_set(recipe_id, ["glutenfrei"])
    test_db.recipe_auto_tags_set(recipe_id, ["schnell", "nussfrei"])

    result = backfill_diet_auto_tags(test_db)
    tags = {tag["name"]: tag["auto"] for tag in test_db.recipe_tags_get(recipe_id)}

    assert result["recipes_checked"] == 1
    assert result["recipes_changed"] == 1
    assert tags["schnell"] == 1
    assert tags["glutenfrei"] == 0
    assert "nussfrei" not in tags
    assert tags["laktosefrei"] == 1
    assert tags["eifrei"] == 1


def test_backfill_skips_positive_allergen_claims_for_short_lists(test_db) -> None:
    recipe = _create_recipe(test_db, name="Kurze Liste", folder_path="/tmp/short")
    _insert_ingredients(test_db, int(recipe["id"]), ["reis", "salz", "wasser"])

    result = backfill_diet_auto_tags(test_db)
    tags = {tag["name"] for tag in test_db.recipe_tags_get(int(recipe["id"]))}

    assert result["skipped_too_few_ingredients"] == 1
    assert not tags.intersection({"glutenfrei", "laktosefrei", "eifrei", "nussfrei"})


def test_allergen_backfill_endpoint_is_repeatable(client, test_db) -> None:
    recipe = _create_recipe(test_db, name="Reisgericht", folder_path="/tmp/reis")
    _insert_ingredients(test_db, int(recipe["id"]), SAFE_INGREDIENTS)

    first = client.post("/api/recipes/allergens/backfill")
    second = client.post("/api/recipes/allergens/backfill")

    assert first.status_code == 200
    assert first.json()["recipes_changed"] == 1
    assert second.status_code == 200
    assert second.json()["recipes_changed"] == 0
    assert first.json()["assigned"] == {
        "laktosefrei": 1,
        "glutenfrei": 1,
        "eifrei": 1,
        "nussfrei": 1,
    }


def test_schema_migration_backfills_existing_recipes_and_creates_backup(tmp_path) -> None:
    db_path = tmp_path / "recipes.db"
    db = Database(db_path)
    recipe = _create_recipe(db, name="Altbestand", folder_path="/tmp/old")
    recipe_id = int(recipe["id"])
    _insert_ingredients(db, recipe_id, SAFE_INGREDIENTS)
    with db.conn() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version>=240")

    migrated = Database(db_path)

    tags = {tag["name"] for tag in migrated.recipe_tags_get(recipe_id)}
    assert {"glutenfrei", "laktosefrei", "eifrei", "nussfrei"} <= tags
    with migrated.conn() as connection:
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=240"
        ).fetchone()
    assert migration["name"] == "backfill_allergen_free_tags"
    assert list((tmp_path / "backups").glob(
        f"pre-migration-v230-to-v{CURRENT_SCHEMA_VERSION}-*.db"
    ))


def test_new_recipe_prompt_requests_conservative_allergen_info() -> None:
    analyzer = OpenAIAnalyzer.__new__(OpenAIAnalyzer)
    captured = {}

    def fake_call(system, _user):
        captured["system"] = system
        return json.dumps(
            {
                "ingredients": [],
                "steps": [],
                "servings": None,
                "tags": [],
                "allergen_info": {
                    "gluten": "frei",
                    "lactose": "enthält",
                    "egg": "vielleicht",
                },
            },
            ensure_ascii=False,
        )

    analyzer._call = fake_call
    result = analyzer.analyze_recipe_content("Vollständiger Rezepttext")

    assert "Niemals raten; bei Zweifel immer unklar" in captured["system"]
    assert "gluten, lactose, egg und nuts" in captured["system"]
    assert result["allergen_info"] == {
        "gluten": "frei",
        "lactose": "enthält",
        "egg": "unklar",
        "nuts": "unklar",
    }


def test_video_merge_keeps_stronger_allergen_warning() -> None:
    merged = _merge_missing(
        {
            "ingredients": [],
            "steps": [],
            "servings": None,
            "tags": [],
            "allergen_info": {
                "gluten": "frei",
                "lactose": "frei",
                "egg": "frei",
                "nuts": "frei",
            },
        },
        {
            "ingredients": [],
            "steps": [],
            "servings": None,
            "tags": [],
            "allergen_info": {
                "gluten": "enthält",
                "lactose": "unklar",
                "egg": "frei",
                "nuts": "frei",
            },
        },
    )

    assert merged["allergen_info"] == {
        "gluten": "enthält",
        "lactose": "unklar",
        "egg": "frei",
        "nuts": "frei",
    }
