import json
import subprocess
from pathlib import Path

from app.jobs import schedule_apply
from app.routes import api_schedule


class _Db:
    def __init__(self, path: Path):
        self.path = path


class _Config:
    def __init__(self):
        self.saved = None

    def set(self, section, key, value):
        self.saved = (section, key, value)

    def save(self):
        return None


def test_schedule_route_uses_privileged_helper(tmp_path: Path, monkeypatch):
    config = _Config()
    monkeypatch.setattr(api_schedule, "get_db", lambda: _Db(tmp_path / "scrapper.db"))
    monkeypatch.setattr(api_schedule, "get_config", lambda: config)
    monkeypatch.setattr(api_schedule, "_validate_oncalendar", lambda value: value)

    def fake_systemctl(*args):
        assert args == ("start", "scrapper-schedule-apply.service")
        request = tmp_path / "schedule-request.json"
        assert json.loads(request.read_text(encoding="utf-8")) == {"scraper": "*:0/5"}
        request.unlink()
        (tmp_path / "schedule-result.json").write_text(
            json.dumps({"ok": True, "scraper": "*:0/5"}), encoding="utf-8",
        )
        return {"ok": True, "stdout": "", "stderr": "", "cmd": "systemctl"}

    monkeypatch.setattr(api_schedule, "_systemctl", fake_systemctl)
    result = api_schedule.update_schedule(api_schedule.ScheduleUpdate(scraper="*:0/5"))

    assert result == {"ok": True, "changes": {"scraper": "*:0/5"}}
    assert config.saved == ("schedule", "scraper_interval", "*:0/5")


def test_read_schedule_uses_effective_nonempty_drop_in_value(tmp_path: Path, monkeypatch):
    timer = tmp_path / "scrapper-job.timer"
    timer.write_text("[Timer]\nOnCalendar=*:0/30\n", encoding="utf-8")
    override = tmp_path / "scrapper-job.timer.d" / "override.conf"
    override.parent.mkdir()
    override.write_text(
        "[Timer]\nOnCalendar=\nOnCalendar=*:0/5\n", encoding="utf-8",
    )
    assert api_schedule._read_oncalendar(str(timer)) == "*:0/5"


def test_failed_timer_restart_restores_previous_drop_in(tmp_path: Path, monkeypatch):
    request = tmp_path / "schedule-request.json"
    result = tmp_path / "schedule-result.json"
    drop_in = tmp_path / "scrapper-job.timer.d" / "override.conf"
    drop_in.parent.mkdir()
    original = b"[Timer]\nOnCalendar=\nOnCalendar=*:0/30\n"
    drop_in.write_bytes(original)
    request.write_text(json.dumps({"scraper": "*:0/5"}), encoding="utf-8")
    monkeypatch.setattr(schedule_apply, "REQUEST", request)
    monkeypatch.setattr(schedule_apply, "RESULT", result)
    monkeypatch.setattr(schedule_apply, "DROP_IN", drop_in)
    monkeypatch.setattr(schedule_apply, "_validate", lambda value: value)
    restart_calls = 0

    def fake_run(command, **kwargs):
        nonlocal restart_calls
        if command == ["systemctl", "restart", "scrapper-job.timer"]:
            restart_calls += 1
            if restart_calls == 1:
                raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(schedule_apply.subprocess, "run", fake_run)

    assert schedule_apply.main() == 1
    assert drop_in.read_bytes() == original
    assert json.loads(result.read_text(encoding="utf-8"))["ok"] is False
    assert restart_calls == 2
