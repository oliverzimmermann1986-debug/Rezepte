from pathlib import Path

from app.db import Database
from app.jobs.scraper import ScraperJob, _sanitize


def bare_job(tmp_path: Path) -> ScraperJob:
    job = ScraperJob.__new__(ScraperJob)
    job.temp_dir = tmp_path / "temp"
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.db = Database(tmp_path / "data" / "db.sqlite")
    return job


def test_pending_video_and_attachment_stash_and_cleanup(tmp_path: Path):
    job = bare_job(tmp_path)
    source_dir = job.temp_dir / "download"
    source_dir.mkdir(parents=True)
    source = source_dir / "video.mp4"
    source.write_bytes(b"123")
    stashed = job._stash_for_pending(source)
    assert stashed and stashed.is_file() and stashed.parent == job.temp_dir / "pending"

    attachment = job._stash_bytes_for_pending(b"pdf", "../../CON.pdf", ".pdf")
    assert attachment.is_file() and attachment.suffix == ".pdf"
    assert ".." not in attachment.name

    job._remove_pending_files({"video_path": str(attachment)})
    assert not attachment.exists()


def test_sanitize_uses_single_safe_component():
    assert _sanitize("../A/B") == "AB"
    assert _sanitize("..") == "Unbekannt"
