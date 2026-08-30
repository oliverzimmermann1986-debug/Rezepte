"""Substitutionslabor: Vorschau und sichere, eigenständige Rezeptvariante."""
import json

import pytest

from app.recipes import manage
from app.routes import api_recipes


def _recipe_with_files(
    db,
    root,
    *,
    name="Milchpasta",
    ingredients=None,
    description="Milchpasta",
    steps=None,
):
    folder = root / "Hauptgericht" / "Test" / name
    folder.mkdir(parents=True)
    source_url = f"https://example.test/{name.casefold()}"
    (folder / "info.json").write_text(
        json.dumps({
            "name": name,
            "type": "Hauptgericht",
            "category": "Test",
            "url": source_url,
        }),
        encoding="utf-8",
    )
    (folder / "description.txt").write_text(description, encoding="utf-8")
    recipe_id = db.recipe_upsert(
        url=source_url,
        name=name,
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description=description,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    db.recipe_set_extraction_result(recipe_id, "ok", ingredients or [
        {
            "name": "Milch",
            "canonical_name": "milch",
            "amount": 250,
            "unit": "ml",
            "raw": "250 ml Milch",
        },
        {
            "name": "Butter",
            "canonical_name": "butter",
            "amount": 20,
            "unit": "g",
            "raw": "20 g Butter",
        },
    ])
    db.recipe_steps_set(
        recipe_id,
        steps or [{"instruction": "Alles im Topf erhitzen."}],
    )
    with db.conn() as connection:
        connection.execute(
            "UPDATE recipes SET servings=2, user_verified=1, verified_at=1, "
            "verified_by='test', calories_per_serving=450, protein_g=12, "
            "carbs_g=55, fat_g=18, nutrition_computed_at=1 WHERE id=?",
            (recipe_id,),
        )
    return recipe_id, folder


def test_substitution_creates_reviewable_variant_and_preserves_original(
    client, test_db, tmp_path, monkeypatch
):
    root = tmp_path / "recipes"
    recipe_id, original_folder = _recipe_with_files(test_db, root)
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    preview = client.get(f"/api/recipes/{recipe_id}/substitutions")

    assert preview.status_code == 200, preview.text
    lab = preview.json()
    assert lab["automatic_apply"] is False
    assert lab["medical_safety_claim"] is False
    milk = next(item for item in lab["items"] if item["canonical_name"] == "milch")
    candidate = next(
        item for item in milk["candidates"] if item["id"] == "milk-oat-drink"
    )
    assert candidate["requires_review"] is True
    assert candidate["result_ingredient"] == {
        "name": "Haferdrink",
        "canonical_name": "haferdrink",
        "amount": 250.0,
        "unit": "ml",
        "raw": "250 ml Haferdrink",
    }

    applied = client.post(
        f"/api/recipes/{recipe_id}/substitutions/apply",
        json={
            "ingredient_id": milk["ingredient_id"],
            "candidate_id": candidate["id"],
            "variant_name": "Milchpasta mit Haferdrink",
        },
    )

    assert applied.status_code == 200, applied.text
    result = applied.json()
    variant_id = result["recipe_id"]
    assert result["substitution"]["review_required"] is True
    assert result["substitution"]["nutrition_invalidated"] is True
    assert result["substitution"]["result_ingredient"] == candidate["result_ingredient"]
    original = test_db.recipe_get(recipe_id)
    variant = test_db.recipe_get(variant_id)
    assert original["folder_path"] == str(original_folder)
    assert original["user_verified"] == 1
    assert original["calories_per_serving"] == 450
    assert [item["canonical_name"] for item in test_db.recipe_ingredients_get(recipe_id)] == [
        "milch", "butter",
    ]
    assert variant["url"] is None
    assert variant["user_verified"] == 0
    assert variant["calories_per_serving"] is None
    assert variant["nutrition_computed_at"] is None
    assert variant["description"] == "Milchpasta"
    assert [item["canonical_name"] for item in test_db.recipe_ingredients_get(variant_id)] == [
        "haferdrink", "butter",
    ]
    variant_info = json.loads(
        (root / "Hauptgericht" / "Test" / "Milchpasta_mit_Haferdrink" / "info.json")
        .read_text(encoding="utf-8")
    )
    assert variant_info["variant_of"] == recipe_id
    assert variant_info["variant_provenance"]["candidate_id"] == "milk-oat-drink"
    assert variant_info["variant_provenance"]["result_ingredient"] == candidate["result_ingredient"]
    assert variant_info["variant_provenance"]["functional_effect"] == candidate["functional_effect"]
    assert variant_info["variant_provenance"]["allergen_notes"] == candidate["allergen_notes"]
    assert variant_info["variant_review_notice"] == result["substitution"]["review_notice"]
    assert (
        root / "Hauptgericht" / "Test" / "Milchpasta_mit_Haferdrink" / "description.txt"
    ).read_text(encoding="utf-8") == "Milchpasta"
    variant_steps = test_db.recipe_steps_get(variant_id)
    assert [step["instruction"] for step in variant_steps] == ["Alles im Topf erhitzen."]
    assert [
        step["instruction"] for step in test_db.recipe_steps_get(recipe_id)
    ] == ["Alles im Topf erhitzen."]
    monkeypatch.setattr(
        api_recipes,
        "_safe_recipe_folder",
        lambda recipe: type(original_folder)(str(recipe["folder_path"])).resolve(),
    )
    detail = client.get(f"/api/recipes/{variant_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["variant_review_notice"] == result["substitution"]["review_notice"]
    assert detail.json()["variant_provenance"]["candidate_id"] == "milk-oat-drink"


def test_substitution_rejects_candidate_from_another_ingredient(
    client, test_db, tmp_path, monkeypatch
):
    root = tmp_path / "recipes"
    recipe_id, _folder = _recipe_with_files(test_db, root)
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())
    milk = test_db.recipe_ingredients_get(recipe_id)[0]

    response = client.post(
        f"/api/recipes/{recipe_id}/substitutions/apply",
        json={
            "ingredient_id": milk["id"],
            "candidate_id": "egg-applesauce",
            "variant_name": "Unzulässige Variante",
        },
    )

    assert response.status_code == 400
    assert test_db.recipe_count() == 1


@pytest.mark.parametrize(
    ("amount", "unit", "expected_amount"),
    [(2, "Stück", 120.0), (1, None, 60.0)],
    ids=["pieces", "unitless-count"],
)
def test_egg_applesauce_preview_and_apply_use_60_grams_per_piece(
    client, test_db, tmp_path, monkeypatch, amount, unit, expected_amount
):
    root = tmp_path / "recipes"
    recipe_id, _folder = _recipe_with_files(
        test_db,
        root,
        name="Eierkuchen",
        ingredients=[{
            "name": "Eier",
            "canonical_name": "eier",
            "amount": amount,
            "unit": unit,
            "raw": f"{amount} Eier",
        }],
        description="2 Eier unter den Teig rühren.",
        steps=[{"instruction": "Die Eier schaumig schlagen."}],
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    preview = client.get(f"/api/recipes/{recipe_id}/substitutions")

    assert preview.status_code == 200, preview.text
    egg = preview.json()["items"][0]
    candidate = next(item for item in egg["candidates"] if item["id"] == "egg-applesauce")
    assert candidate["result_ingredient"] == {
        "name": "Apfelmus",
        "canonical_name": "apfelmus",
        "amount": expected_amount,
        "unit": "g",
        "raw": f"{expected_amount:g} g Apfelmus",
    }

    applied = client.post(
        f"/api/recipes/{recipe_id}/substitutions/apply",
        json={
            "ingredient_id": egg["ingredient_id"],
            "candidate_id": "egg-applesauce",
            "variant_name": "Eierkuchen mit Apfelmus",
        },
    )

    assert applied.status_code == 200, applied.text
    result = applied.json()["substitution"]["result_ingredient"]
    assert result == candidate["result_ingredient"]
    stored = test_db.recipe_ingredients_get(applied.json()["recipe_id"])
    assert stored[0]["amount"] == expected_amount
    assert stored[0]["unit"] == "g"
    assert stored[0]["raw"] == f"{expected_amount:g} g Apfelmus"


@pytest.mark.parametrize(
    ("amount", "unit", "raw", "detail"),
    [
        (100, "g", "100 g Ei", "Stück-/Anzahlmengen"),
        (None, "Stück", "Ei", "Ausgangsmenge"),
    ],
    ids=["mass-unit", "missing-amount"],
)
def test_egg_applesauce_rejects_incompatible_or_missing_amounts(
    client, test_db, tmp_path, monkeypatch, amount, unit, raw, detail
):
    root = tmp_path / "recipes"
    recipe_id, _folder = _recipe_with_files(
        test_db,
        root,
        name="Ungeeignetes Ei",
        ingredients=[{
            "name": "Ei",
            "canonical_name": "ei",
            "amount": amount,
            "unit": unit,
            "raw": raw,
        }],
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())
    ingredient = test_db.recipe_ingredients_get(recipe_id)[0]

    preview = client.get(f"/api/recipes/{recipe_id}/substitutions")
    assert preview.status_code == 200
    assert all(
        candidate["id"] != "egg-applesauce"
        for item in preview.json()["items"]
        for candidate in item["candidates"]
    )

    applied = client.post(
        f"/api/recipes/{recipe_id}/substitutions/apply",
        json={
            "ingredient_id": ingredient["id"],
            "candidate_id": "egg-applesauce",
            "variant_name": "Darf nicht entstehen",
        },
    )

    assert applied.status_code == 400
    assert detail in applied.json()["detail"]
    assert test_db.recipe_count() == 1
    assert all(
        item.get("raw") != "g Apfelmus"
        for item in test_db.recipe_ingredients_get(recipe_id)
    )


@pytest.mark.parametrize(
    ("source_name", "source_canonical", "candidate_id", "blocked_tag"),
    [
        ("Milch", "milch", "milk-oat-drink", "glutenfrei"),
        ("Joghurt", "joghurt", "yogurt-plant-yogurt", "nussfrei"),
    ],
    ids=["oat-drink-gluten", "plant-yogurt-nuts"],
)
def test_product_dependent_risks_remove_manual_claims_and_block_auto_tags(
    client,
    test_db,
    tmp_path,
    monkeypatch,
    source_name,
    source_canonical,
    candidate_id,
    blocked_tag,
):
    root = tmp_path / "recipes"
    ingredients = [{
        "name": source_name,
        "canonical_name": source_canonical,
        "amount": 200,
        "unit": "g",
        "raw": f"200 g {source_name}",
    }]
    ingredients.extend({
        "name": name.title(),
        "canonical_name": name,
        "amount": 1,
        "unit": "Stück",
        "raw": name,
    } for name in ("reis", "tomate", "zwiebel", "salz", "olivenöl"))
    recipe_id, _folder = _recipe_with_files(
        test_db,
        root,
        name=f"Risiko {source_name}",
        ingredients=ingredients,
    )
    test_db.recipe_tags_set(recipe_id, ["Familie", blocked_tag])
    test_db.recipe_auto_tags_set(recipe_id, ["schnell"])
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())
    source = test_db.recipe_ingredients_get(recipe_id)[0]

    response = client.post(
        f"/api/recipes/{recipe_id}/substitutions/apply",
        json={
            "ingredient_id": source["id"],
            "candidate_id": candidate_id,
            "variant_name": f"Sichere Variante {source_name}",
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert blocked_tag in result["substitution"]["blocked_auto_tags"]
    assert blocked_tag in result["substitution"]["removed_manual_safety_tags"]
    variant_tags = {
        tag["name"].casefold(): tag["auto"]
        for tag in test_db.recipe_tags_get(result["recipe_id"])
    }
    assert blocked_tag not in variant_tags
    assert variant_tags["familie"] == 0
    assert variant_tags["schnell"] == 1
    original_tags = {
        tag["name"].casefold(): tag["auto"]
        for tag in test_db.recipe_tags_get(recipe_id)
    }
    assert original_tags[blocked_tag] == 0
