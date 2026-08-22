import os
import time
from email.message import EmailMessage

from app.core.email_processor import _decode_attachment_payload
from app.core.temp_cleanup import cleanup_temp_files


def _age(path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_cleanup_keeps_active_pending_and_removes_orphans(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    active = pending / "active.jpg"
    orphan = pending / "orphan.pdf"
    fresh = pending / "fresh.jpg"
    active.write_bytes(b"active")
    orphan.write_bytes(b"orphan")
    fresh.write_bytes(b"fresh")
    _age(active, 2 * 3600)
    _age(orphan, 2 * 3600)

    result = cleanup_temp_files(tmp_path, [active])

    assert result["ok"] is True
    assert result["removed"] == 1
    assert result["bytes_removed"] == len(b"orphan")
    assert active.exists()
    assert fresh.exists()
    assert not orphan.exists()


def test_cleanup_removes_old_work_directory_but_not_recent_one(tmp_path):
    old = tmp_path / "job-old"
    recent = tmp_path / "job-recent"
    old.mkdir()
    recent.mkdir()
    (old / "data.bin").write_bytes(b"1234")
    (recent / "data.bin").write_bytes(b"5678")
    _age(old, 8 * 86400)

    result = cleanup_temp_files(tmp_path, [])

    assert result["removed"] == 1
    assert not old.exists()
    assert recent.exists()


def test_pending_file_paths_only_returns_active_status(test_db, tmp_path):
    active = tmp_path / "active.jpg"
    skipped = tmp_path / "skipped.jpg"
    test_db.pending_add("manual-upload://active", "recipe", video_path=str(active))
    test_db.pending_add("manual-upload://skipped", "recipe", video_path=str(skipped))
    test_db.pending_resolve("manual-upload://skipped", "auto_skipped")

    assert test_db.pending_file_paths() == [str(active)]


def test_attachment_decode_enforces_decoded_limit():
    message = EmailMessage()
    message.add_attachment(b"123456789", maintype="image", subtype="jpeg", filename="x.jpg")
    part = next(message.iter_attachments())

    assert _decode_attachment_payload(part, 9) == b"123456789"
    assert _decode_attachment_payload(part, 8) is None
