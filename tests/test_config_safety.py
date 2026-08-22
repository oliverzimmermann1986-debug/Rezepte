import os
import stat

from app.config_store import ConfigStore
from app.routes.api_config import _deep_merge, _unmask


def test_partial_config_patch_preserves_unmentioned_sections_and_secrets():
    current = {
        "web": {"secret_key": "secret", "password": "hash", "bind_port": 8000},
        "mail": {"recipe": {"username": "mail", "password": "mail-secret"}},
        "ai": {"openai": {"api_key": "sk-secret", "model": "old"}},
    }

    merged = _unmask(
        _deep_merge(current, {"ai": {"openai": {"model": "new"}}}),
        current,
    )

    assert merged["ai"]["openai"]["model"] == "new"
    assert merged["ai"]["openai"]["api_key"] == "sk-secret"
    assert merged["web"] == current["web"]
    assert merged["mail"] == current["mail"]


def test_config_save_is_atomic_private_and_leaves_no_fixed_temp_file(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.replace({"web": {"secret_key": "s" * 32}, "mail": {"password": "private"}})

    store.save()

    assert path.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert not list(tmp_path.glob(".config.yaml.tmp-*"))
    assert ConfigStore(path).all()["mail"]["password"] == "private"
