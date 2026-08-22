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

