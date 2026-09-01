"""FS-Lesefehler dürfen bereits bekannte Rezeptdaten nicht nullen."""

import json

from app.recipes import indexer, manage


def test_mail_attachment_placeholder_still_triggers_media_extraction():
    recipe = {"url": "mail-attachment://message::recipe-card.jpg"}
    placeholder = "Importierter Mail-Anhang: recipe-card.jpg"

    assert indexer._needs_media_extract(recipe, placeholder) is True
    assert indexer._needs_media_extract(
        {"url": "https://example.test/recipe"}, placeholder
    ) is False


def test_indexer_preserves_known_metadata_when_sidecar_and_scan_fail(test_db, tmp_path, monkeypatch):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "Ordnername"
    folder.mkdir(parents=True)
    recipe_id = test_db.recipe_upsert(
        url="https://www.tiktok.com/@koch/video/123",
        name="Mein Anzeigename",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Eine bereits sicher gespeicherte Beschreibung",
        thumb_filename="cover.jpg",
        video_filename="source.mp4",
        source_added_at=1,
    )
    (folder / "info.json").write_text("{kaputt", encoding="utf-8")

    monkeypatch.setattr(indexer, "_safe_iterdir_checked", lambda _folder: ([], False))
    monkeypatch.setattr(indexer, "_pdf_thumb", lambda _folder: None)
    indexer._index_one(test_db, folder, "Hauptgericht", "Pasta")

    stored = test_db.recipe_get(recipe_id)
    assert stored["url"] == "https://www.tiktok.com/@koch/video/123"
    assert stored["name"] == "Mein Anzeigename"
    assert stored["description"] == "Eine bereits sicher gespeicherte Beschreibung"
    assert stored["thumb_filename"] == "cover.jpg"
    assert stored["video_filename"] == "source.mp4"


def test_indexer_preserves_known_url_when_sidecar_omits_url(test_db, tmp_path, monkeypatch):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "Altbestand"
    folder.mkdir(parents=True)
    recipe_id = test_db.recipe_upsert(
        url="https://www.tiktok.com/@koch/video/987",
        name="Altbestand",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Eine ausreichend lange Rezeptbeschreibung",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    (folder / "info.json").write_text(
        json.dumps({"name": "Altbestand", "type": "Hauptgericht"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(indexer, "_pdf_thumb", lambda _folder: None)

    indexer._index_one(test_db, folder, "Hauptgericht", "Pasta")

    assert test_db.recipe_get(recipe_id)["url"] == "https://www.tiktok.com/@koch/video/987"


def test_indexer_prefers_generated_image_over_resized_http_caches(test_db, tmp_path):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "Generiert"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({"name": "Generiert", "url": "https://example.test/generiert"}),
        encoding="utf-8",
    )
    (folder / "description.txt").write_text("Eine vollständige Beschreibung", encoding="utf-8")
    (folder / "thumb.jpg").write_bytes(b"source")
    (folder / "thumb-generated.jpg").write_bytes(b"generated")
    (folder / "thumb-w400.jpg").write_bytes(b"cache-400")
    (folder / "thumb-w800.jpg").write_bytes(b"cache-800")
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/generiert",
        name="Generiert",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Eine vollständige Beschreibung",
        thumb_filename="thumb-w400.jpg",
        video_filename=None,
        source_added_at=1,
    )

    indexer._index_one(test_db, folder, "Hauptgericht", "Pasta")

    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb-generated.jpg"


def test_indexer_reactivates_generated_image_after_successful_generation(test_db, tmp_path):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "ErfolgreichGeneriert"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({"name": "Erfolgreich generiert", "url": "https://example.test/ok"}),
        encoding="utf-8",
    )
    (folder / "thumb.jpg").write_bytes(b"source")
    (folder / "thumb-generated.jpg").write_bytes(b"generated")
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/ok",
        name="Erfolgreich generiert",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename="thumb.jpg",
        video_filename=None,
        source_added_at=1,
    )
    test_db.recipe_image_generation_status(recipe_id, status="ok")

    indexer._index_one(test_db, folder, "Hauptgericht", "Pasta")

    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb-generated.jpg"


def test_indexer_preserves_intentionally_restored_active_image(test_db, tmp_path):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "Wiederhergestellt"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({"name": "Wiederhergestellt", "url": "https://example.test/original"}),
        encoding="utf-8",
    )
    (folder / "thumb.jpg").write_bytes(b"restored")
    (folder / "thumb-generated.jpg").write_bytes(b"older-generated")
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/original",
        name="Wiederhergestellt",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename="thumb.jpg",
        video_filename=None,
        source_added_at=1,
    )
    test_db.recipe_image_generation_status(recipe_id, status="restored")

    indexer._index_one(test_db, folder, "Hauptgericht", "Pasta")

    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb.jpg"


def test_display_only_rename_is_persisted_in_atomic_sidecar(test_db, tmp_path, monkeypatch):
    folder = tmp_path / "Hauptgericht" / "Pasta" / "Alter_Ordner"
    folder.mkdir(parents=True)
    info_file = folder / "info.json"
    info_file.write_text(
        json.dumps({"name": "Alter Name", "url": "https://example.test/rezept"}),
        encoding="utf-8",
    )
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/rezept",
        name="Alter Name",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    monkeypatch.setattr(manage, "_assert_inside_root", lambda path: path.resolve())

    result = manage.safe_rename_recipe(
        test_db, recipe_id, "Neuer__Anzeigename_", rename_folder=False
    )

    assert result["ok"] is True
    assert test_db.recipe_get(recipe_id)["name"] == "Neuer Anzeigename"
    assert json.loads(info_file.read_text(encoding="utf-8"))["name"] == "Neuer Anzeigename"
    assert result["new_name"] == "Neuer Anzeigename"
    assert not list(folder.glob(".info.json.tmp-*"))


def test_history_edit_updates_recipe_path_and_sidecars(test_db, tmp_path, monkeypatch):
    from app.jobs.scraper import ScraperJob

    root = tmp_path / "recipes"
    old = root / "Hauptgericht" / "Pasta" / "Alt"
    old.mkdir(parents=True)
    (old / "info.json").write_text(
        json.dumps({"name": "Alt", "type": "Hauptgericht", "category": "Pasta"}),
        encoding="utf-8",
    )
    (old / "Alt.jpg").write_bytes(b"cover")
    url = "https://example.test/history-edit"
    recipe_id = test_db.recipe_upsert(
        url=url,
        name="Alt",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(old),
        description="Beschreibung",
        thumb_filename="Alt.jpg",
        video_filename=None,
        source_added_at=1,
    )
    test_db.history_add(
        url,
        content_type="recipe",
        name="Alt",
        target_dir=str(old),
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = root
    job.wedding_dir = tmp_path / "wedding"

    result = job.move_history_item(
        url,
        new_name="Neue Suppe",
        new_type="Vorspeise",
        new_category="Suppen",
    )

    target = root / "Vorspeise" / "Suppen" / "Neue_Suppe"
    assert result["ok"] is True
    assert test_db.recipe_get(recipe_id)["folder_path"] == str(target.resolve())
    assert test_db.history_get(url)["target_dir"] == str(target.resolve())
    assert (target / "Neue_Suppe.jpg").read_bytes() == b"cover"


def test_soft_delete_releases_source_and_folder_unique_slots(test_db, tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    trash = tmp_path / "trash"
    folder = root / "Hauptgericht" / "Pasta" / "Alt"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text("{}", encoding="utf-8")
    url = "https://example.test/reimport"
    original_id = test_db.recipe_upsert(
        url=url,
        name="Alt",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )

    class Config:
        def get(self, *keys, default=None):
            return {
                ("paths", "recipe_dir"): str(root),
                ("safety", "trash_dir"): str(trash),
            }.get(keys, default)

    monkeypatch.setattr(manage, "get_config", lambda: Config())
    manage.safe_delete_recipe(test_db, original_id)
    deleted = test_db.recipe_get(original_id)
    assert deleted["url"] is None
    assert deleted["deleted_url"] == url
    assert deleted["folder_path"] == f"__trash__/{original_id}"
    assert deleted["deleted_folder_path"] == str(folder)

    replacement_id = test_db.recipe_upsert(
        url=url,
        name="Neu",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename=None,
        video_filename=None,
        source_added_at=2,
    )
    assert replacement_id != original_id
    restore = test_db.recipe_restore(original_id, files_restored=True)
    assert restore["ok"] is False
    assert restore["conflict_recipe_id"] == replacement_id


def test_metadata_edit_moves_folder_and_updates_sidecars(test_db, tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Pasta" / "Alter_Name"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({"name": "Alter Name", "custom": "bleibt"}), encoding="utf-8"
    )
    (folder / "description.txt").write_text("Alt", encoding="utf-8")
    (folder / "Alter_Name.jpg").write_bytes(b"cover")
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/alt",
        name="Alter Name",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Alt",
        thumb_filename="Alter_Name.jpg",
        video_filename=None,
        source_added_at=1,
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    result = manage.safe_update_recipe_metadata(
        test_db,
        recipe_id,
        name="Neue Suppe",
        recipe_type="Vorspeise",
        category="Suppen",
        description="Neue Beschreibung",
        servings=4,
        url="https://example.test/neu",
    )

    target = root / "Vorspeise" / "Suppen" / "Neue_Suppe"
    assert result["moved"] is True
    assert not folder.exists()
    assert (target / "Neue_Suppe.jpg").read_bytes() == b"cover"
    info = json.loads((target / "info.json").read_text(encoding="utf-8"))
    assert info["custom"] == "bleibt"
    assert info["name"] == "Neue Suppe"
    assert (target / "description.txt").read_text(encoding="utf-8") == "Neue Beschreibung"
    stored = test_db.recipe_get(recipe_id)
    assert stored["folder_path"] == str(target.resolve())
    assert stored["thumb_filename"] == "Neue_Suppe.jpg"
    assert stored["servings"] == 4


def test_metadata_edit_rolls_back_filesystem_on_unique_url_conflict(test_db, tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Pasta" / "Original"
    folder.mkdir(parents=True)
    original_info = json.dumps({"name": "Original", "custom": "sicher"})
    (folder / "info.json").write_text(original_info, encoding="utf-8")
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/one",
        name="Original",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description=None,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    test_db.recipe_upsert(
        url="https://example.test/taken",
        name="Andere",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(root / "Hauptgericht" / "Pasta" / "Andere"),
        description=None,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    try:
        manage.safe_update_recipe_metadata(
            test_db,
            recipe_id,
            name="Verschoben",
            recipe_type="Vorspeise",
            category="Suppen",
            description="Neu",
            servings=2,
            url="https://example.test/taken",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("UNIQUE-Konflikt hätte fehlschlagen müssen")

    assert folder.is_dir()
    assert json.loads((folder / "info.json").read_text(encoding="utf-8"))["custom"] == "sicher"
    assert not (root / "Vorspeise" / "Suppen" / "Verschoben").exists()
    stored = test_db.recipe_get(recipe_id)
    assert stored["name"] == "Original"
    assert stored["url"] == "https://example.test/one"


def test_version_restore_moves_nas_folder_and_restores_source_url(test_db, tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    original = root / "Hauptgericht" / "Pasta" / "Original"
    original.mkdir(parents=True)
    (original / "info.json").write_text(
        json.dumps({"name": "Original", "type": "Hauptgericht", "category": "Pasta"}),
        encoding="utf-8",
    )
    recipe_id = test_db.recipe_upsert(
        url="https://example.test/original",
        name="Original",
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(original),
        description="Originaltext",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())
    version_id = test_db.recipe_version_create(recipe_id, reason="Vor Änderung")
    manage.safe_update_recipe_metadata(
        test_db,
        recipe_id,
        name="Neu",
        recipe_type="Vorspeise",
        category="Suppen",
        description="Neu",
        servings=3,
        url="https://example.test/new",
    )

    result = test_db.recipe_version_restore(version_id, restored_by="test")

    assert result["ok"] is True
    assert original.is_dir()
    assert not (root / "Vorspeise" / "Suppen" / "Neu").exists()
    restored = test_db.recipe_get(recipe_id)
    assert restored["name"] == "Original"
    assert restored["url"] == "https://example.test/original"
    assert restored["folder_path"] == str(original.resolve())
    info = json.loads((original / "info.json").read_text(encoding="utf-8"))
    assert info["name"] == "Original"
    assert info["type"] == "Hauptgericht"
