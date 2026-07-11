from pathlib import Path

import yaml

import app.config_store as config_store
from app.config_store import DEFAULT_CONFIG_PATH, validate_config


def load_default():
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_default_config_is_valid_and_has_no_file_sync_block():
    data = load_default()
    assert "backup" not in data
    assert validate_config(data) == []


def test_config_store_recovers_invalid_yaml_from_backup(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    backup_path = tmp_path / "config.yaml.bak"
    config_path.write_text("web: [broken\n", encoding="utf-8")
    backup_path.write_text(yaml.safe_dump({"web": {"username": "restored"}}), encoding="utf-8")
    store = config_store.ConfigStore(config_path)
    assert store.get("web", "username") == "restored"
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["web"]["username"] == "restored"
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_missing_config_is_created_private(tmp_path: Path, monkeypatch):
    default_path = tmp_path / "default.yaml"
    default_path.write_text("web:\n  username: admin\n", encoding="utf-8")
    monkeypatch.setattr(config_store, "DEFAULT_CONFIG_PATH", default_path)
    config_path = tmp_path / "nested" / "config.yaml"
    store = config_store.ConfigStore(config_path)
    assert store.get("web", "username") == "admin"
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_trusted_proxies_accepts_comma_separated_text():
    data = load_default()
    data["web"]["trusted_proxies"] = "127.0.0.1, 10.0.0.0/8"
    assert validate_config(data) == []


def test_rejects_hdd_mountpoint_traversal():
    data = load_default()
    data["external_hdd"]["mount_point"] = "/mnt/../../etc"
    errors = validate_config(data)
    assert any("mount_point" in error for error in errors)
