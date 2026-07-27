from pathlib import Path

import pymupdf

from app.auth import require_admin
from app.core.pdf_processing import analyze_pdf_bytes, process_pdf_bytes, process_pdf_path
from app.db import Database
from app.recipes.search import parse_search_query, suggest_query


def _recipe(db: Database, tmp_path: Path, name: str = "Pasta al Limone", description: str = "Zitrone und Sahne") -> int:
    folder = tmp_path / name.replace(" ", "-")
    folder.mkdir(parents=True, exist_ok=True)
    return db.recipe_upsert(
        url=f"https://example.test/{folder.name}", name=name,
        type="Hauptgericht", category="Pasta", folder_path=str(folder),
        description=description, thumb_filename=None, video_filename=None,
        source_added_at=1.0,
    )


def test_recipe_version_restore_is_atomic_and_keeps_personal_state(test_db: Database, tmp_path: Path):
    recipe_id = _recipe(test_db, tmp_path)
    test_db.recipe_set_extraction_result(recipe_id, "ok", [{
        "name": "Zitrone", "canonical_name": "zitrone", "amount": 1, "unit": "Stück", "raw": "1 Zitrone"
    }])
    test_db.recipe_steps_set(recipe_id, [{"instruction": "Alles verrühren", "timer_seconds": 60}])
    test_db.recipe_tags_set(recipe_id, ["schnell"])
    version_id = test_db.recipe_version_create(recipe_id, created_by="admin", reason="Vor Bearbeitung")
    assert version_id

    with test_db.conn() as c:
        c.execute("UPDATE recipes SET name='Verändert', is_favorite=1, rating=5 WHERE id=?", (recipe_id,))
    test_db.recipe_set_extraction_result(recipe_id, "ok", [{
        "name": "Salz", "canonical_name": "salz", "amount": 2, "unit": "g", "raw": "2 g Salz"
    }])
    test_db.recipe_steps_set(recipe_id, [{"instruction": "Verändert"}])
    test_db.recipe_tags_set(recipe_id, ["neu"])

    result = test_db.recipe_version_restore(version_id, restored_by="admin")
    assert result["ok"] is True
    restored = test_db.recipe_get(recipe_id)
    assert restored["name"] == "Pasta al Limone"
    # Favorit und Bewertung sind Nutzungszustand und werden nicht zurückgerollt.
    assert restored["is_favorite"] == 1
    assert restored["rating"] == 5
    assert [i["canonical_name"] for i in test_db.recipe_ingredients_get(recipe_id)] == ["zitrone"]
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Alles verrühren"
    assert [t["name"] for t in test_db.recipe_tags_get(recipe_id)] == ["schnell"]
    assert len(test_db.recipe_versions_list(recipe_id=recipe_id)) == 2  # Snapshot + Undo-Snapshot


def test_smart_search_synonyms_exclusions_and_unicode(test_db: Database, tmp_path: Path):
    hack_id = _recipe(test_db, tmp_path, "Hackpfanne", "Herzhafte Pfanne")
    test_db.recipe_set_extraction_result(hack_id, "ok", [
        {"name": "Hackfleisch", "canonical_name": "hackfleisch"},
        {"name": "Zwiebel", "canonical_name": "zwiebel"},
    ])
    veg_id = _recipe(test_db, tmp_path, "Kartoffelpfanne", "Knusprig aus dem Ofen")
    test_db.recipe_set_extraction_result(veg_id, "ok", [
        {"name": "Erdäpfel", "canonical_name": "erdäpfel"},
    ])

    # Default-Synonyme werden bei der Migration angelegt.
    assert [r["id"] for r in test_db.recipe_list(search="Faschiertes")] == [hack_id]
    assert [r["id"] for r in test_db.recipe_list(search="Erdapfel")] == [veg_id]
    assert [r["id"] for r in test_db.recipe_list(search="Pfanne -Zwiebel")] == [veg_id]

    plan = parse_search_query('Pfanne ohne Zwiebel', test_db.search_synonyms_map())
    assert plan.negative_terms == ["Zwiebel"]
    suggestion = suggest_query("Kartofel", test_db.search_vocabulary(), test_db.search_synonyms_map())
    assert suggestion and suggestion.corrected_query


def _pdf_with_content_and_blank() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(pymupdf.Rect(80, 100, 520, 700), color=(0, 0, 0), width=1)
    page.insert_text((120, 180), "Zutaten: Kartoffeln, Salz, Butter", fontsize=14)
    doc.new_page(width=600, height=800)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_processing_removes_blank_and_crops_without_losing_text():
    source = _pdf_with_content_and_blank()
    analysis = analyze_pdf_bytes(source)
    assert analysis.pages_before == 2
    assert analysis.pages[1].blank is True

    output, report = process_pdf_bytes(
        source, auto_rotate=False, remove_blank_pages=True,
        auto_crop=True, deskew_scans=False,
    )
    assert report.ok is True
    assert report.changed is True
    assert report.removed_blank_pages == 1
    assert report.pages_after == 1
    doc = pymupdf.open(stream=output, filetype="pdf")
    try:
        assert len(doc) == 1
        assert "Kartoffeln" in doc[0].get_text()
        assert doc[0].cropbox.width < 600 or doc[0].cropbox.height < 800
    finally:
        doc.close()


def test_pdf_path_keeps_original_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "recipe.pdf"
    source = _pdf_with_content_and_blank()
    path.write_bytes(source)
    backup_root = tmp_path / "originals"
    report = process_pdf_path(
        path, backup_root=backup_root, keep_original=True,
        auto_rotate=False, remove_blank_pages=True, auto_crop=False,
    )
    assert report.ok and report.changed
    assert report.original_backup
    assert Path(report.original_backup).read_bytes() == source
    second = process_pdf_path(
        path, backup_root=backup_root, keep_original=True,
        auto_rotate=False, remove_blank_pages=True, auto_crop=False,
    )
    assert second.changed is False


def test_admin_routes_are_consolidated(client, test_db: Database, tmp_path: Path):
    from app.main import app
    app.dependency_overrides[require_admin] = lambda: None
    recipe_id = _recipe(test_db, tmp_path)
    test_db.recipe_version_create(recipe_id, created_by="test", reason="Test")
    try:
        overview = client.get("/api/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["counts"]["versions"] == 1

        versions = client.get(f"/api/admin/versions?recipe_id={recipe_id}")
        assert versions.status_code == 200
        assert len(versions.json()["items"]) == 1

        maintenance = client.post("/api/admin/maintenance/run/integrity", json={})
        assert maintenance.status_code == 200
        assert maintenance.json()["ok"] is True
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _scan_pdf_bytes(text: str = "Zutaten Kartoffeln Salz Butter") -> bytes:
    from PIL import Image, ImageDraw
    import io
    image = Image.new("RGB", (1400, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((150, 350), text, fill="black", font_size=52)
    buf = io.BytesIO(); image.save(buf, format="PNG")
    doc = pymupdf.open(); page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes(); doc.close()
    return data


def test_scan_pdf_gets_searchable_ocr_layer_when_tesseract_exists():
    import shutil
    import pytest
    if not shutil.which("tesseract"):
        pytest.skip("tesseract nicht installiert")
    output, report = process_pdf_bytes(
        _scan_pdf_bytes(), auto_rotate=False, remove_blank_pages=False,
        auto_crop=False, ocr_scans=True, improve_contrast=True,
        ocr_language="deu+eng",
    )
    assert report.ok is True
    assert report.ocr_pages == 1
    assert report.contrast_pages == 1
    doc = pymupdf.open(stream=output, filetype="pdf")
    try:
        text = doc[0].get_text().casefold()
        assert "kartoffeln" in text
        assert abs(doc[0].rect.width - 595) < 5
        assert abs(doc[0].rect.height - 842) < 5
    finally:
        doc.close()


def test_admin_pdf_page_editor_reorders_rotates_and_keeps_backup(client, test_db: Database,
                                                                  tmp_path: Path, monkeypatch):
    from app.main import app
    import app.routes.api_admin as admin_api

    root = tmp_path / "recipes"; folder = root / "Test"; folder.mkdir(parents=True)
    pdf_path = folder / "recipe.pdf"
    doc = pymupdf.open()
    p1 = doc.new_page(width=400, height=600); p1.insert_text((50, 100), "SEITE EINS", fontsize=20)
    p2 = doc.new_page(width=400, height=600); p2.insert_text((50, 100), "SEITE ZWEI", fontsize=20)
    pdf_path.write_bytes(doc.tobytes()); doc.close()
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/pdf", name="PDF Test", type="Hauptgericht",
        category="Test", folder_path=str(folder), description="Test",
        thumb_filename=None, video_filename=None, source_added_at=1.0,
    )

    class FakeConfig:
        def get(self, section, key=None, default=None):
            values = {
                ("paths", "recipe_dir"): str(root),
                ("paths", "data_dir"): str(tmp_path / "data"),
                ("paths", "temp_dir"): str(tmp_path / "temp"),
                ("pdf", None): {},
            }
            return values.get((section, key), default)

    monkeypatch.setattr(admin_api, "get_config", lambda: FakeConfig())
    app.dependency_overrides[require_admin] = lambda: None
    try:
        info = client.get(f"/api/admin/pdf/{recipe_id}/pages")
        assert info.status_code == 200
        assert len(info.json()["pages"]) == 2
        preview = client.get(f"/api/admin/pdf/{recipe_id}/pages/1/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("image/jpeg")

        result = client.post(f"/api/admin/pdf/{recipe_id}/pages/apply", json={
            "order": [2, 1], "rotations": {"2": 90}, "keep_original": True,
        })
        assert result.status_code == 200, result.text
        assert result.json()["pages_after"] == 2
        assert Path(result.json()["backup"]).is_file()

        changed = pymupdf.open(str(pdf_path))
        try:
            assert "SEITE ZWEI" in changed[0].get_text()
            assert changed[0].rotation == 90
            assert "SEITE EINS" in changed[1].get_text()
        finally:
            changed.close()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_recipe_api_returns_transparent_typo_correction(client, test_db: Database, tmp_path: Path):
    _recipe(test_db, tmp_path, "Kartoffelsuppe", "Cremige Suppe")
    response = client.get("/api/recipes?search=Kartofelsuppe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["search_meta"]["corrected"] is True
    assert payload["search_meta"]["suggestion"] == "Kartoffelsuppe"


def test_structured_mutation_creates_required_version(client, test_db: Database, tmp_path: Path):
    recipe_id = _recipe(test_db, tmp_path)
    response = client.put(f"/api/recipes/{recipe_id}/tags", json={"tags": ["Sommer"]})
    assert response.status_code == 200
    versions = test_db.recipe_versions_list(recipe_id=recipe_id)
    assert len(versions) == 1
    assert versions[0]["reason"] == "Tags geändert"


def test_mutation_is_blocked_when_version_snapshot_fails(client, test_db: Database,
                                                          tmp_path: Path, monkeypatch):
    recipe_id = _recipe(test_db, tmp_path)

    def broken_snapshot(*args, **kwargs):
        raise OSError("Datenträger nicht beschreibbar")

    monkeypatch.setattr(test_db, "recipe_version_create", broken_snapshot)
    response = client.put(f"/api/recipes/{recipe_id}/tags", json={"tags": ["Darf nicht gespeichert werden"]})
    assert response.status_code == 500
    assert test_db.recipe_tags_get(recipe_id) == []


def test_admin_pdf_dry_run_detects_rotation_without_writing(client, test_db: Database,
                                                              tmp_path: Path, monkeypatch):
    from app.main import app
    import app.routes.api_admin as admin_api

    root = tmp_path / "recipes"; folder = root / "Sideways"; folder.mkdir(parents=True)
    pdf_path = folder / "recipe.pdf"
    doc = pymupdf.open(); page = doc.new_page(width=600, height=800)
    text = "Zutaten Kartoffeln Butter Sahne Salz Rezept Anleitung " * 4
    page.insert_text((160, 700), text, fontsize=12, rotate=90)
    pdf_path.write_bytes(doc.tobytes()); doc.close()
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/sideways", name="Sideways", type="Hauptgericht",
        category="Test", folder_path=str(folder), description="Test",
        thumb_filename=None, video_filename=None, source_added_at=1.0,
    )
    original = pdf_path.read_bytes()

    class FakeConfig:
        def get(self, section, key=None, default=None):
            values = {
                ("paths", "recipe_dir"): str(root),
                ("paths", "data_dir"): str(tmp_path / "data"),
                ("pdf", None): {"use_tesseract_osd": True, "use_ocr_vote": True},
            }
            return values.get((section, key), default)

    monkeypatch.setattr(admin_api, "get_config", lambda: FakeConfig())
    app.dependency_overrides[require_admin] = lambda: None
    try:
        response = client.post("/api/admin/pdf/process", json={
            "recipe_id": recipe_id, "dry_run": True, "auto_rotate": True,
            "remove_blank_pages": False, "auto_crop": False,
            "deskew_scans": False, "ocr_scans": False,
            "improve_contrast": False, "sharpen_scans": False,
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["changed"] == 1
        assert result["files"][0]["rotated_pages"] == 1
        assert result["files"][0]["rotation_decisions"][0]["method"] == "text-layer"
        assert pdf_path.read_bytes() == original
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_every_active_user_has_admin_compat_access(test_db: Database, monkeypatch):
    import app.auth as auth

    test_db.user_create("mitglied", "not-used", role="user")
    monkeypatch.setattr(auth, "session_user", lambda token: "mitglied")

    class Request:
        cookies = {auth.SESSION_COOKIE: "valid"}

    import asyncio
    result = asyncio.run(auth.require_admin(Request()))
    assert result["username"] == "mitglied"
    assert result["full_access"] is True


def test_direct_admin_routes_render_requested_start_page(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "auth_disabled", lambda: True)
    admin = client.get("/admin")
    assert admin.status_code == 200
    assert 'data-initial-page="admin"' in admin.text
    assert 'data-initial-admin-tab="home"' in admin.text
    assert admin.headers.get("cache-control") == "no-store"

    pdf = client.get("/admin/pdf")
    assert pdf.status_code == 200
    assert 'data-initial-page="admin"' in pdf.text
    assert 'data-initial-admin-tab="pdf"' in pdf.text


def test_user_list_hides_legacy_role_and_self_disable_is_blocked(client, test_db: Database):
    from app.main import app

    user_id = test_db.user_create("mitglied", "not-used", role="admin")
    app.dependency_overrides[require_admin] = lambda: {"username": "mitglied", "full_access": True}
    try:
        listed = client.get("/api/users")
        assert listed.status_code == 200
        item = next(item for item in listed.json()["users"] if item["id"] == user_id)
        assert "role" not in item

        disabled = client.patch(f"/api/users/{user_id}", json={"disabled": True})
        assert disabled.status_code == 400
        assert "eigenes Konto" in disabled.json()["detail"]
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_pdf_background_job_persists_result(client, test_db: Database, tmp_path: Path, monkeypatch):
    from app.main import app
    import app.routes.api_admin as admin_api

    root = tmp_path / "recipes"; folder = root / "Background"; folder.mkdir(parents=True)
    pdf_path = folder / "recipe.pdf"
    doc = pymupdf.open(); page = doc.new_page(width=600, height=800)
    page.insert_text((160, 700), "Zutaten Kartoffeln Butter Sahne Salz Rezept Anleitung " * 4,
                     fontsize=12, rotate=90)
    pdf_path.write_bytes(doc.tobytes()); doc.close()
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/background", name="Background", type="Hauptgericht",
        category="Test", folder_path=str(folder), description="Test",
        thumb_filename=None, video_filename=None, source_added_at=1.0,
    )

    class FakeConfig:
        def get(self, section, key=None, default=None):
            values = {
                ("paths", "recipe_dir"): str(root),
                ("paths", "data_dir"): str(tmp_path / "data"),
                ("pdf", None): {"use_tesseract_osd": False, "use_ocr_vote": False},
            }
            return values.get((section, key), default)

    class ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)
            return object()

    monkeypatch.setattr(admin_api, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(admin_api, "_PDF_EXECUTOR", ImmediateExecutor())
    monkeypatch.setattr(admin_api, "_PDF_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(admin_api, "_pdf_preflight", lambda **_kwargs: {
        "ok": True, "issues": [], "warnings": [], "recipe_root": str(root),
        "backup_root": str(tmp_path / "data" / "pdf-originals"),
        "tesseract": None, "tesseract_languages": [], "free_bytes": None,
    })
    app.dependency_overrides[require_admin] = lambda: None
    try:
        response = client.post("/api/admin/pdf/process", json={
            "recipe_id": recipe_id, "dry_run": True, "background": True,
            "auto_rotate": True, "remove_blank_pages": False, "auto_crop": False,
            "deskew_scans": False, "ocr_scans": False,
            "improve_contrast": False, "sharpen_scans": False,
        })
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert accepted["accepted"] is True
        status = client.get(f"/api/admin/pdf/jobs/{accepted['run_id']}")
        assert status.status_code == 200, status.text
        job = status.json()
        assert job["status"] == "ok"
        assert job["result"]["processed"] == 1
        assert job["result"]["changed"] == 1
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_safe_pdf_render_dpi_limits_huge_pages():
    from app.core.pdf_processing import _safe_render_dpi

    doc = pymupdf.open(); page = doc.new_page(width=2384, height=3370)  # ungefähr A1
    try:
        assert _safe_render_dpi(page, 400) < 400
        assert _safe_render_dpi(page, 400) >= 150
    finally:
        doc.close()
