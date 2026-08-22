from pathlib import Path

from app.routes import api_schedule


def test_failed_timer_restart_restores_previous_schedule(
    tmp_path: Path, monkeypatch
):
    timer = tmp_path / "scrapper-job.timer"
    original = "[Timer]\nOnCalendar=*:0/30\n\n[Install]\nWantedBy=timers.target\n"
    timer.write_text(original, encoding="utf-8")
    monkeypatch.setitem(api_schedule.TIMER_FILES, "scraper", str(timer))
    monkeypatch.setattr(api_schedule, "_validate_oncalendar", lambda value: value)

    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        if args == ("restart", "scrapper-job.timer") and calls.count(args) == 1:
            return {"ok": False, "stdout": "", "stderr": "simulierter Fehler", "cmd": "systemctl"}
        return {"ok": True, "stdout": "", "stderr": "", "cmd": "systemctl"}

    monkeypatch.setattr(api_schedule, "_systemctl", fake_systemctl)

    result = api_schedule.update_schedule(api_schedule.ScheduleUpdate(scraper="*:0/5"))

    assert result["ok"] is False
    assert "zurückgerollt" in result["error"]
    assert timer.read_text(encoding="utf-8") == original
    assert calls == [
        ("daemon-reload",),
        ("restart", "scrapper-job.timer"),
        ("daemon-reload",),
        ("restart", "scrapper-job.timer"),
    ]
