from pathlib import Path

import pymupdf

from app.db import Database
from app.recipes.pdf_recipe_extract import (
    ExtractedRecipeData,
    apply_extracted_recipe_data,
    extract_pdf_text,
    extract_recipe_data,
    parse_ingredient_lines,
)


def _recipe(db: Database, tmp_path: Path) -> int:
    folder = tmp_path / "PDF-Rezept"
    folder.mkdir()
    return db.recipe_upsert(
        url="mail-attachment://recipe.pdf", name="PDF Rezept",
        type="Hauptgericht", category="Test", folder_path=str(folder),
        description=None, thumb_filename=None, video_filename=None,
        source_added_at=1.0,
    )


def test_local_pdf_ingredient_parser_reads_amounts_units_and_section():
    text = """Kartoffelsuppe

Zutaten:
- 500 g Kartoffeln
- 1,5 l Gemüsebrühe
- 2 Stück Zwiebeln
- Salz nach Geschmack

Zubereitung:
Alles 25 Minuten kochen.
"""
    items = parse_ingredient_lines(text)
    assert [item["canonical_name"] for item in items[:3]] == [
        "kartoffel", "brühe", "zwiebel",
    ]
    assert items[0]["amount"] == 500
    assert items[0]["unit"] == "g"
    assert items[1]["amount"] == 1.5
    assert items[1]["unit"] == "l"
    assert any(item["name"].casefold().startswith("salz") for item in items)


def test_pdf_text_layer_is_read_for_recipe_extraction():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Zutaten:\n250 g Mehl\n2 Stück Eier\nZubereitung:")
    data = doc.tobytes(); doc.close()
    text = extract_pdf_text(data)
    assert "250 g Mehl" in text
    assert len(parse_ingredient_lines(text)) == 2


def test_ai_enriches_local_pdf_extraction():
    class FakeAnalyzer:
        def analyze_recipe_content(self, text, **kwargs):
            return {
                "ingredients": [
                    {"name": "Mehl", "amount": 250, "unit": "g", "raw": "250 g Mehl"},
                    {"name": "Ei", "amount": 2, "unit": "Stück", "raw": "2 Eier"},
                ],
                "steps": [{"instruction": "Teig verrühren.", "timer_seconds": None}],
                "servings": 4,
                "tags": ["backen"],
            }

    result = extract_recipe_data(
        "Zutaten:\n250 g Mehl\n2 Eier\nZubereitung:\nTeig verrühren.",
        analyzer=FakeAnalyzer(),
    )
    assert result.method in {"ai", "ai+local"}
    assert len(result.ingredients) == 2
    assert result.steps[0]["instruction"] == "Teig verrühren."
    assert result.servings == 4


def test_pdf_recipe_data_only_fills_missing_fields_by_default(test_db: Database, tmp_path: Path):
    recipe_id = _recipe(test_db, tmp_path)
    test_db.recipe_set_extraction_result(recipe_id, "ok", [{
        "name": "Alte Zutat", "canonical_name": "alte zutat", "amount": 1,
        "unit": "Stück", "raw": "1 Alte Zutat",
    }])
    data = ExtractedRecipeData(
        text="Neue PDF-Beschreibung",
        ingredients=[{"name": "Mehl", "canonical_name": "mehl", "amount": 250, "unit": "g", "raw": "250 g Mehl"}],
        steps=[{"instruction": "Verrühren", "timer_seconds": None}],
        servings=4,
        tags=["backen"],
        method="local",
    )
    result = apply_extracted_recipe_data(test_db, recipe_id, data, actor="test", overwrite=False)
    assert result["ok"] is True
    assert result["skipped_existing"]["ingredients"] is True
    assert test_db.recipe_ingredients_get(recipe_id)[0]["name"] == "Alte Zutat"
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Verrühren"
    assert test_db.recipe_get(recipe_id)["servings"] == 4


def test_pdf_recipe_overwrite_creates_version_and_replaces_data(test_db: Database, tmp_path: Path):
    recipe_id = _recipe(test_db, tmp_path)
    test_db.recipe_set_extraction_result(recipe_id, "ok", [{
        "name": "Alt", "canonical_name": "alt", "amount": 1, "unit": None, "raw": "Alt",
    }])
    data = ExtractedRecipeData(
        text="Zutaten:\n300 g Mehl",
        ingredients=[{"name": "Mehl", "canonical_name": "mehl", "amount": 300, "unit": "g", "raw": "300 g Mehl"}],
        method="ai",
    )
    result = apply_extracted_recipe_data(test_db, recipe_id, data, actor="test", overwrite=True)
    assert result["changed"] is True
    assert test_db.recipe_ingredients_get(recipe_id)[0]["canonical_name"] == "mehl"
    versions = test_db.recipe_versions_list(recipe_id=recipe_id)
    assert len(versions) == 1
    assert versions[0]["source"] == "pdf"


def test_admin_pdf_batch_applies_ingredients_to_existing_recipe(test_db: Database, tmp_path: Path, monkeypatch):
    import app.routes.api_admin as admin_api

    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Test" / "PDF-Rezept"
    folder.mkdir(parents=True)
    recipe_id = test_db.recipe_upsert(
        url="mail-attachment://batch.pdf", name="PDF Rezept", type="Hauptgericht",
        category="Test", folder_path=str(folder), description=None,
        thumb_filename=None, video_filename=None, source_added_at=1.0,
    )
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_text((72, 80), "Zutaten:\n500 g Kartoffeln\n1 l Bruehe\n2 Stueck Zwiebeln\nZubereitung:")
    pdf_path = folder / "PDF-Rezept.pdf"
    pdf_path.write_bytes(doc.tobytes()); doc.close()

    class FakeConfig:
        def get(self, section, key=None, default=None):
            values = {
                ("paths", "recipe_dir"): str(root),
                ("paths", "data_dir"): str(tmp_path / "data"),
                ("pdf", None): {},
                ("ai", None): {},
            }
            return values.get((section, key), default)

    monkeypatch.setattr(admin_api, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(admin_api, "build_analyzer", lambda cfg: (_ for _ in ()).throw(ValueError("kein Key")))
    payload = admin_api.PdfBatchPayload(
        recipe_id=recipe_id, dry_run=False, background=False,
        auto_rotate=False, remove_blank_pages=False, auto_crop=False,
        deskew_scans=False, ocr_scans=False, improve_contrast=False,
        sharpen_scans=False, keep_original=False,
        extract_recipe_data=True, overwrite_recipe_data=False,
    )
    result = admin_api._process_pdf_targets(payload, [pdf_path], actor="test")
    assert result["errors"] == 0
    assert result["ingredients_found"] >= 3
    assert result["recipes_updated"] == 1
    names = [item["canonical_name"] for item in test_db.recipe_ingredients_get(recipe_id)]
    assert "kartoffel" in names
    assert "zwiebel" in names
