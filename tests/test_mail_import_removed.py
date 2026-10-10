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
from tools.retire_mail_import import (
    RETIRED_UNITS, TIMER_WRITE_CONTENT, TIMER_WRITE_OVERRIDE, prepare_web_service, retire_mail_import,
)


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


def _timer_write_override(units, content=TIMER_WRITE_CONTENT):
    path = units / TIMER_WRITE_OVERRIDE
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize("content", [TIMER_WRITE_CONTENT, TIMER_WRITE_CONTENT.rstrip(b"\n"), TIMER_WRITE_CONTENT.replace(b"\n", b"\r\n")])
def test_prepare_web_service_only_removes_exact_legacy_override_and_is_idempotent(tmp_path, content):
    units, rules = _retirement_tree(tmp_path)
    target = _timer_write_override(units, content)
    sibling = target.with_name("local-custom.conf")
    sibling.write_text("[Service]\nEnvironment=KEEP=yes\n")
    calls = []
    assert prepare_web_service(unit_root=units, run=_systemctl_mock(calls)) == {"ok": True, "removed_files": 1}
    assert not target.exists()
    assert sibling.read_text() == "[Service]\nEnvironment=KEEP=yes\n"
    assert calls == [["systemctl", "daemon-reload"]]
    assert all((units / name).exists() for name in RETIRED_UNITS)
    assert (rules / "49-scrapper-systemctl.rules").exists()
    assert prepare_web_service(unit_root=units, run=_systemctl_mock(calls))["removed_files"] == 0
    assert calls == [["systemctl", "daemon-reload"]]


@pytest.mark.parametrize("content", [
    TIMER_WRITE_CONTENT + b"Environment=LOCAL_SETTING=yes\n",
    b"[Service]\nReadWritePaths=/etc/systemd/system/scrapper-job.timer /srv/local\n",
    b"[Service]\nEnvironment=LOCAL_SETTING=yes\n",
])
def test_prepare_and_full_retirement_preserve_unknown_or_mixed_override_before_service_changes(tmp_path, content):
    units, rules = _retirement_tree(tmp_path)
    target = _timer_write_override(units, content)
    calls = []
    with pytest.raises(RuntimeError, match="contents"):
        prepare_web_service(unit_root=units, run=_systemctl_mock(calls))
    with pytest.raises(RuntimeError, match="contents"):
        retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock(calls))
    assert target.read_bytes() == content
    assert calls == []
    assert all((units / name).exists() for name in RETIRED_UNITS)


def test_prepare_web_service_rejects_other_owner_before_unlinking(tmp_path, monkeypatch):
    units, _ = _retirement_tree(tmp_path)
    target = _timer_write_override(units)
    monkeypatch.setattr(retirement, "ROOT_UID", -1)
    calls = []
    with pytest.raises(RuntimeError, match="ownership"):
        prepare_web_service(unit_root=units, run=_systemctl_mock(calls))
    assert target.read_bytes() == TIMER_WRITE_CONTENT
    assert calls == []


def test_prepare_web_service_rejects_hardlinked_override(tmp_path):
    import os
    units, _ = _retirement_tree(tmp_path)
    target = _timer_write_override(units)
    other = tmp_path / "keep.conf"
    os.link(target, other)
    calls = []
    with pytest.raises(RuntimeError, match="file type"):
        prepare_web_service(unit_root=units, run=_systemctl_mock(calls))
    assert target.read_bytes() == other.read_bytes() == TIMER_WRITE_CONTENT
    assert calls == []


def test_full_retirement_also_removes_known_override_for_other_installers(tmp_path):
    units, rules = _retirement_tree(tmp_path)
    target = _timer_write_override(units)
    result = retire_mail_import(unit_root=units, polkit_root=rules, run=_systemctl_mock([]))
    assert result["removed_files"] == 6
    assert not target.exists()


def test_updater_prepares_web_sandbox_after_snapshot_before_first_new_start():
    script = Path("proxmox/update-local.sh").read_text(encoding="utf-8")
    forward = script.split("trap restore_on_error ERR", 1)[1]
    prepare = forward.index('retire_mail_import.py" --prepare-web-service')
    assert forward.index('release_state.py" capture') < prepare
    assert prepare < forward.index("systemctl daemon-reload") < forward.index("systemctl restart scrapper-web.service")
    assert forward.index("required_methods =") < forward.index('retire_mail_import.py" --apply')


@pytest.mark.parametrize("timer_present", [True, False])
def test_rollback_preparation_only_removes_known_override_if_timer_is_missing(tmp_path, timer_present):
    units, _ = _retirement_tree(tmp_path)
    target = _timer_write_override(units)
    if not timer_present:
        (units / "scrapper-job.timer").unlink()
    calls = []
    result = prepare_web_service(unit_root=units, run=_systemctl_mock(calls), only_if_timer_missing=True)
    assert result["removed_files"] == int(not timer_present)
    assert target.exists() is timer_present
    assert calls == ([] if timer_present else [["systemctl", "daemon-reload"]])


def test_rollback_preparation_never_removes_mixed_override(tmp_path):
    units, _ = _retirement_tree(tmp_path)
    content = TIMER_WRITE_CONTENT + b"Environment=KEEP=yes\n"
    target = _timer_write_override(units, content)
    calls = []
    assert prepare_web_service(unit_root=units, run=_systemctl_mock(calls), only_if_timer_missing=True)["removed_files"] == 0
    (units / "scrapper-job.timer").unlink()
    with pytest.raises(RuntimeError, match="contents"):
        prepare_web_service(unit_root=units, run=_systemctl_mock(calls), only_if_timer_missing=True)
    assert target.read_bytes() == content
    assert calls == []
