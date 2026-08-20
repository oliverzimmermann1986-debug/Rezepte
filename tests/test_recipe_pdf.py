"""PDF-Export und Dateifreigabe für einzelne Rezepte."""

from io import BytesIO

import pdfplumber

from app.db import Database
from tests.conftest import _create_recipe


def test_recipe_pdf_contains_ingredients_steps_and_metadata(
    client,
    test_db: Database,
):
    recipe = _create_recipe(
        test_db,
        name="Kartoffel-Gratin",
        folder_path="/tmp/recipe-pdf",
        description="Cremig, goldgelb und einfach vorzubereiten.",
    )
    recipe_id = int(recipe["id"])
    test_db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Kartoffeln",
                "canonical_name": "kartoffel",
                "amount": 800,
                "unit": "g",
            },
            {
                "name": "Sahne",
                "canonical_name": "sahne",
                "amount": 250,
                "unit": "ml",
            },
        ],
    )
    test_db.recipe_set_servings(recipe_id, 4)
    test_db.recipe_steps_set(
        recipe_id,
        [
            {"instruction": "Kartoffeln in dünne Scheiben schneiden."},
            {"instruction": "Mit Sahne übergießen und goldbraun backen."},
        ],
    )

    response = client.get(f"/recipe/{recipe_id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="kartoffel-gratin.pdf"'
    )
    assert response.content.startswith(b"%PDF-")

    with pdfplumber.open(BytesIO(response.content)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    assert "Kartoffel-Gratin" in text
    assert "4 Portionen" in text
    assert "800 g Kartoffeln" in text
    assert "250 ml Sahne" in text
    assert "Kartoffeln in dünne Scheiben schneiden." in text
    assert "Mit Sahne übergießen und goldbraun backen." in text


def test_recipe_pdf_returns_404_for_unknown_recipe(client):
    assert client.get("/recipe/999999/pdf").status_code == 404
