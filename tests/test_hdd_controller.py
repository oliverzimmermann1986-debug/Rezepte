import json
import threading
import time
from pathlib import Path

import yaml

import app.config_store as config_store
import app.core.hdd_controller as hdd_module
from app.core.hdd_controller import HDDController


def test_hdd_action_rejects_mountpoint_not_in_root_allow_file(tmp_path: Path, monkeypatch):
    allow = tmp_path / "allow"
    allow.write_text("/mnt/allowed\n", encoding="utf-8")
    monkeypatch.setattr(hdd_module, "_ROOT_ALLOW_FILE", allow)
    ctl = HDDController({"mount_point": "/mnt/other"})
    result = ctl._root_mount_action("mount")
    assert result["ok"] is False
    assert "nicht root-freigegeben" in result["error"]


def test_hdd_action_uses_request_result_handshake(tmp_path: Path, monkeypatch):
    mountpoint = tmp_path / "disk"
    mountpoint.mkdir()
    allow = tmp_path / "allow"
    allow.write_text(str(mountpoint) + "\n", encoding="utf-8")
    monkeypatch.setattr(hdd_module, "_ROOT_ALLOW_FILE", allow)

    config_path = tmp_path / "data" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text(yaml.safe_dump({"web": {}}), encoding="utf-8")
    monkeypatch.setattr(config_store, "_config", config_store.ConfigStore(config_path))

    def root_simulator():
        request = config_path.parent / "hdd-action.request"
        deadline = time.time() + 3
        while time.time() < deadline and not request.exists():
            time.sleep(0.01)
        payload = json.loads(request.read_text(encoding="utf-8"))
        request.unlink()
        (config_path.parent / "hdd-action.result").write_text(json.dumps({
            "ok": True,
            "action": payload["action"],
            "request_id": payload["request_id"],
            "mount_point": str(mountpoint),
            "mounted": True,
        }), encoding="utf-8")

    worker = threading.Thread(target=root_simulator, daemon=True)
    worker.start()
    ctl = HDDController({"mount_point": str(mountpoint)})
    result = ctl._root_mount_action("mount")
    worker.join(timeout=2)
    assert result["ok"] is True
    assert result["mounted"] is True
