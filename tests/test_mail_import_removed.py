"""Retired mail entry points stay inert while explicit imports remain usable."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.core.content_urls import is_content_url, normalize_content_url
from app.jobs.scraper import ScraperJob
from app.routes import api_config
from tools import retire_mail_import as retirement
from tools.retire_mail_import import RETIRED_UNITS, retire_mail_import


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/test/mail"),
    ("POST", "/api/jobs/scraper/run"),
    ("POST", "/api/jobs/scraper/cancel"),
    ("GET", "/api/jobs/scraper/progress"),
    ("GET", "/api/schedule"),
    ("PUT", "/api/schedule"),
])
def test_retired_mail_routes_cannot_start_work(client, monkeypatch, method, path):
    import subprocess
    import requests
    from app.db import Database

    def forbidden(*_args, **_kwargs):
        pytest.fail("A retired mail endpoint reached a job/network/service boundary")

    monkeypatch.setattr(Database, "job_start", forbidden)
    monkeypatch.setattr(Database, "background_task_enqueue", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    response = client.request(method, path, json={"account": "recipe", "scraper": "*:0/5"})
    assert response.status_code == 404


def test_runtime_has_no_mail_client_or_automatic_scraper_entrypoint():
    for module in ("app.core.email_processor", "app.jobs.scraper_cli", "app.jobs.schedule_apply"):
        assert importlib.util.find_spec(module) is None
    assert not hasattr(ScraperJob, "run")
    assert callable(ScraperJob.process_url) and callable(ScraperJob.process_attachment)
    config = yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
    assert "mail" not in config and "schedule" not in config


@pytest.mark.parametrize("url,expected", [
    ("https://www.tiktok.com/@cook/video/123?utm_source=share", "https://www.tiktok.com/@cook/video/123"),
    ("https://www.instagram.com/reel/ABC/?utm_source=copy", "https://www.instagram.com/reel/ABC/"),
    ("file:///private/recipe.pdf", None),
    ("mail-attachment://old-message::recipe.pdf", None),
])
def test_direct_url_normalization_survives_mail_removal(url, expected):
    assert normalize_content_url(url) == expected
    assert is_content_url(url) is (expected is not None)


def test_config_response_hides_retired_settings_without_deleting_legacy_storage(monkeypatch):
    legacy = {"web": {"password": "unused-secret"},
              "mail": {"recipe": {"enabled": True, "password": "old-mail-secret"}},
              "schedule": {"scraper_interval": "*:0/5"}, "pdf": {"auto_rotate": True}}
    original = copy.deepcopy(legacy)
    monkeypatch.setattr(api_config, "get_config", lambda: SimpleNamespace(all=lambda: legacy))
    result = api_config.read_config()
    assert "mail" not in result and "schedule" not in result
    assert result["pdf"] == {"auto_rotate": True}
    assert result["web"]["password"] == api_config.MASKED
    assert legacy == original


@pytest.mark.parametrize("field", ["mail", "schedule"])
def test_retired_settings_are_rejected_before_config_writes(client, monkeypatch, field):
    def forbidden():
        pytest.fail("Retired config update touched the stored configuration")

    monkeypatch.setattr(api_config, "get_config", forbidden)
    response = client.put("/api/config", json={field: {"enabled": True}})
    assert response.status_code == 400
    assert "entfernt" in response.json()["detail"]


@pytest.fixture(autouse=True)
def synthetic_unit_owner(monkeypatch, tmp_path):
    # CI runs without root; represent root ownership with this test tree's UID.
    monkeypatch.setattr(retirement, "ROOT_UID", tmp_path.stat().st_uid)


def _retirement_tree(tmp_path):
    units = tmp_path / "units"
    rules = tmp_path / "rules"
    units.mkdir()
    rules.mkdir()
    for name in RETIRED_UNITS:
        (units / name).write_text("synthetic retired unit", encoding="utf-8")
    override = units / "scrapper-job.timer.d"
    override.mkdir()
    (override / "override.conf").write_text("synthetic override", encoding="utf-8")
    (rules / "49-scrapper-systemctl.rules").write_text("synthetic privilege", encoding="utf-8")
    (units / "scrapper-db-backup.timer").write_text("preserve backup schedule", encoding="utf-8")
    return units, rules


def _systemctl_mock(calls, *, missing=False):
    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "show":
            return SimpleNamespace(returncode=1 if missing else 0, stdout="not-found\n" if missing else "loaded\n")
        return SimpleNamespace(returncode=3 if command[1] == "is-active" else 0, stdout="")
    return run


def test_mail_unit_retirement_is_idempotent_and_preserves_backup_and_unknown_files(tmp_path):
    units, rules = _retirement_tree(tmp_path)
    unrelated = units / "scrapper-job.timer.d" / "local-note.txt"
    unrelated.write_text("keep independently managed file", encoding="utf-8")
    calls = []
    result = retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock(calls))
    assert result == {"ok": True, "mail_units_inactive": True, "removed_files": 5}
    assert (units / "scrapper-db-backup.timer").read_text() == "preserve backup schedule"
    assert unrelated.read_text() == "keep independently managed file"
    assert [call[3] for call in calls if call[1:3] == ["disable", "--now"]] == list(RETIRED_UNITS)
    assert not any("scrapper-db-backup.timer" in command for command in calls)
    again = retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock([], missing=True))
    assert again["removed_files"] == 0 and again["ok"]


def test_mail_unit_retirement_removes_only_empty_override_directory(tmp_path):
    units, rules = _retirement_tree(tmp_path)
    retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock([]))
    assert not (units / "scrapper-job.timer.d").exists()


def test_mail_unit_retirement_refuses_non_regular_target_before_service_changes(tmp_path):
    units, rules = _retirement_tree(tmp_path)
    target = units / RETIRED_UNITS[0]
    target.unlink()
    target.mkdir()
    calls = []
    with pytest.raises(RuntimeError, match="file type"):
        retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock(calls))
    assert calls == []
    assert (rules / "49-scrapper-systemctl.rules").is_file()


def test_mail_unit_retirement_refuses_active_unit_before_unlinking(tmp_path):
    units, rules = _retirement_tree(tmp_path)
    def still_running(command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="loaded\n" if command[1] == "show" else "")
    with pytest.raises(RuntimeError, match="still active"):
        retire_mail_import(unit_root=units, polkit_root=rules, run=still_running)
    assert all((units / name).is_file() for name in RETIRED_UNITS)


def test_installers_never_enable_retired_mail_units_and_retire_after_update_gates():
    for name in ("install.sh", "update-local.sh", "setup-review-instance.sh"):
        source = (Path("proxmox") / name).read_text(encoding="utf-8")
        assert "systemctl enable --now scrapper-job.timer" not in source
        assert 'retire_mail_import.py" --apply' in source
        assert 'systemd/scrapper-job.service"' not in source
        assert 'systemd/scrapper-schedule-apply.service"' not in source
    update = Path("proxmox/update-local.sh").read_text(encoding="utf-8")
    assert update.index('retire_mail_import.py" --apply') > update.index("required_methods =")
    assert update.index("restore_timer_activity 1") < update.index('retire_mail_import.py" --apply')


def test_live_status_has_no_mail_job_reads():
    from app.routes.api_events import _status_snapshot
    calls = []
    def running(kind):
        calls.append(kind)
        return kind == "reanalyze"
    db = SimpleNamespace(job_running=running, pending_count=lambda: 3)
    assert _status_snapshot(db) == {"reanalyze": True, "pending_count": 3}
    assert calls == ["reanalyze"]


def test_mail_unit_retirement_refuses_other_owner_before_service_changes(tmp_path, monkeypatch):
    units, rules = _retirement_tree(tmp_path)
    monkeypatch.setattr(retirement, "ROOT_UID", -1)
    calls = []
    with pytest.raises(RuntimeError, match="ownership"):
        retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock(calls))
    assert calls == []
    assert all((units / name).is_file() for name in RETIRED_UNITS)
