"""Statische Sicherheitsregressionen für die ausgelieferten Units."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_service_never_trusts_forwarded_headers_from_every_peer():
    unit = (ROOT / "systemd" / "scrapper-web.service").read_text(encoding="utf-8")
    assert "--forwarded-allow-ips=*" not in unit
    assert "SCRAPPER_FORWARDED_ALLOW_IPS=127.0.0.1" in unit
    assert "--forwarded-allow-ips=${SCRAPPER_FORWARDED_ALLOW_IPS}" in unit
    assert "EnvironmentFile=-/opt/scrapper/data/web.env" in unit


def test_mutating_services_are_sandboxed_and_resource_bounded():
    for name in (
        "scrapper-web.service",
        "scrapper-job.service",
        "scrapper-db-backup.service",
        "video-archiver.service",
    ):
        unit = (ROOT / "systemd" / name).read_text(encoding="utf-8")
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert "MemoryMax=" in unit
        assert "CPUQuota=" in unit
        assert "TasksMax=" in unit


def test_video_archiver_syncs_read_only_recipe_links_before_download():
    unit = (ROOT / "systemd" / "video-archiver.service").read_text(encoding="utf-8")
    assert "ExecStartPre=+/opt/video-archiver/venv/bin/python -m video_archiver" in unit
    assert "--recipes-db /opt/scrapper/data/scrapper.db" in unit
    assert "--queue-user videoarchive" in unit


def test_schedule_permissions_are_limited_to_the_single_timer():
    web = (ROOT / "systemd" / "scrapper-web.service").read_text(encoding="utf-8")
    rule = (ROOT / "systemd" / "49-scrapper-systemctl.rules").read_text(encoding="utf-8")
    installer = (ROOT / "proxmox" / "install.sh").read_text(encoding="utf-8")

    assert "ReadWritePaths=/opt/scrapper /mnt /etc/systemd/system/scrapper-job.timer" in web
    assert 'action.lookup("unit") === "scrapper-job.timer"' in rule
    assert "org.freedesktop.systemd1.reload-daemon" in rule
    assert "sudoers-scrapper" not in installer
    assert "49-scrapper-systemctl.rules" in installer


def test_repository_normalizes_text_files_to_lf():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attributes
