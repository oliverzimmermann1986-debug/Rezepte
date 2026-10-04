"""Queue regressions at the persistent DB and concurrent worker boundaries."""
import threading
import time
import sqlite3

import pytest

from app.jobs import task_queue
from app.recipes import image_generation


def test_image_claims_never_take_imports_and_prefer_single_images(test_db):
    backfill = test_db.background_task_enqueue("recipe_image_backfill", {})
    single = test_db.background_task_enqueue("recipe_image_generate", {})
    imported = test_db.background_task_enqueue("share_ingest", {})
    assert test_db.background_task_claim_next(lane="images")["id"] == single
    assert test_db.background_task_claim_next(lane="imports")["id"] == imported
    assert test_db.background_task_claim_next(lane="images")["id"] == backfill


def test_import_finishes_while_image_generation_is_still_blocked(test_db, monkeypatch):
    assert task_queue.stop_worker(timeout=2)
    image_started = threading.Event()
    release_image = threading.Event()
    import_finished = threading.Event()

    def dispatch(kind, payload):
        if kind == "recipe_image_generate":
            image_started.set()
            assert release_image.wait(3)
        else:
            import_finished.set()
        return {"ok": True}

    monkeypatch.setattr(task_queue, "_dispatch", dispatch)
    monkeypatch.setattr(task_queue, "get_db", lambda: test_db)
    image_id = task_queue.enqueue("recipe_image_generate", {"recipe_id": 1})
    task_queue.start_worker()
    try:
        assert image_started.wait(2)
        import_id = task_queue.enqueue("share_ingest", {"url": "https://example.invalid/recipe"})
        assert import_finished.wait(2)
        assert test_db.background_task_get(image_id)["status"] == "running"
        deadline = time.monotonic() + 2
        while test_db.background_task_get(import_id)["status"] != "ok":
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        release_image.set()
        assert task_queue.stop_worker(timeout=3)


def test_backfill_checkpoints_survive_restart_without_repeating_images(test_db, monkeypatch):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    payload = {"run_id": run_id, "batch_id": "resume-test-batch"}
    task_id = test_db.background_task_enqueue("recipe_image_backfill", payload)
    recipes = [{"id": 1, "name": "Suppe"}, {"id": 2, "name": "Brot"}]
    calls = []
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    monkeypatch.setattr(test_db, "recipes_for_image_backfill", lambda **_kw: recipes)
    monkeypatch.setattr(test_db, "recipe_get", lambda recipe_id: next(
        recipe for recipe in recipes if recipe["id"] == recipe_id
    ))
    monkeypatch.setattr(image_generation, "backup_recipe_image", lambda recipe, batch: calls.append(("backup", recipe["id"])) or recipe["id"])
    monkeypatch.setattr(image_generation, "generate_recipe_image", lambda recipe_id, **kwargs: calls.append(("generate", recipe_id)))

    for _ in range(3):
        assert test_db.background_task_claim_next(lane="images")["id"] == task_id
        result = image_generation.run_image_backfill(payload, chunk_size=1)
        assert result["continue"]
        test_db.background_task_continue(task_id)
    assert calls == [("backup", 1), ("backup", 2), ("generate", 1)]
    recipes.append({"id": 3, "name": "Neu hinzugefügt"})
    assert test_db.background_task_claim_next(lane="images")["id"] == task_id
    assert test_db.reset_stale_maintenance() == 0
    assert test_db.background_tasks_recover() == 1
    assert test_db.maintenance_get(run_id)["result"]["completed_ids"] == [1]
    result = image_generation.run_image_backfill(payload, chunk_size=1)
    assert result["ok"]
    assert result["generated"] == 2
    assert result["recipe_ids"] == [1, 2], "The original plan must stay frozen after a restart"
    assert calls == [("backup", 1), ("backup", 2), ("generate", 1), ("generate", 2)]
    assert test_db.maintenance_get(run_id)["status"] == "ok"


def test_restart_after_image_commit_recognizes_recipe_batch_status(test_db, monkeypatch):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    batch_id = "committed-test-batch"
    test_db.maintenance_progress(run_id, {
        "phase": "generate", "batch_id": batch_id, "recipe_ids": [1],
        "backup_processed": 1, "backed_up": 1, "generated": 0,
        "completed_ids": [], "errors": [],
    })
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    monkeypatch.setattr(test_db, "recipe_get", lambda _recipe_id: {
        "id": 1, "image_generation_batch_id": batch_id, "image_generation_status": "ok",
    })
    monkeypatch.setattr(test_db, "recipes_for_image_backfill", lambda **_kw: pytest.fail(
        "A resumed checkpoint must not rescan the recipe library"
    ))
    calls = []
    monkeypatch.setattr(image_generation, "generate_recipe_image", lambda *a, **kw: calls.append(a))
    result = image_generation.run_image_backfill({"run_id": run_id, "batch_id": batch_id}, chunk_size=1)
    assert result["generated"] == 1
    assert calls == []


def test_abandoned_maintenance_is_closed_but_retry_budget_resets_per_checkpoint(test_db):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    task_id = test_db.background_task_enqueue("recipe_image_backfill", {"run_id": run_id})
    for _ in range(15):
        task = test_db.background_task_claim_next(lane="images")
        assert task["attempts"] == 1
        test_db.background_task_continue(task_id)
    test_db.background_task_finish(task_id, ok=False)
    assert test_db.reset_stale_maintenance() == 0
    assert test_db.maintenance_get(run_id)["status"] == "error"


def test_failed_image_task_finishes_its_maintenance_without_a_restart(test_db):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    test_db.maintenance_progress(run_id, {"phase": "generate", "completed_ids": [1], "generated": 1})
    task_id = test_db.background_task_enqueue("recipe_image_backfill", {"run_id": run_id})
    test_db.background_task_claim_next(lane="images")
    test_db.background_task_finish(task_id, ok=False, error="simulated dispatch failure")
    run = test_db.maintenance_get(run_id)
    assert run["status"] == "error", "An ended task must not leave the GUI polling a running maintenance forever"
    assert run["result"]["completed_ids"] == [1]
    assert run["result"]["error"] == "simulated dispatch failure"


def test_task_and_maintenance_failure_statuses_are_one_transaction(test_db):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    task_id = test_db.background_task_enqueue("recipe_image_backfill", {"run_id": run_id})
    test_db.background_task_claim_next(lane="images")
    with test_db.conn() as connection:
        connection.execute("""
            CREATE TRIGGER fail_maintenance_status BEFORE UPDATE OF status
            ON maintenance_runs BEGIN
                SELECT RAISE(ABORT, 'simulated maintenance failure');
            END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="maintenance failure"):
        test_db.background_task_finish(task_id, ok=False, error="simulated dispatch failure")
    assert test_db.background_task_get(task_id)["status"] == "running"
    assert test_db.maintenance_get(run_id)["status"] == "running"


def test_backfill_does_not_reload_all_recipe_content_for_each_checkpoint(test_db, monkeypatch):
    count = 12
    for index in range(count):
        test_db.recipe_upsert(
            url=f"https://example.invalid/batch/{index}", name=f"Recipe {index}",
            type="Hauptgericht", category="Test", folder_path=f"/isolated/{index}",
            description="Recipe content " * 500, thumb_filename=None,
            video_filename=None, source_added_at=None,
        )
    reads = {"full_rows": 0, "id_scans": 0}
    original_scan = test_db.recipes_for_image_backfill
    original_get = test_db.recipe_get

    def scan(**kwargs):
        rows = original_scan(**kwargs)
        reads["full_rows"] += sum(len(row) > 1 for row in rows)
        reads["id_scans"] += bool(kwargs.get("ids_only"))
        return rows

    def get(recipe_id):
        recipe = original_get(recipe_id)
        reads["full_rows"] += bool(recipe)
        return recipe

    monkeypatch.setattr(test_db, "recipes_for_image_backfill", scan)
    monkeypatch.setattr(test_db, "recipe_get", get)
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    monkeypatch.setattr(image_generation, "backup_recipe_image", lambda *_: None)
    monkeypatch.setattr(image_generation, "generate_recipe_image", lambda *_a, **_kw: {"ok": True})
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    payload = {"run_id": run_id, "batch_id": "bounded-read-batch"}
    for _ in range(count * 2):
        result = image_generation.run_image_backfill(payload, chunk_size=1)
    assert result["generated"] == count and result["phase"] == "done"
    assert reads == {"full_rows": count * 2, "id_scans": 1}
    assert image_generation.run_image_backfill(payload, chunk_size=1) == result
    assert reads == {"full_rows": count * 2, "id_scans": 1}, "A completed run needs no more recipe reads"
