"""Whole-book exports: real authentication, visibility, bounds and PDF content."""
from io import BytesIO
from pathlib import Path
import re
import time

import pdfplumber
import pytest
from PIL import Image

from app.db import RECIPE_VARIANT_PENDING_STATUS
from app.recipes import cookbook_pdf
from tests.test_household_features import household_api, recipe


def book_with_recipes(client, ids, name="Für Gäste"):
    book = client.post("/api/cookbooks", json={"name": name}).json()["item"]["id"]
    for rid in ids:
        assert client.put(f"/api/cookbooks/{book}/recipes/{rid}").status_code == 200
    return book


def pdf_text(data):
    with pdfplumber.open(BytesIO(data)) as document:
        return [page.extract_text() or "" for page in document.pages]


def test_whole_cookbook_pdf_includes_complete_recipes_and_correct_index(household_api):
    client, db, users, login = household_api
    ids = [recipe(db, "Zucchini-Pasta", users["anna"][1]), recipe(db, "Apfelküchlein")]
    db.recipe_steps_set(ids[0], [{"instruction": "Zucchini schneiden und mit Öl anbraten."}])
    login("anna")
    book = book_with_recipes(client, ids)
    response = client.get(f"/api/cookbooks/{book}/pdf")
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="f-r-g-ste.pdf"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Authorization" in response.headers["vary"]
    pages = pdf_text(response.content)
    assert len(pages) == 4
    assert "Für Gäste" in pages[0] and "2 Rezepte" in pages[0]
    assert re.search(r"Apfelküchlein.*3", pages[1])
    assert re.search(r"Zucchini-Pasta.*4", pages[1])
    assert "Apfelküchlein" in pages[2] and "100 g Nudeln" in pages[2]
    assert "Zucchini schneiden und mit Öl anbraten." in pages[3]
    assert "https://household.example" in pages[3]


def test_pdf_never_exposes_foreign_households_or_hidden_recipe_members(household_api):
    client, db, users, login = household_api
    visible = recipe(db, "Sichtbar", users["anna"][1])
    secret = recipe(db, "Berts-Geheimnis", users["bert"][1])
    deleted = recipe(db, "Geloeschtes-Rezept")
    pending = recipe(db, "Unfertige-Variante", users["anna"][1])
    login("anna")
    book = book_with_recipes(client, [visible, deleted, pending])
    with db.conn() as c:
        c.execute("INSERT INTO household_cookbook_recipes(cookbook_id,recipe_id,added_at) VALUES(?,?,?)", (book, secret, time.time()))
        c.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time(), deleted))
        c.execute("UPDATE recipes SET ingredients_status=? WHERE id=?", (RECIPE_VARIANT_PENDING_STATUS, pending))
    response = client.get(f"/api/cookbooks/{book}/pdf")
    assert response.status_code == 200
    text = "\n".join(pdf_text(response.content))
    assert "Sichtbar" in text
    assert all(name not in text for name in ("Berts-Geheimnis", "Geloeschtes-Rezept", "Unfertige-Variante"))
    for name in ("bert", "guest"):
        login(name)
        assert client.get(f"/api/cookbooks/{book}/pdf").status_code in (403, 404)
    client.headers.pop("Authorization")
    assert client.get(f"/api/cookbooks/{book}/pdf").status_code == 401


def test_pdf_refuses_empty_and_oversized_books_without_silent_truncation(household_api, monkeypatch):
    client, db, _, login = household_api
    login("anna")
    book = book_with_recipes(client, [])
    assert client.get(f"/api/cookbooks/{book}/pdf").status_code == 422
    ids = [recipe(db, f"Rezept-{index}") for index in range(3)]
    for rid in ids:
        client.put(f"/api/cookbooks/{book}/recipes/{rid}")
    monkeypatch.setattr(cookbook_pdf, "MAX_RECIPES", 2)
    assert client.get(f"/api/cookbooks/{book}/pdf").status_code == 422
    monkeypatch.setattr(cookbook_pdf, "MAX_RECIPES", 100)
    monkeypatch.setattr(cookbook_pdf, "MAX_TEXT_CHARACTERS", 1)
    assert client.get(f"/api/cookbooks/{book}/pdf").status_code == 422


def test_render_slots_return_retryable_busy_response(household_api):
    from app.routes.api_household_features import _pdf_slots
    client, db, _, login = household_api
    login("anna")
    book = book_with_recipes(client, [recipe(db)])
    _pdf_slots.acquire()
    _pdf_slots.acquire()
    try:
        response = client.get(f"/api/cookbooks/{book}/pdf")
        assert response.status_code == 429
        assert response.headers["retry-after"] == "5"
    finally:
        _pdf_slots.release()
        _pdf_slots.release()
    assert client.get(f"/api/cookbooks/{book}/pdf").status_code == 200


def test_pdf_embeds_safe_local_image_and_preserves_long_steps(tmp_path):
    folder = tmp_path / "recipe"
    folder.mkdir()
    Image.new("RGB", (800, 400), "#865878").save(folder / "cover.png")
    lines = [f"Abschnitt {index:03d}: Zutaten vorbereiten und sorgfältig verrühren." for index in range(120)]
    pdf = cookbook_pdf.build_cookbook_pdf({"name": "Langes Kochbuch"}, [{
        "name": "Äpfel & Öl <3", "folder_path": str(folder), "thumb_filename": "cover.png",
        "ingredients": [{"amount": 4, "name": "Äpfel"}], "steps": [{"instruction": "\n".join(lines)}],
    }], tmp_path)
    with pdfplumber.open(BytesIO(pdf)) as document:
        assert sum(len(page.images) for page in document.pages) == 1
        text = "\n".join(page.extract_text() or "" for page in document.pages)
        assert len(document.pages) > 4
    assert "Äpfel & Öl <3" in text
    positions = [text.index(f"Abschnitt {index:03d}:") for index in range(120)]
    assert positions == sorted(positions)


@pytest.mark.parametrize("filename", ["../outside.png", "https://127.0.0.1/private", "bad.png", "missing.png"])
def test_pdf_image_rejects_traversal_urls_invalid_and_missing_images(tmp_path, filename):
    folder = tmp_path / "recipe"
    folder.mkdir()
    Image.new("RGB", (40, 40), "red").save(tmp_path / "outside.png")
    (folder / "bad.png").write_bytes(b"not an image")
    assert cookbook_pdf.local_recipe_image({"folder_path": str(folder), "thumb_filename": filename}, tmp_path) is None


def test_pdf_image_limits_and_external_folder(tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    root.mkdir()
    path = root / "image.png"
    Image.new("RGB", (40, 40), "red").save(path)
    row = {"folder_path": str(root), "thumb_filename": path.name}
    monkeypatch.setattr(cookbook_pdf, "MAX_IMAGE_PIXELS", 100)
    assert cookbook_pdf.local_recipe_image(row, root) is None
    monkeypatch.setattr(cookbook_pdf, "MAX_IMAGE_PIXELS", 16_000_000)
    monkeypatch.setattr(cookbook_pdf, "MAX_IMAGE_BYTES", 1)
    assert cookbook_pdf.local_recipe_image(row, root) is None
    assert cookbook_pdf.local_recipe_image({"folder_path": str(tmp_path), "thumb_filename": path.name}, root) is None
