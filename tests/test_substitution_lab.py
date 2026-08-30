"""Substitutionslabor: Vorschau und sichere, eigenständige Rezeptvariante."""
import json

from app.recipes import manage


def _recipe_with_files(db, root):
    folder = root / "Hauptgericht" / "Test" / "Milchpasta"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({
            "name": "Milchpasta",
            "type": "Hauptgericht",
            "category": "Test",
            "url": "https://example.test/milchpasta",
        }),
        encoding="utf-8",
    )
    (folder / "description.txt").write_text("Milchpasta", encoding="utf-8")
    recipe_id = db.recipe_upsert(
        url="https://example.test/milchpasta",
        name="Milchpasta",
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description="Milchpasta",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    db.recipe_set_extraction_result(recipe_id, "ok", [
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
    db.recipe_steps_set(recipe_id, [{"instruction": "Alles im Topf erhitzen."}])
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
    assert [item["canonical_name"] for item in test_db.recipe_ingredients_get(variant_id)] == [
        "haferdrink", "butter",
    ]
    variant_info = json.loads(
        (root / "Hauptgericht" / "Test" / "Milchpasta_mit_Haferdrink" / "info.json")
        .read_text(encoding="utf-8")
    )
    assert variant_info["variant_of"] == recipe_id


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
