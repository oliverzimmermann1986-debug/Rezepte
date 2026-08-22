"""FS-Lesefehler dürfen bereits bekannte Rezeptdaten nicht nullen."""

import json

from app.recipes import indexer, manage


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
    monkeypatch.setattr(manage, "_assert_inside_root", lambda _path: None)

    result = manage.safe_rename_recipe(
        test_db, recipe_id, "Neuer Anzeigename", rename_folder=False
    )

    assert result["ok"] is True
    assert test_db.recipe_get(recipe_id)["name"] == "Neuer Anzeigename"
    assert json.loads(info_file.read_text(encoding="utf-8"))["name"] == "Neuer Anzeigename"
    assert not list(folder.glob(".info.json.tmp-*"))


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
