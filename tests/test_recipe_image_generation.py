from io import BytesIO
from pathlib import Path
import sqlite3
import threading

from PIL import Image
import pytest

from app.recipes import image_generation, image_publish
from app.jobs import task_queue


def _jpeg(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (80, 60), color).save(output, format="JPEG")
    return output.getvalue()


class _Config:
    def __init__(self, recipe_root, data_root):
        self.values = {
            "paths": {"recipe_dir": str(recipe_root), "data_dir": str(data_root)},
            "ai": {
                "openai": {"api_key": "test-key"},
                "image_generation": {
                    "enabled": True,
                    "model": "gpt-image-2",
                    "size": "1536x1024",
                    "quality": "medium",
                    "output_format": "jpeg",
                },
            },
        }

    def get(self, *keys, default=None):
        current = self.values
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current


def _image_recipe(test_db, tmp_path, monkeypatch, filename="thumb-generated.jpg"):
    recipe_root = tmp_path / "recipes"
    folder = recipe_root / "Suppe"
    folder.mkdir(parents=True)
    original = _jpeg("red")
    active = folder / filename
    active.write_bytes(original)
    recipe_id = test_db.recipe_upsert(
        url="https://example.invalid/image-test", name="Suppe", type="Hauptgericht",
        category="Suppe", folder_path=str(folder), description="Suppe",
        thumb_filename=filename, video_filename=None, source_added_at=None,
    )

    class FakeAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            return _jpeg("green")

    config = _Config(recipe_root, tmp_path / "data")
    monkeypatch.setattr(image_generation, "get_config", lambda: config)
    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: FakeAnalyzer())
    return recipe_id, folder, active, original


def test_automatic_image_generation_keeps_source_cover(test_db, tmp_path, monkeypatch):
    recipe_id, _folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch, filename="source.jpg")
    batch = "automatic-source-cover"
    test_db.background_task_enqueue("recipe_image_generate", {"recipe_id": recipe_id, "batch_id": batch}, dedupe_key=str(recipe_id))
    monkeypatch.setattr(image_generation, "build_analyzer", lambda *_a: pytest.fail("source cover must not trigger AI"))
    result = image_generation.generate_recipe_image(recipe_id, batch_id=batch, queued=True)
    assert result["skipped"] and active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "source.jpg"


def test_explicit_image_request_can_replace_source_after_backup(test_db, tmp_path, monkeypatch):
    recipe_id, _folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch, filename="source.jpg")
    batch = "explicit-source-cover"
    test_db.background_task_enqueue("recipe_image_generate", {"recipe_id": recipe_id, "batch_id": batch, "replace_existing": True}, dedupe_key=str(recipe_id))
    result = image_generation.generate_recipe_image(recipe_id, batch_id=batch, queued=True, replace_existing=True)
    assert result["ok"] and not result.get("skipped")
    assert active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb-generated.jpg"
    assert result["backup_id"] is not None


def test_failed_image_install_keeps_the_active_original(test_db, tmp_path, monkeypatch):
    recipe_id, folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    real_replace = image_publish.os.replace

    def fail_install(source, destination):
        if Path(destination) == active and Path(source).name.startswith(".generated-") and "rollback" not in Path(source).name:
            raise OSError("simulated install failure")
        return real_replace(source, destination)

    monkeypatch.setattr(image_publish.os, "replace", fail_install)
    with pytest.raises(OSError, match="install failure"):
        image_generation.generate_recipe_image(recipe_id, batch_id="install-failure-batch")
    assert active.is_file(), "The existing active image must survive a failed replacement"
    assert active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["thumb_filename"] == active.name
    assert not list(folder.glob(".*rollback*"))


def test_backup_metadata_failure_cannot_publish_a_dangling_db_pointer(test_db, tmp_path, monkeypatch):
    recipe_id, folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch, "thumb.jpg")
    with test_db.conn() as connection:
        connection.execute("""
            CREATE TRIGGER fail_generated_backup BEFORE UPDATE OF generated_sha256
            ON recipe_image_backups BEGIN
                SELECT RAISE(ABORT, 'simulated backup commit failure');
            END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="backup commit failure"):
        image_generation.generate_recipe_image(recipe_id, batch_id="metadata-failure-batch")
    recipe = test_db.recipe_get(recipe_id)
    assert recipe["thumb_filename"] == active.name
    assert (folder / recipe["thumb_filename"]).read_bytes() == original
    assert recipe["image_generated_at"] is None
    assert recipe["image_generation_status"] == "error"
    assert not (folder / "thumb-generated.jpg").exists()


def test_failed_rollback_retains_the_recovery_copy(test_db, tmp_path, monkeypatch):
    recipe_id, folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    real_status = test_db.recipe_image_generation_status
    real_replace = image_publish.os.replace

    def fail_commit(*args, **kwargs):
        if kwargs["status"] == "ok":
            raise OSError("simulated database failure")
        return real_status(*args, **kwargs)

    def fail_rollback(source, destination):
        if "rollback" in Path(source).name and Path(destination) == active:
            raise OSError("simulated rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(test_db, "recipe_image_generation_status", fail_commit)
    monkeypatch.setattr(image_publish.os, "replace", fail_rollback)
    with pytest.raises(OSError, match="rollback failure"):
        image_generation.generate_recipe_image(recipe_id, batch_id="rollback-failure-batch")
    recovery = list(folder.glob(".*rollback*"))
    assert len(recovery) == 1, "A recovery failure must not delete the rollback image"
    assert recovery[0].read_bytes() == original


def test_old_batch_backup_is_reused_beyond_the_list_limit(test_db, tmp_path, monkeypatch):
    recipe_id, _, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    batch_id = "old-retry-batch"
    backup_id = image_generation.backup_recipe_image(test_db.recipe_get(recipe_id), batch_id)
    backup = test_db.recipe_image_backup_get(backup_id)
    backup_file = image_generation.image_backup_root() / backup["backup_path"]
    with test_db.conn() as connection:
        connection.execute("UPDATE recipe_image_backups SET created_at=0 WHERE id=?", (backup_id,))
        connection.executemany(
            "INSERT INTO recipe_image_backups(batch_id, recipe_id, original_filename, "
            "backup_path, original_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(f"other-batch-{index}", recipe_id, active.name, f"other/{index}.jpg", "unused", 1)
             for index in range(1000)],
        )
    active.write_bytes(_jpeg("green"))
    assert image_generation.backup_recipe_image(test_db.recipe_get(recipe_id), batch_id) == backup_id
    assert backup_file.read_bytes() == original, "Retry must never overwrite an old backup excluded by pagination"


def test_valid_existing_backup_does_not_require_the_active_file(test_db, tmp_path, monkeypatch):
    recipe_id, _, active, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    batch_id = "existing-retry-batch"
    backup_id = image_generation.backup_recipe_image(test_db.recipe_get(recipe_id), batch_id)
    active.unlink()
    assert image_generation.backup_recipe_image(test_db.recipe_get(recipe_id), batch_id) == backup_id


def test_restore_metadata_failure_keeps_the_previously_active_image(test_db, tmp_path, monkeypatch):
    recipe_id, _, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    result = image_generation.generate_recipe_image(recipe_id, batch_id="restore-failure-batch")
    generated = active.read_bytes()
    assert generated != original
    with test_db.conn() as connection:
        connection.execute("""
            CREATE TRIGGER fail_restore_backup BEFORE UPDATE OF restored_at
            ON recipe_image_backups BEGIN
                SELECT RAISE(ABORT, 'simulated restore commit failure');
            END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="restore commit failure"):
        image_generation.restore_recipe_image_backup(result["backup_id"])
    assert active.read_bytes() == generated
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "ok"
    assert test_db.recipe_image_backup_get(result["backup_id"])["restored_at"] is None


def test_failed_publication_cannot_undo_another_successful_publication(tmp_path):
    target = tmp_path / "thumb.jpg"
    first = tmp_path / ".first.jpg"
    second = tmp_path / ".second.jpg"
    target.write_bytes(b"original")
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first_published = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    failures = []

    def fail_first():
        try:
            with image_publish.publish_image(first, target):
                first_published.set()
                assert release_first.wait(3)
                raise RuntimeError("simulated first DB failure")
        except RuntimeError as error:
            failures.append(str(error))

    def finish_second():
        second_started.set()
        with image_publish.publish_image(second, target):
            pass
        second_finished.set()

    first_thread = threading.Thread(target=fail_first)
    second_thread = threading.Thread(target=finish_second)
    first_thread.start()
    try:
        assert first_published.wait(2)
        second_thread.start()
        assert second_started.wait(2)
        second_finished.wait(0.5)
    finally:
        release_first.set()
        first_thread.join(timeout=3)
        if second_thread.ident is not None:
            second_thread.join(timeout=3)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert failures == ["simulated first DB failure"]
    assert second_finished.is_set()
    assert target.read_bytes() == b"second", "A failed operation must not roll back a different successful upload"


@pytest.mark.parametrize("fail_generation", [False, True])
def test_manual_image_wins_over_an_older_inflight_generation(test_db, tmp_path, monkeypatch, fail_generation):
    from app.routes.api_recipes import _publish_thumbnail

    recipe_id, folder, _, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    manual = _jpeg("blue")

    class DelayedAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            staged = folder / ".manual-upload.jpg"
            staged.write_bytes(manual)
            _publish_thumbnail(test_db, recipe_id, staged, folder / "thumb.jpg")
            if fail_generation:
                raise RuntimeError("simulated obsolete AI failure")
            return _jpeg("green")

    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: DelayedAnalyzer())
    result = image_generation.generate_recipe_image(recipe_id, batch_id="older-inflight-batch")
    recipe = test_db.recipe_get(recipe_id)
    assert recipe["thumb_filename"] == "thumb.jpg"
    assert (folder / recipe["thumb_filename"]).read_bytes() == manual
    assert recipe["image_generation_status"] == "skipped"
    assert result["ok"] and result["skipped"]


@pytest.mark.parametrize("absent", [False, True])
def test_restored_version_cover_wins_over_an_older_generation(test_db, tmp_path, monkeypatch, absent):
    from app.recipes import manage

    recipe_id, folder, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    monkeypatch.setattr(manage, "_recipe_root", lambda: folder.parent.resolve())
    version_id = test_db.recipe_version_create(recipe_id)
    if absent:
        test_db.recipe_version_attach_media(version_id, {"thumbnail_absent": True})
    else:
        test_db._backup_thumbnail_for_version(test_db.recipe_get(recipe_id), version_id)
    active.write_bytes(_jpeg("blue"))

    class DelayedAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            restored = test_db.recipe_version_restore(version_id)
            assert restored["ok"] and restored["media_restored"]
            return _jpeg("green")

    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: DelayedAnalyzer())
    result = image_generation.generate_recipe_image(recipe_id, batch_id="obsolete-before-version-restore")

    recipe = test_db.recipe_get(recipe_id)
    assert result["ok"] and result["skipped"]
    assert recipe["image_generation_status"] == "skipped"
    assert recipe["image_generation_batch_id"] is None
    if absent:
        assert recipe["thumb_filename"] is None
        assert not active.exists()
    else:
        assert (folder / recipe["thumb_filename"]).read_bytes() == original


def test_manual_image_also_wins_over_an_older_queued_generation(test_db, tmp_path, monkeypatch):
    from app.routes.api_recipes import _publish_thumbnail

    recipe_id, folder, _, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    payload = {"recipe_id": recipe_id, "batch_id": "older-queued-batch", "ai_processing_consent": "openai-recipe-v1"}
    test_db.background_task_enqueue("recipe_image_generate", payload)
    staged = folder / ".manual-upload.jpg"
    manual = _jpeg("blue")
    staged.write_bytes(manual)
    _publish_thumbnail(test_db, recipe_id, staged, folder / "thumb.jpg")
    calls = []

    class Analyzer:
        def generate_recipe_image(self, *_a, **_kw):
            calls.append(True)
            return _jpeg("green")

    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: Analyzer())
    result = task_queue._dispatch("recipe_image_generate", payload)
    assert calls == [], "A superseded queued job must not incur another AI call"
    assert result["ok"] and result["skipped"]
    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb.jpg"


def test_fast_completed_generation_is_not_reset_to_pending_by_the_route(client, test_db, tmp_path, monkeypatch):
    recipe_id, _, _, _ = _image_recipe(test_db, tmp_path, monkeypatch)

    def complete_before_return(kind, payload, **kwargs):
        task_id = test_db.background_task_enqueue(kind, payload, **kwargs)
        result = image_generation.generate_recipe_image(recipe_id, batch_id=payload["batch_id"])
        test_db.background_task_finish(task_id, ok=True, result=result)
        return task_id

    monkeypatch.setattr(task_queue, "enqueue", complete_before_return)
    response = client.post(f"/api/recipes/{recipe_id}/generate-image", json={"ai_processing_consent": "openai-recipe-v1", })
    assert response.status_code == 202
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "ok"


def test_image_enqueue_and_pending_status_share_one_transaction(test_db, tmp_path, monkeypatch):
    recipe_id, _, _, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    with test_db.conn() as connection:
        connection.execute("""
            CREATE TRIGGER fail_pending_image BEFORE UPDATE OF image_generation_status
            ON recipes WHEN NEW.image_generation_status='pending' BEGIN
                SELECT RAISE(ABORT, 'simulated pending commit failure');
            END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="pending commit failure"):
        test_db.background_task_enqueue("recipe_image_generate", {
            "recipe_id": recipe_id, "batch_id": "atomic-enqueue-batch",
        }, reserve_budget=True)
    with test_db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 0
    assert test_db.recipe_get(recipe_id)["image_generation_batch_id"] is None


def test_new_queued_intent_during_image_commit_rolls_back_the_old_publication(test_db, tmp_path, monkeypatch):
    recipe_id, _, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    old_batch = "older-publishing-batch"
    new_payload = {"recipe_id": recipe_id, "batch_id": "newer-publishing-batch", "replace_existing": True, "ai_processing_consent": "openai-recipe-v1"}
    real_status = test_db.recipe_image_generation_status

    def enqueue_before_commit(*args, **kwargs):
        if kwargs["status"] == "ok" and kwargs["batch_id"] == old_batch:
            test_db.background_task_enqueue("recipe_image_generate", new_payload)
        return real_status(*args, **kwargs)

    monkeypatch.setattr(test_db, "recipe_image_generation_status", enqueue_before_commit)
    result = image_generation.generate_recipe_image(recipe_id, batch_id=old_batch)
    assert result["ok"] and result["skipped"]
    assert active.read_bytes() == original
    recipe = test_db.recipe_get(recipe_id)
    assert recipe["image_generation_status"] == "pending"
    assert recipe["image_generation_batch_id"] == new_payload["batch_id"]
    assert test_db.recipe_image_backup_for_batch(recipe_id, old_batch)["generated_sha256"] is None
    result = task_queue._dispatch("recipe_image_generate", new_payload)
    assert result["ok"] and not result.get("skipped")
    assert active.read_bytes() != original


@pytest.mark.parametrize("fail_generation", [False, True])
def test_restored_original_wins_over_inflight_generation(test_db, tmp_path, monkeypatch, fail_generation):
    recipe_id, _, active, original = _image_recipe(test_db, tmp_path, monkeypatch)
    batch_id = "restore-inflight-batch"

    class DelayedAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            backup = test_db.recipe_image_backup_for_batch(recipe_id, batch_id)
            image_generation.restore_recipe_image_backup(backup["id"])
            if fail_generation:
                raise RuntimeError("simulated obsolete AI failure")
            return _jpeg("green")

    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: DelayedAnalyzer())
    result = image_generation.generate_recipe_image(recipe_id, batch_id=batch_id)
    assert result["ok"] and result["skipped"]
    assert active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "restored"


def test_backfill_preserves_manual_change_after_backup(test_db, tmp_path, monkeypatch):
    from app.routes.api_recipes import _publish_thumbnail

    recipe_id, folder, _, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    calls = []

    class Analyzer:
        def generate_recipe_image(self, *_a, **_kw):
            calls.append(True)
            return _jpeg("green")

    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: Analyzer())
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    payload = {"run_id": run_id, "batch_id": "manual-after-backup-batch"}
    assert image_generation.run_image_backfill(payload, chunk_size=1)["continue"]
    staged = folder / ".manual-upload.jpg"
    manual = _jpeg("blue")
    staged.write_bytes(manual)
    _publish_thumbnail(test_db, recipe_id, staged, folder / "thumb.jpg")
    result = image_generation.run_image_backfill(payload, chunk_size=1)
    assert result["ok"] and result["generated"] == 0
    assert result["skipped_ids"] == [recipe_id]
    assert result["completed_ids"] == [recipe_id]
    assert calls == []
    assert (folder / test_db.recipe_get(recipe_id)["thumb_filename"]).read_bytes() == manual


def test_backfill_still_generates_recipes_without_an_existing_image(test_db, tmp_path, monkeypatch):
    recipe_id, _, active, _ = _image_recipe(test_db, tmp_path, monkeypatch)
    active.unlink()
    with test_db.conn() as connection:
        connection.execute("UPDATE recipes SET thumb_filename=NULL WHERE id=?", (recipe_id,))
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    result = image_generation.run_image_backfill({"run_id": run_id, "batch_id": "no-original-batch"})
    assert result["ok"] and result["generated"] == 1 and result["backed_up"] == 0
    assert result["skipped_ids"] == []
    assert active.is_file()
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "ok"


def test_generated_image_is_backed_up_and_restorable(test_db, tmp_path, monkeypatch):
    recipe_root = tmp_path / "recipes"
    folder = recipe_root / "Hauptgericht" / "Suppe" / "Kartoffelsuppe"
    folder.mkdir(parents=True)
    original = _jpeg("red")
    active = folder / "thumb-generated.jpg"
    active.write_bytes(original)
    recipe_id = test_db.recipe_upsert(
        url="https://koch.example/suppe",
        name="Kartoffelsuppe",
        type="Hauptgericht",
        category="Suppe",
        folder_path=str(folder),
        description="Kartoffelsuppe mit Kartoffeln und Brühe",
        thumb_filename=active.name,
        video_filename=None,
        source_added_at=None,
    )
    test_db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=[{"name": "Kartoffeln", "canonical_name": "kartoffel", "unit": "g"}],
        steps=[], servings=4, auto_tags=[],
    )

    class FakeAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            return _jpeg("green")

    config = _Config(recipe_root, tmp_path / "data")
    monkeypatch.setattr(image_generation, "get_config", lambda: config)
    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: FakeAnalyzer())

    result = image_generation.generate_recipe_image(recipe_id, batch_id="batch-test-123")
    assert result["backup_id"] is not None
    assert active.read_bytes() != original
    backup = test_db.recipe_image_backup_get(result["backup_id"])
    backup_file = image_generation.image_backup_root() / backup["backup_path"]
    assert backup_file.read_bytes() == original

    restored = image_generation.restore_recipe_image_backup(result["backup_id"])
    assert restored["ok"] is True
    assert active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "restored"


def test_backfill_never_generates_when_backup_phase_fails(test_db, monkeypatch):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    monkeypatch.setattr(test_db, "recipes_for_image_backfill", lambda **_kw: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(test_db, "recipe_get", lambda recipe_id: {"id": recipe_id})
    calls = []

    def fail_second(recipe, _batch_id):
        if recipe["id"] == 2:
            raise RuntimeError("backup failed")
        return 1

    monkeypatch.setattr(image_generation, "backup_recipe_image", fail_second)
    monkeypatch.setattr(
        image_generation,
        "generate_recipe_image",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    result = image_generation.run_image_backfill(
        {"run_id": run_id, "batch_id": "batch-test-456"}
    )
    assert result["phase"] == "backup_failed"
    assert result["generated"] == 0
    assert calls == []
