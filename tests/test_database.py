import sqlite3
import time
from pathlib import Path

from app.db import Database


def test_database_pending_jobs_and_verified_backup(tmp_path: Path):
    db = Database(tmp_path / "data" / "scrapper.db")
    db.pending_add(
        "https://www.tiktok.com/@x/video/1", "recipe",
        description="test", video_path=str(tmp_path / "pending.mp4"),
        ai_suggestion={"confidence": 0.2},
    )
    assert db.pending_count() == 1
    assert db.pending_get("https://www.tiktok.com/@x/video/1")["ai_suggestion"]["confidence"] == 0.2

    job_id = db.job_start("reanalyze")
    db.job_finish(job_id, "error", {"ok": False, "pairs": [{"name": "x", "ok": False}]})
    assert db.job_get(job_id)["status"] == "error"

    backup = db.backup_to(tmp_path / "backup.db.gz", compress=True, verify=True)
    assert backup["ok"] and backup["verified"] is True
    assert Path(backup["dest"]).is_file()


def test_stale_job_recovery_preserves_protected_active_job(tmp_path: Path):
    db = Database(tmp_path / "jobs.db")
    old_id = db.job_start("reanalyze")
    active_id = db.job_start("reanalyze")
    scraper_id = db.job_start("scraper")

    reset = db.reset_stale_running({active_id})

    assert reset == 2
    assert db.job_get(active_id)["status"] == "running"
    assert db.job_get(old_id)["status"] == "error"
    assert db.job_get(scraper_id)["status"] == "error"
