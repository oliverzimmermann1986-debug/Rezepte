"""Fehler vor Thread-Start dürfen keine ewigen Running-Locks erzeugen."""

import threading

import pytest
from fastapi import HTTPException

from app.recipes import sync_manager
from app.routes import api_jobs
from app import main


class _BrokenThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise RuntimeError("kein Thread verfügbar")


def test_sync_manager_resets_running_state_when_thread_start_fails(monkeypatch):
    sync_manager.reset_sync_state_for_tests()
    monkeypatch.setattr(sync_manager.threading, "Thread", _BrokenThread)

    with pytest.raises(RuntimeError, match="kein Thread"):
        sync_manager.request_sync(force=True)

    state = sync_manager.sync_status()
    assert state["running"] is False
    assert state["queued"] is False
    assert "thread start failed" in state["error"]
    sync_manager.reset_sync_state_for_tests()


def test_scraper_route_releases_lock_when_thread_start_fails(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.finished = []

        def job_start(self, _kind):
            return 42

        def job_finish(self, job_id, status, summary):
            self.finished.append((job_id, status, summary))

    fake_db = FakeDb()
    lock = threading.Lock()
    monkeypatch.setitem(api_jobs._locks, "scraper", lock)
    monkeypatch.setattr(api_jobs, "get_db", lambda: fake_db)
    monkeypatch.setattr(api_jobs.threading, "Thread", _BrokenThread)

    with pytest.raises(HTTPException) as exc:
        api_jobs.run_scraper()
    assert exc.value.status_code == 500
    assert fake_db.finished[0][0:2] == (42, "error")
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_trash_cleanup_thread_can_be_stopped_without_waiting_for_daily_sleep():
    main._stop_trash_cleanup_thread(timeout=1)
    main._start_trash_cleanup_thread()
    assert main._trash_cleanup_thread is not None
    assert main._trash_cleanup_thread.is_alive()
    assert main._stop_trash_cleanup_thread(timeout=1) is True
    assert main._trash_cleanup_thread_started is False


def test_file_logging_failure_falls_back_without_raising(monkeypatch, tmp_path):
    def denied_handler(*_args, **_kwargs):
        raise PermissionError("Log-Volume ist schreibgeschützt")

    monkeypatch.setattr(main, "RotatingFileHandler", denied_handler)

    handler, error = main._create_file_log_handler(tmp_path / "logs")

    assert handler is None
    assert isinstance(error, PermissionError)
