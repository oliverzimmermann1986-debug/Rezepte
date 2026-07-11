from pathlib import Path

import yaml

import app.auth as auth
import app.config_store as config_store


def test_password_change_invalidates_session(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    data = {
        "web": {
            "username": "oliver",
            "password": auth.hash_password("a-very-long-password"),
            "secret_key": "x" * 64,
        }
    }
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    store = config_store.ConfigStore(config_path)
    monkeypatch.setattr(config_store, "_config", store)

    token = auth.create_session("oliver")
    assert auth.verify_session(token)
    store.set("web", "password", auth.hash_password("another-long-password"))
    store.save()
    assert not auth.verify_session(token)


def test_passwords_longer_than_bcrypt_limit_are_supported():
    password = "pässphrase-" + ("x" * 100)
    stored = auth.hash_password(password)
    assert stored.startswith(auth.PREHASH_PREFIX)
    assert auth.is_hashed(stored)
    assert auth.verify_password(password, stored)
    assert not auth.verify_password(password + "!", stored)


def test_security_migration_rotates_metrics_token_and_removes_stale_initial_password(
    tmp_path: Path, monkeypatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "web": {
                    "username": "oliver",
                    "password": "a-very-long-password",
                    "secret_key": "change-this-to-random-string-32chars-min",
                },
                "monitoring": {"metrics_token": "change-this-metrics-token"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".initial-password").write_text("obsolete-password\n", encoding="utf-8")
    store = config_store.ConfigStore(config_path)
    monkeypatch.setattr(config_store, "_config", store)

    auth.migrate_security()

    assert auth.is_hashed(store.get("web", "password"))
    assert len(store.get("web", "secret_key")) >= 32
    assert store.get("monitoring", "metrics_token") != "change-this-metrics-token"
    assert len(store.get("monitoring", "metrics_token")) >= 24
    assert not (tmp_path / ".initial-password").exists()
