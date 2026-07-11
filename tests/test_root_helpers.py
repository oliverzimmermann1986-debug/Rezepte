import importlib.util
from importlib.machinery import SourceFileLoader
import os
import sys
from pathlib import Path

import pytest


def load_script(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_schedule_helper_rejects_symlink_request(tmp_path: Path, monkeypatch):
    module = load_script(
        "scrapper_set_schedule_test",
        Path(__file__).parents[1] / "systemd" / "scrapper-set-schedule",
    )
    target = tmp_path / "secret"
    target.write_text("*-*-* 01:00:00", encoding="utf-8")
    request = tmp_path / "request"
    request.symlink_to(target)
    monkeypatch.setattr(module, "REQUEST", request)
    monkeypatch.setattr(sys, "argv", ["helper", "--request-file", str(request)])

    with pytest.raises(OSError):
        module.consume_request()
    assert not request.exists()
    assert target.read_text(encoding="utf-8") == "*-*-* 01:00:00"


def test_hdd_helper_rejects_symlink_request(tmp_path: Path, monkeypatch):
    module = load_script(
        "scrapper_hdd_action_test",
        Path(__file__).parents[1] / "systemd" / "scrapper-hdd-action",
    )
    target = tmp_path / "secret"
    target.write_text('{"action":"mount","request_id":"1234567890abcdef"}', encoding="utf-8")
    request = tmp_path / "request"
    request.symlink_to(target)
    monkeypatch.setattr(module, "REQUEST", request)
    monkeypatch.setattr(sys, "argv", ["helper", "--request-file", str(request)])

    with pytest.raises(OSError):
        module._consume_request()
    assert not request.exists()
    assert target.is_file()


def test_hdd_allow_file_must_not_be_group_writable(tmp_path: Path, monkeypatch):
    module = load_script(
        "scrapper_hdd_action_permissions_test",
        Path(__file__).parents[1] / "systemd" / "scrapper-hdd-action",
    )
    allow = tmp_path / "allow"
    allow.write_text("/mnt/disk\n", encoding="utf-8")
    os.chmod(allow, 0o664)
    actual = allow.lstat()
    fields = list(actual)
    fields[4] = 0  # st_uid=root, unabhängig vom CI-Runner
    root_owned = os.stat_result(fields)
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: root_owned if self == allow else original_lstat(self),
    )
    with pytest.raises(ValueError, match="writable"):
        module._regular_root_file(allow, 512)
