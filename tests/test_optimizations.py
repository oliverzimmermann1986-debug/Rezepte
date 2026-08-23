"""Regressionstests für Performance-, Queue- und UI-Optimierungen."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from app.recipes.image_cache import (
    MAX_OTHER_SOURCE_PIXELS,
    MAX_SOURCE_DIMENSION,
    assert_safe_image_dimensions,
    ensure_thumbnail,
    normalize_image,
)
from app.recipes import sync_manager
from tests.conftest import _create_recipe


def test_search_relevance_is_applied_before_pagination(test_db):
    exact = _create_recipe(
        test_db, name="Tomate", folder_path="/tmp/exact", description="Einfach"
    )
    # Neuerer Eintrag, der nur in der Beschreibung matcht. Eine reine
    # Aktualitätssortierung würde ihn bei limit=1 fälschlich vorziehen.
    other = _create_recipe(
        test_db, name="Abendessen", folder_path="/tmp/other", description="mit Tomate"
    )
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET source_added_at=1 WHERE id=?", (exact["id"],))
        c.execute("UPDATE recipes SET source_added_at=9999999999 WHERE id=?", (other["id"],))

    rows = test_db.recipe_list(search="Tomate", limit=1, offset=0)
    assert [row["id"] for row in rows] == [exact["id"]]


def test_filesystem_sync_request_is_non_blocking_and_deduplicated(test_db, monkeypatch):
    sync_manager.reset_sync_state_for_tests()
    started = threading.Event()
    release = threading.Event()

    def slow_sync(db):
        started.set()
        release.wait(timeout=2)
        return {"scanned": 3, "added": 1, "updated": 2}

    monkeypatch.setattr(sync_manager, "sync_filesystem", slow_sync)
    before = time.monotonic()
    first = sync_manager.request_sync(force=True, db=test_db)
    elapsed = time.monotonic() - before
    assert first["accepted"] is True
    assert elapsed < 0.2
    assert started.wait(timeout=1)

    second = sync_manager.request_sync(force=True, db=test_db)
    assert second["accepted"] is False
    assert second["already_running"] is True

    release.set()
    state = sync_manager.wait_for_sync(timeout=2)
    assert state["running"] is False
    assert state["result"]["added"] == 1
    sync_manager.reset_sync_state_for_tests()


def test_background_tasks_are_claimed_and_recovered(test_db):
    task_id = test_db.background_task_enqueue("share_ingest", {"url": "https://example.test/x"})
    claimed = test_db.background_task_claim_next()
    assert claimed["id"] == task_id
    assert claimed["payload"]["url"].endswith("/x")
    assert test_db.background_task_claim_next() is None

    assert test_db.background_tasks_recover() == 1
    claimed_again = test_db.background_task_claim_next()
    assert claimed_again["id"] == task_id
    test_db.background_task_finish(task_id, ok=True, result={"saved": True})
    done = test_db.background_task_get(task_id)
    assert done["status"] == "ok"
    assert done["result"] == {"saved": True}


def test_background_task_retry_waits_until_next_attempt(test_db):
    task_id = test_db.background_task_enqueue(
        "share_ingest",
        {"url": "https://example.test/retry"},
    )
    claimed = test_db.background_task_claim_next()
    assert claimed["id"] == task_id

    test_db.background_task_retry(
        task_id,
        delay_seconds=60,
        error="Scraper belegt",
        result={"retry": True},
    )

    assert test_db.background_task_claim_next() is None
    waiting = test_db.background_task_get(task_id)
    assert waiting["status"] == "queued"
    assert waiting["next_attempt_at"] is not None
    with test_db.conn() as connection:
        connection.execute(
            "UPDATE background_tasks SET next_attempt_at=0 WHERE id=?",
            (task_id,),
        )
    retried = test_db.background_task_claim_next()
    assert retried["id"] == task_id
    assert retried["attempts"] == 2


def test_thumbnail_cache_is_atomic_and_reused(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (1200, 800), (255, 0, 0, 180)).save(source)
    normalized = normalize_image(source, tmp_path / "thumb.jpg")
    first = ensure_thumbnail(normalized, 400)
    mtime = first.stat().st_mtime_ns
    second = ensure_thumbnail(normalized, 400)
    assert first == second
    assert second.stat().st_mtime_ns == mtime
    with Image.open(second) as image:
        assert image.width == 400
        assert image.mode == "RGB"


def test_image_dimension_guard_rejects_dimensions_and_pixel_bombs():
    class FakeImage:
        format = "PNG"

        def __init__(self, size):
            self.size = size

    with pytest.raises(ValueError, match="abmessungen"):
        assert_safe_image_dimensions(FakeImage((MAX_SOURCE_DIMENSION + 1, 10)))
    with pytest.raises(ValueError, match="Pixelbudget"):
        assert_safe_image_dimensions(
            FakeImage((6000, MAX_OTHER_SOURCE_PIXELS // 6000 + 1))
        )


def test_ui_loads_runtime_helpers_and_accessible_status_region():
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    runtime = (root / "runtime.js").read_text(encoding="utf-8")
    assert '/static/runtime.js?v={VERSION}' in html
    assert 'aria-live="polite"' in html
    assert "initAccessibleDialogs" in runtime
    assert "createPoller" in runtime
    assert "_listController?.abort()" in js
    assert "/api/recipes/sync/status" in js
