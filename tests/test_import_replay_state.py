"""Import placeholders must not discard the work already saved by a job."""
from pathlib import Path

import pytest

from app.config_store import get_config
from app.db import get_db
from app.routes import api_pending
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.test_tenants import households  # noqa: F401


@pytest.mark.parametrize("finish_before_enqueue", [False, True])
def test_url_replay_retains_partial_analysis_and_media(households, monkeypatch, finish_before_enqueue):
    client, db, users, login = households
    db.user_set_role(users["anna"][0], "admin")
    original_get = get_config().get
    monkeypatch.setattr(get_config(), "get", lambda *keys, default=None:
                        1 if keys == ("web", "import_daily_limit") else original_get(*keys, default=default))
    monkeypatch.setattr(api_pending, "enqueue", lambda kind, payload, **values:
                        get_db().background_task_enqueue(kind, payload, **values))
    login("anna")
    url = "https://recipes.example/partial-analysis"
    first = client.post("/api/pending/import-url", json={"url": url})
    assert first.status_code == 200, first.text
    task_id = first.json()["task_id"]
    scoped = HouseholdDatabase(db, HouseholdScope(users["anna"][1]), import_owner=users["anna"][1])
    root = Path(get_config().get("paths", "temp_dir")) / "households" / str(users["anna"][1])
    root.mkdir(parents=True, exist_ok=True)
    video, frame = root / "source.mp4", root / "source.jpg"
    video.write_bytes(b"synthetic-media")
    frame.write_bytes(b"synthetic-frame")
    scoped.pending_add(url, content_type="recipe", description="Bereits gelesener Rezepttext",
                       video_path=str(video), frame_path=str(frame),
                       ai_suggestion={"name": "Ermittelte Suppe", "ingredients": [{"name": "Tomate"}],
                                      "steps": [{"instruction": "Kochen"}], "analysis_state": "review"})
    before = scoped.pending_get(url)
    if finish_before_enqueue:
        def finish(kind, payload, **values):
            db.background_task_finish(task_id, ok=True, result={})
            return get_db().background_task_enqueue(kind, payload, **values)
        monkeypatch.setattr(api_pending, "enqueue", finish)
    replay = client.post("/api/pending/import-url", json={"url": url})
    assert replay.status_code == (429 if finish_before_enqueue else 200), replay.text
    if not finish_before_enqueue:
        assert replay.json()["task_id"] == task_id
    assert scoped.pending_get(url) == before
    assert video.read_bytes() == b"synthetic-media" and frame.read_bytes() == b"synthetic-frame"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM import_budget_usage").fetchone()[0] == 1
    assert len(db.background_task_list()) == 1
