"""Restore/purge races and folder reuse must never delete another recipe's files."""
import json
import time
from pathlib import Path

import pytest

from app.recipes import manage


@pytest.fixture
def trash_tree(tmp_path, monkeypatch):
    root, trash = tmp_path / "recipes", tmp_path / "trash"

    class Config:
        def get(self, *keys, default=None):
            return {
                ("paths", "recipe_dir"): str(root),
                ("safety", "trash_dir"): str(trash),
            }.get(keys, default)

    monkeypatch.setattr(manage, "get_config", lambda: Config())
    return root, trash


def _recipe(db, root, label):
    folder = root / "Hauptgericht" / "Test" / "Rezept"
    folder.mkdir(parents=True)
    (folder / "description.txt").write_text(label, encoding="utf-8")
    (folder / "info.json").write_text(json.dumps({"name": label}), encoding="utf-8")
    recipe_id = db.recipe_upsert(
        url="https://example.invalid/reused-recipe", name=label,
        type="Hauptgericht", category="Test", folder_path=str(folder),
        description=label, thumb_filename=None, video_filename=None, source_added_at=1,
    )
    return recipe_id, folder


def _delete_to_quarantine(db, recipe_id, folder):
    manage.safe_delete_recipe(db, recipe_id)
    history = db.deleted_history_latest(str(folder), reason="soft_delete")
    return Path(history["quarantine_path"])


@pytest.mark.parametrize("registered", [False, True])
def test_purging_old_trash_keeps_reimported_active_folder(test_db, trash_tree, registered):
    root, _ = trash_tree
    old_id, folder = _recipe(test_db, root, "Original")
    original_quarantine = _delete_to_quarantine(test_db, old_id, folder)
    if registered:
        new_id, _ = _recipe(test_db, root, "Neu importiert")
        assert new_id != old_id
    else:
        folder.mkdir(parents=True)
        (folder / "description.txt").write_text("Neu importiert", encoding="utf-8")

    manage.safe_delete_recipe(test_db, old_id, hard=True, delete_files=True)

    assert folder.is_dir(), "Purging an old trash entry must not move the current recipe folder"
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Neu importiert"
    if registered:
        assert test_db.recipe_get(new_id)["deleted_at"] is None
    assert test_db.recipe_get(old_id) is None
    assert not original_quarantine.exists()


def test_invalid_quarantine_target_is_rejected_before_deleting_the_db_record(test_db, trash_tree, tmp_path):
    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Beschädigter Verweis")
    quarantine = _delete_to_quarantine(test_db, recipe_id, folder)
    outside = tmp_path / "unrelated" / "payload"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("Fremde Dateien", encoding="utf-8")
    with test_db.conn() as connection:
        connection.execute("UPDATE deleted_history SET quarantine_path=?", (str(outside),))

    with pytest.raises(RuntimeError, match="außerhalb"):
        manage.safe_delete_recipe(test_db, recipe_id, hard=True, delete_files=True)

    assert test_db.recipe_get(recipe_id)["deleted_at"] is not None
    assert quarantine.is_dir()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "Fremde Dateien"


def test_restore_does_not_adopt_an_unregistered_reimport_folder(test_db, trash_tree):
    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Original")
    quarantine = _delete_to_quarantine(test_db, recipe_id, folder)
    folder.mkdir(parents=True)
    (folder / "description.txt").write_text("Neuer Import vor DB-Registrierung", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Zielordner"):
        manage.safe_restore_recipe(test_db, recipe_id)

    assert test_db.recipe_get(recipe_id)["deleted_at"] is not None
    assert quarantine.is_dir()
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Neuer Import vor DB-Registrierung"


@pytest.mark.parametrize("legacy_history", [False, True])
def test_restore_accepts_a_payload_already_moved_back_by_manual_repair(test_db, trash_tree, legacy_history):
    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Manuell zurückgebracht")
    quarantine = _delete_to_quarantine(test_db, recipe_id, folder)
    quarantine.rename(folder)
    if legacy_history:
        with test_db.conn() as connection:
            connection.execute("UPDATE deleted_history SET metadata='{}'")

    assert manage.safe_restore_recipe(test_db, recipe_id)["ok"]
    assert test_db.recipe_get(recipe_id)["deleted_at"] is None
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Manuell zurückgebracht"


def test_hard_delete_rechecks_trash_state_in_its_database_transaction(test_db, trash_tree, monkeypatch):
    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Legacy Papierkorb")
    test_db.recipe_soft_delete(recipe_id, files_deleted=False)
    real_commit = test_db.recipe_delete_with_history

    def restore_before_commit(*args, **kwargs):
        # Emulate a direct DB repair after the folder has been moved but before
        # the guarded transaction. The manager must compensate its own move.
        assert test_db.recipe_restore(recipe_id)["ok"]
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(test_db, "recipe_delete_with_history", restore_before_commit)
    result = manage.safe_delete_recipe(test_db, recipe_id, hard=True, delete_files=True, only_deleted=True)

    assert result["ok"] and result["skipped"]
    assert test_db.recipe_get(recipe_id)["deleted_at"] is None
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Legacy Papierkorb"


@pytest.mark.parametrize("legacy_history", [False, True])
def test_restore_uses_recipe_identity_after_repeated_folder_reuse(test_db, trash_tree, legacy_history):
    root, _ = trash_tree
    first_id, folder = _recipe(test_db, root, "Erstes Original")
    first_quarantine = _delete_to_quarantine(test_db, first_id, folder)
    second_id, _ = _recipe(test_db, root, "Zweites Original")
    second_quarantine = _delete_to_quarantine(test_db, second_id, folder)
    if legacy_history:
        with test_db.conn() as connection:
            connection.execute("UPDATE deleted_history SET metadata='{}'")

    result = manage.safe_restore_recipe(test_db, first_id)

    assert result["ok"]
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Erstes Original"
    assert not first_quarantine.exists()
    assert second_quarantine.is_dir()
    assert test_db.recipe_get(second_id)["deleted_at"] is not None


@pytest.mark.parametrize("legacy_history", [False, True])
def test_purging_one_trash_keeps_other_recipe_quarantine(test_db, trash_tree, legacy_history):
    root, _ = trash_tree
    first_id, folder = _recipe(test_db, root, "Erstes Original")
    first_quarantine = _delete_to_quarantine(test_db, first_id, folder)
    second_id, _ = _recipe(test_db, root, "Zweites Original")
    second_quarantine = _delete_to_quarantine(test_db, second_id, folder)
    if legacy_history:
        with test_db.conn() as connection:
            connection.execute("UPDATE deleted_history SET metadata='{}'")

    manage.safe_delete_recipe(test_db, first_id, hard=True, delete_files=True)

    assert not first_quarantine.exists()
    assert second_quarantine.is_dir(), "A different recipe's original must stay restorable"
    assert test_db.recipe_get(second_id)["deleted_at"] is not None
    assert manage.safe_restore_recipe(test_db, second_id)["ok"]
    assert (folder / "description.txt").read_text(encoding="utf-8") == "Zweites Original"


def test_empty_trash_skips_a_recipe_restored_after_listing(client, test_db, trash_tree, monkeypatch):
    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Wiederhergestellt")
    _delete_to_quarantine(test_db, recipe_id, folder)
    real_delete = manage.safe_delete_recipe

    def restore_before_purge(db, recipe_id, **kwargs):
        assert manage.safe_restore_recipe(db, recipe_id)["ok"]
        return real_delete(db, recipe_id, **kwargs)

    monkeypatch.setattr(manage, "safe_delete_recipe", restore_before_purge)
    response = client.delete("/api/recipes/trash/empty?delete_files=true")

    assert response.status_code == 200
    assert response.json()["purged"] == 0 and response.json()["errors"] == []
    assert test_db.recipe_get(recipe_id)["deleted_at"] is None
    assert folder.is_dir()


def test_expired_quarantined_recipe_is_actually_purged(test_db, trash_tree, monkeypatch):
    from app import main

    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Abgelaufen")
    quarantine = _delete_to_quarantine(test_db, recipe_id, folder)
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time() - 40 * 86400, recipe_id))
    monkeypatch.setattr(main, "_db", test_db)

    main._purge_old_trash_items(days=30)

    assert test_db.recipe_get(recipe_id) is None
    assert not quarantine.exists()


@pytest.mark.parametrize("deleted_again", [False, True])
def test_cleanup_rechecks_current_trash_age_after_waiting(test_db, trash_tree, monkeypatch, deleted_again):
    from app import main

    root, _ = trash_tree
    recipe_id, folder = _recipe(test_db, root, "Zwischenzeitlich gerettet")
    # Legacy entries may still have their files in the original folder.
    test_db.recipe_soft_delete(recipe_id, files_deleted=False)
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time() - 40 * 86400, recipe_id))
    real_delete = manage.safe_delete_recipe

    def restore_before_purge(db, recipe_id, **kwargs):
        assert manage.safe_restore_recipe(db, recipe_id)["ok"]
        if deleted_again:
            db.recipe_soft_delete(recipe_id, files_deleted=False)
        return real_delete(db, recipe_id, **kwargs)

    monkeypatch.setattr(main, "_db", test_db)
    monkeypatch.setattr(manage, "safe_delete_recipe", restore_before_purge)
    main._purge_old_trash_items(days=30)

    recipe = test_db.recipe_get(recipe_id)
    assert recipe is not None, "A stale expiry scan must not purge a restored or newly trashed recipe"
    assert bool(recipe["deleted_at"]) == deleted_again
    assert folder.is_dir()
