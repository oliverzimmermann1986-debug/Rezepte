"""Version restores must keep recipe data, covers and sidecars coherent."""
import os
from pathlib import Path
import threading

from PIL import Image
import pytest

from app.recipes import image_publish, manage
from app.recipes.image_cache import ensure_thumbnail, invalidate_thumbnail_cache


def _version_with_media(db, tmp_path, monkeypatch, *, absent=False):
    monkeypatch.setattr(manage, "_recipe_root", lambda: tmp_path.resolve())
    folder = tmp_path / "Original"
    folder.mkdir()
    original = folder / "original-cover.jpg"
    Image.new("RGB", (80, 60), "red").save(original)
    recipe_id = db.recipe_upsert(
        url="https://example.invalid/version-media", name="Original",
        type="Hauptgericht", category="Test", folder_path=str(folder),
        description="Originalbeschreibung", thumb_filename=None if absent else original.name,
        video_filename=None, source_added_at=None,
    )
    db.recipe_steps_set(recipe_id, [{"instruction": "Originalschritt"}])
    version_id = db.recipe_version_create(recipe_id)
    db._backup_thumbnail_for_version(db.recipe_get(recipe_id), version_id)
    original_bytes = original.read_bytes()
    original.unlink()
    active = folder / "thumb.jpg"
    Image.new("RGB", (80, 60), "blue").save(active)
    with db.conn() as connection:
        connection.execute("UPDATE recipes SET thumb_filename=? WHERE id=?", (active.name, recipe_id))
    manage.safe_update_recipe_metadata(
        db, recipe_id, name="Aktuell", recipe_type="Hauptgericht", category="Test",
        description="Aktuelle Beschreibung", servings=3, url="https://example.invalid/version-media",
        target_folder_override=str(tmp_path / "Aktuell"),
    )
    folder = tmp_path / "Aktuell"
    active = folder / "thumb.jpg"
    db.recipe_steps_set(recipe_id, [{"instruction": "Aktueller Schritt"}])
    db.recipe_cooking_progress_set(recipe_id, "cook", completed_steps=[0], active_step=0, servings=3)
    return recipe_id, version_id, folder, active, original_bytes


@pytest.mark.parametrize("absent", [False, True])
def test_cover_database_failure_rolls_back_the_entire_version_restore(test_db, tmp_path, monkeypatch, absent):
    recipe_id, version_id, folder, active, _ = _version_with_media(test_db, tmp_path, monkeypatch, absent=absent)
    before = test_db.recipe_get(recipe_id)
    cover = active.read_bytes()
    sidecar = (folder / "info.json").read_bytes()
    with test_db.conn() as connection:
        connection.execute("""
            CREATE TRIGGER fail_version_cover BEFORE UPDATE OF thumb_filename ON recipes
            WHEN NEW.thumb_filename IS NULL OR NEW.thumb_filename='original-cover.jpg'
            BEGIN SELECT RAISE(ABORT, 'simulated version cover failure'); END
        """)

    result = test_db.recipe_version_restore(version_id)

    assert not result["ok"] and "version cover failure" in result["error"]
    restored = test_db.recipe_get(recipe_id)
    for field in ("name", "description", "folder_path", "thumb_filename", "servings"):
        assert restored[field] == before[field]
    assert active.read_bytes() == cover
    assert (folder / "info.json").read_bytes() == sidecar
    assert not (tmp_path / "Original").exists()
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Aktueller Schritt"
    assert test_db.recipe_cooking_progress_get(recipe_id, "cook") is not None
    assert not list(folder.glob(".thumb-rollback-*"))


def test_cover_install_failure_keeps_current_recipe_and_cover(test_db, tmp_path, monkeypatch):
    recipe_id, version_id, folder, active, _ = _version_with_media(test_db, tmp_path, monkeypatch)
    cover = active.read_bytes()
    real_replace = image_publish.os.replace

    def fail_install(source, target):
        if Path(target).name == "original-cover.jpg":
            raise OSError("simulated version image install failure")
        return real_replace(source, target)

    monkeypatch.setattr(image_publish.os, "replace", fail_install)

    result = test_db.recipe_version_restore(version_id)

    assert not result["ok"] and "image install failure" in result["error"]
    assert test_db.recipe_get(recipe_id)["name"] == "Aktuell"
    assert test_db.recipe_get(recipe_id)["folder_path"] == str(folder)
    assert active.read_bytes() == cover
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Aktueller Schritt"


def test_version_restore_discards_stale_resized_cover_caches(test_db, tmp_path, monkeypatch):
    recipe_id, version_id, folder, active, original = _version_with_media(test_db, tmp_path, monkeypatch)
    cached = ensure_thumbnail(active, 400)
    future = active.stat().st_mtime + 3600
    os.utime(cached, (future, future))

    result = test_db.recipe_version_restore(version_id)

    assert result["ok"] and result["media_restored"]
    folder = Path(test_db.recipe_get(recipe_id)["folder_path"])
    assert (folder / "original-cover.jpg").read_bytes() == original
    assert not (folder / cached.name).exists()
    regenerated = ensure_thumbnail(folder / "original-cover.jpg", 400)
    with Image.open(regenerated) as image:
        red, _, blue = image.getpixel((0, 0))
        assert red > blue


def test_deleted_recipe_version_cannot_write_into_a_reused_folder(test_db, tmp_path, monkeypatch):
    recipe_id, version_id, folder, _, _ = _version_with_media(test_db, tmp_path, monkeypatch)
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET deleted_at=1, files_deleted=1 WHERE id=?", (recipe_id,))
    before = (folder / "info.json").read_bytes()
    versions_before = test_db.recipe_versions_list(recipe_id=recipe_id)

    result = test_db.recipe_version_restore(version_id)

    assert not result["ok"]
    assert (folder / "info.json").read_bytes() == before
    assert test_db.recipe_get(recipe_id)["deleted_at"] == 1
    assert len(test_db.recipe_versions_list(recipe_id=recipe_id)) == len(versions_before)


def test_version_restore_rejects_media_outside_recipe_folder_before_changes(test_db, tmp_path, monkeypatch):
    recipe_id, version_id, folder, active, _ = _version_with_media(test_db, tmp_path, monkeypatch)
    outside = tmp_path / "foreign-cover.jpg"
    outside.write_bytes(b"foreign bytes")
    test_db.recipe_version_attach_media(version_id, {"thumbnail_backup": "../foreign-cover.jpg"})
    before = test_db.recipe_get(recipe_id)

    result = test_db.recipe_version_restore(version_id)

    assert not result["ok"]
    assert test_db.recipe_get(recipe_id)["folder_path"] == before["folder_path"]
    assert active.is_file() and folder.is_dir()
    assert outside.read_bytes() == b"foreign bytes"


def test_cover_http_cache_revalidates_and_distinguishes_changes_within_one_second(client, test_db, tmp_path, monkeypatch):
    from app.routes import api_recipes

    monkeypatch.setattr(api_recipes, "_recipe_root", lambda: tmp_path.resolve())
    recipe_id, _, _, active, _ = _version_with_media(test_db, tmp_path, monkeypatch)
    stamp = 1_791_000_000_000_000_000
    os.utime(active, ns=(stamp + 100, stamp + 100))
    first = client.get(f"/api/recipes/{recipe_id}/thumb")
    assert first.status_code == 200
    for etag in (first.headers["etag"], 'W/' + first.headers["etag"], '*',
                 '"another-etag", ' + first.headers["etag"]):
        cached = client.get(f"/api/recipes/{recipe_id}/thumb", headers={"If-None-Match": etag})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["etag"] == first.headers["etag"]
        assert cached.headers["cache-control"] == "private, max-age=0, must-revalidate"
        assert "Cookie" in cached.headers["vary"] and "Authorization" in cached.headers["vary"]
    size = active.stat().st_size
    active.write_bytes(b"x" * size)
    os.utime(active, ns=(stamp + 100_000_000, stamp + 100_000_000))

    second = client.get(f"/api/recipes/{recipe_id}/thumb", headers={"If-None-Match": first.headers["etag"]})

    assert second.status_code == 200 and second.content != first.content
    assert second.headers["etag"] != first.headers["etag"]
    assert second.headers["cache-control"] == "private, max-age=0, must-revalidate"


def test_old_resize_cannot_publish_a_stale_cache_after_a_new_cover(tmp_path, monkeypatch):
    target = tmp_path / "thumb.jpg"
    Image.new("RGB", (80, 60), "blue").save(target)
    staged = tmp_path / ".new-cover.jpg"
    Image.new("RGB", (80, 60), "red").save(staged)
    reader_ready = threading.Event()
    release_reader = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    failures = []
    real_open = Image.open

    def slow_open(path, *args, **kwargs):
        image = real_open(path, *args, **kwargs)
        if threading.current_thread().name == "old-thumbnail-reader":
            image.load()
            reader_ready.set()
            if not release_reader.wait(3):
                image.close()
                raise TimeoutError("test reader was not released")
        return image

    def read_old():
        try:
            ensure_thumbnail(target, 400)
        except Exception as exc:
            failures.append(exc)

    def publish_new():
        writer_started.set()
        try:
            with image_publish.publish_image(staged, target):
                pass
            invalidate_thumbnail_cache(tmp_path)
        except Exception as exc:
            failures.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(Image, "open", slow_open)
    reader = threading.Thread(target=read_old, name="old-thumbnail-reader")
    writer = threading.Thread(target=publish_new, name="new-cover-writer")
    reader.start()
    try:
        assert reader_ready.wait(2)
        writer.start()
        assert writer_started.wait(2)
        assert not writer_done.wait(0.1), "New cover must wait for the older resize to finish"
    finally:
        release_reader.set()
        reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not reader.is_alive() and not writer.is_alive()
    assert failures == []
    with Image.open(ensure_thumbnail(target, 400)) as image:
        red, _, blue = image.getpixel((0, 0))
        assert red > blue
