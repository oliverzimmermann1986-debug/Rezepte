import json
import os
import sqlite3
import stat
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from video_archiver import ArchiveQueue, VideoArchiver, normalize_supported_url


def test_archiver_url_validation_is_strict():
    assert normalize_supported_url(
        "https://www.tiktok.com/@koch/video/123?share=1#comments"
    ) == "https://www.tiktok.com/@koch/video/123"
    assert normalize_supported_url("https://www.instagram.com/reel/ABC/?igsh=x") == (
        "https://www.instagram.com/reel/ABC/"
    )
    assert normalize_supported_url("https://instagram.com.evil.test/reel/ABC") is None
    assert normalize_supported_url("https://instagram.com@evil.test/reel/ABC") is None
    assert normalize_supported_url("http://www.tiktok.com/@koch/video/123") is None
    assert normalize_supported_url("https://www.tiktok.com/@koch") is None


def test_queue_is_idempotent_and_requeues_changed_url(tmp_path: Path):
    queue = ArchiveQueue(tmp_path / "queue.db")
    first = queue.enqueue(42, "https://www.tiktok.com/@koch/video/123")
    assert first["status"] == "queued"
    claimed = queue.claim()
    assert claimed and claimed["attempts"] == 1
    queue.complete(42, tmp_path / "42.mp4")

    same = queue.enqueue(42, "https://www.tiktok.com/@koch/video/123?tracking=1")
    assert same["status"] == "completed"
    changed = queue.enqueue(42, "https://www.tiktok.com/@koch/video/456")
    assert changed["status"] == "queued"
    assert changed["attempts"] == 0
    assert changed["archive_path"] is None


def test_queue_syncs_new_recipe_links_without_duplicate_events(tmp_path: Path):
    recipes_db = tmp_path / "recipes.db"
    with sqlite3.connect(recipes_db) as connection:
        connection.execute(
            "CREATE TABLE recipes (id INTEGER PRIMARY KEY, url TEXT, deleted_at REAL)"
        )
        connection.executemany(
            "INSERT INTO recipes(id, url, deleted_at) VALUES (?, ?, ?)",
            [
                (202, "https://vm.tiktok.com/ZGdxp79TJ/?share=1", None),
                (203, "https://www.instagram.com/reel/ABC/?igsh=test", None),
                (204, "https://example.test/video", None),
                (205, "https://www.tiktok.com/@koch/video/deleted", 1.0),
            ],
        )

    queue = ArchiveQueue(tmp_path / "queue.db")
    first = queue.sync_from_recipes_db(recipes_db)
    assert first == {
        "seen": 3,
        "eligible": 2,
        "enqueued": 2,
        "unchanged": 0,
        "ignored": 1,
    }
    event_count = len(queue.events())

    second = queue.sync_from_recipes_db(recipes_db)
    assert second["enqueued"] == 0
    assert second["unchanged"] == 2
    assert len(queue.events()) == event_count

    with sqlite3.connect(recipes_db) as connection:
        connection.execute(
            "UPDATE recipes SET url=? WHERE id=202",
            ("https://www.tiktok.com/@koch/video/new",),
        )
    changed = queue.sync_from_recipes_db(recipes_db)
    assert changed["enqueued"] == 1
    assert queue.get(202)["url"] == "https://www.tiktok.com/@koch/video/new"


def test_worker_names_video_by_recipe_id_and_writes_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    executable = tmp_path / "yt-dlp.exe"
    executable.write_bytes(b"fake")
    queue = ArchiveQueue(tmp_path / "queue.db")
    queue.enqueue(35852573, "https://www.tiktok.com/@koch/video/123")

    def fake_run(command, **kwargs):
        template = Path(command[command.index("--output") + 1])
        template.with_name("download.mp4").write_bytes(b"private video")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("video_archiver.worker.subprocess.run", fake_run)
    # Der Erfolgsfall darf nicht vom freien Speicher des Testrechners abhängen;
    # die produktiven Grenzwerte werden separat im Low-Space-Test geprüft.
    worker = VideoArchiver(
        queue,
        tmp_path / "archive",
        str(executable),
        max_bytes=1024,
        free_space_reserve_bytes=1024,
    )
    result = worker.process_one()

    assert result and result["status"] == "completed"
    video = tmp_path / "archive" / "35852573.mp4"
    metadata = json.loads((tmp_path / "archive" / "35852573.json").read_text("utf-8"))
    assert video.read_bytes() == b"private video"
    assert metadata["recipe_id"] == 35852573
    assert metadata["source_url"] == "https://www.tiktok.com/@koch/video/123"
    assert len(metadata["sha256"]) == 64
    if os.name != "nt":
        assert stat.S_IMODE(video.stat().st_mode) == 0o640
        assert stat.S_IMODE((tmp_path / "archive").stat().st_mode) == 0o750
    assert queue.events()[0]["message"] == "Archivierung abgeschlossen"


def test_worker_never_overwrites_a_conflicting_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    executable = tmp_path / "yt-dlp.exe"
    executable.write_bytes(b"fake")
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "7.mp4").write_bytes(b"existing")
    (archive / "7.json").write_text(
        json.dumps({
            "recipe_id": 7,
            "source_url": "https://www.tiktok.com/@koch/video/other",
            "sha256": "wrong",
        }),
        encoding="utf-8",
    )
    queue = ArchiveQueue(tmp_path / "queue.db")
    queue.enqueue(7, "https://www.tiktok.com/@koch/video/123")
    monkeypatch.setattr(
        "video_archiver.worker.subprocess.run",
        lambda *args, **kwargs: pytest.fail("Download darf bei Konflikt nicht starten"),
    )

    result = VideoArchiver(queue, archive, str(executable), max_attempts=1).process_one()
    assert result and result["status"] == "failed"
    assert (archive / "7.mp4").read_bytes() == b"existing"
    assert queue.get(7)["status"] == "failed"


def test_claim_marks_exhausted_stale_download_as_failed(tmp_path: Path):
    queue = ArchiveQueue(tmp_path / "queue.db")
    queue.enqueue(9, "https://www.tiktok.com/@koch/video/123")
    with sqlite3.connect(queue.path) as connection:
        connection.execute(
            """
            UPDATE archive_jobs
               SET status='downloading', attempts=3, updated_at=0
             WHERE recipe_id=9
            """
        )

    assert queue.claim(max_attempts=3, stale_after=60) is None
    failed = queue.get(9)
    assert failed["status"] == "failed"
    assert "maximale Versuche" in failed["error"]
    assert queue.events()[0]["level"] == "error"


def test_worker_refuses_download_when_archive_space_is_too_low(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    executable = tmp_path / "yt-dlp.exe"
    executable.write_bytes(b"fake")
    queue = ArchiveQueue(tmp_path / "queue.db")
    queue.enqueue(11, "https://www.tiktok.com/@koch/video/123")
    monkeypatch.setattr(
        "video_archiver.worker.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=99, free=1),
    )
    monkeypatch.setattr(
        "video_archiver.worker.subprocess.run",
        lambda *args, **kwargs: pytest.fail("Download darf ohne Speicherreserve nicht starten"),
    )

    result = VideoArchiver(
        queue,
        tmp_path / "archive",
        str(executable),
        max_attempts=1,
        max_bytes=10,
        free_space_reserve_bytes=10,
    ).process_one()

    assert result and result["status"] == "failed"
    assert "Zu wenig freier Speicher" in result["error"]
