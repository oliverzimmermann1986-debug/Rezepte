from pathlib import Path

import pytest

import app.jobs.locks as locks


def test_second_lock_attempt_does_not_truncate_owner_pid(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(locks, "LOCK_DIR", tmp_path)
    with locks.file_lock_or_none("backup") as first:
        assert first is not None
        lock_path = tmp_path / "backup.lock"
        owner = lock_path.read_text(encoding="utf-8")
        with locks.file_lock_or_none("backup") as second:
            assert second is None
        assert lock_path.read_text(encoding="utf-8") == owner


def test_invalid_lock_name_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(locks, "LOCK_DIR", tmp_path)
    with pytest.raises(ValueError):
        with locks.file_lock_or_none("../escape"):
            pass
