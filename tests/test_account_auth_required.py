"""Retired proxy login settings must never substitute for a signed session."""
import copy

import pytest
import yaml
from fastapi import HTTPException
from starlette.requests import Request

from app import auth, security, tenancy
from app.config_store import ConfigStore


@pytest.fixture
def legacy_config(tmp_path, monkeypatch):
    values = {"web": {"auth_disabled": True, "external_logout_url": "https://obsolete.invalid/logout",
                       "username": "owner", "password": "existing-password-hash",
                       "secret_key": "existing-session-key-" * 3, "session_version": 4,
                       "trusted_proxies": ["127.0.0.1/32"]},
              "einkauf": {"cf_access_client_id": "keep-id", "cf_access_client_secret": "keep-secret"}}
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    config = ConfigStore(path)
    monkeypatch.setattr(auth, "get_config", lambda: config)
    return config, values


def test_legacy_config_load_and_reload_preserve_credentials_and_einkauf(legacy_config):
    config, original = legacy_config
    original_bytes = config.path.read_bytes()
    expected = copy.deepcopy(original)
    expected["web"].pop("auth_disabled")
    expected["web"].pop("external_logout_url")
    assert config.all() == expected
    assert config.path.read_bytes() == original_bytes
    config.reload()
    assert config.all() == expected
    config.save()
    assert yaml.safe_load(config.path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("token", ["", "cloudflare-access"])
def test_trusted_loopback_cannot_authenticate_or_create_a_household_scope(legacy_config, test_db, token):
    config, original = legacy_config
    # Even an old/in-memory store that still exposes true must have no effect.
    config._data = copy.deepcopy(original)
    request = Request({"type": "http", "method": "GET", "path": "/api/session",
                       "headers": [(b"authorization", ("Bearer " + token).encode())],
                       "client": ("127.0.0.1", 1234), "scheme": "http", "server": ("localhost", 80)})
    assert security.request_is_from_trusted_proxy(request)
    assert auth.request_user(request) is None
    assert tenancy.scope_for_request(request) is None
    for guard in (auth._require_auth, auth._require_admin):
        with pytest.raises(HTTPException) as error:
            guard(request)
        assert error.value.status_code == 401


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("token", ["", "cloudflare-access"])
def test_legacy_proxy_settings_cannot_read_api_or_spa(client, legacy_config, monkeypatch, trusted, token):
    from app.main import app
    config, original = legacy_config
    config._data = copy.deepcopy(original)
    monkeypatch.setattr(security, "request_is_from_trusted_proxy", lambda _request: trusted)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    headers = {"Authorization": "Bearer " + token} if token else {}
    for path in ("/api/auth/session", "/api/session", "/api/recipes", "/api/account", "/api/config", "/api/admin/overview"):
        assert client.get(path, headers=headers).status_code == 401, path
    if token:
        client.cookies.set(auth.SESSION_COOKIE, token)
    for path in ("/", "/admin", "/account"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login"), path
    assert client.get("/login", follow_redirects=False).status_code == 200


def test_legacy_client_can_save_false_but_never_disable_accounts(client, legacy_config, monkeypatch):
    from app.routes import api_config
    config, _ = legacy_config
    monkeypatch.setattr(api_config, "get_config", lambda: config)
    result = client.put("/api/config", json={"web": {"auth_disabled": False,
                                                    "external_logout_url": "https://ignored.invalid"}})
    assert result.status_code == 200
    assert "auth_disabled" not in config.get("web")
    assert "external_logout_url" not in config.get("web")
    before = config.path.read_bytes()
    result = client.put("/api/config", json={"web": {"auth_disabled": True}})
    assert result.status_code == 400
    assert config.path.read_bytes() == before


@pytest.mark.parametrize("role", ["user", "admin"])
def test_real_login_keeps_persisted_role_and_logout_revokes(client, test_db, legacy_config, role):
    from app.main import app
    config, original = legacy_config
    config._data = copy.deepcopy(original)
    password_hash = auth.hash_password("existing-owner-password")
    test_db.user_create("local", password_hash, role=role)
    before = test_db.user_get_by_name("local")
    result = client.post("/api/auth/login", json={"username": "local", "password": "existing-owner-password"})
    assert result.status_code == 200
    payload = result.json()
    assert payload["role"] == role and payload["is_admin"] is (role == "admin")
    assert payload["token"] != "cloudflare-access"
    assert auth.verify_session(payload["token"])
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    headers = {"Authorization": "Bearer " + payload["token"]}
    assert client.get("/api/config", headers=headers).status_code == (200 if role == "admin" else 403)
    assert client.post("/api/auth/logout", headers=headers).json()["revoked"]
    assert client.get("/api/auth/session", headers=headers).status_code == 401
    assert not auth.verify_session(payload["token"])
    after = test_db.user_get_by_name("local")
    assert after["password_hash"] == before["password_hash"]
    assert after["session_version"] == before["session_version"]
    with test_db.conn() as connection:
        assert connection.execute("SELECT revoked_at FROM user_sessions WHERE id=?",
                                  (auth._session_payload(payload["token"])["sid"],)).fetchone()[0] is not None
    assert config.get("web", "secret_key") == original["web"]["secret_key"]


def test_browser_login_and_logout_revoke_existing_session(client, test_db, legacy_config):
    config, original = legacy_config
    config._data = copy.deepcopy(original)
    test_db.user_create("owner", auth.hash_password("existing-owner-password"), role="admin")
    result = client.post("/login", data={"username": "owner", "password": "existing-owner-password"},
                         headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert result.status_code == 303
    token = client.cookies.get(auth.SESSION_COOKIE)
    assert token and auth.verify_session(token)
    assert client.get("/admin").status_code == 200
    result = client.post("/logout", headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == "/login"
    assert not auth.verify_session(token)
    assert config.get("web", "secret_key") == original["web"]["secret_key"]
