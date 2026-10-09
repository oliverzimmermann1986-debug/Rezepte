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
