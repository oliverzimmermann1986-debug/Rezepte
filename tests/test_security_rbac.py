"""Gezielte Regressionstests für Rollen- und Outbound-Sicherheit."""
from __future__ import annotations

import copy
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import auth
from app.auth import require_admin
from app.core import analyzer as analyzer_module
from app.core import hdd_controller, webhook
from app.db import LastActiveAdminError
from app.recipes import audit as recipe_audit
from app.routes import api_config, api_einkauf, api_test


class _Config:
    def __init__(self, values: dict[tuple[str, ...], object]):
        self.values = values

    def get(self, *parts, default=None):
        return self.values.get(tuple(parts), default)


def _public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_legacy_config_user_is_promoted_to_admin(test_db, monkeypatch):
    test_db.user_create("oliver", auth.hash_password("old-password"), role="user")
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: _Config(
            {
                ("web", "username"): "oliver",
                ("web", "password"): auth.hash_password("old-password"),
            }
        ),
    )

    auth.migrate_users_to_db()
    migrated = test_db.user_get_by_name("oliver")

    assert migrated["role"] == "admin"
    assert migrated["session_version"] == 1


def test_empty_install_creates_config_user_as_admin(test_db, monkeypatch):
    password_hash = auth.hash_password("initial-password")
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: _Config(
            {
                ("web", "username"): "owner",
                ("web", "password"): password_hash,
            }
        ),
    )

    auth.migrate_users_to_db()

    assert test_db.user_get_by_name("owner")["role"] == "admin"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/config"),
        ("POST", "/api/test/paths"),
        ("GET", "/api/audit"),
        ("GET", "/api/browse/local?path=/"),
        ("GET", "/api/hdd/status"),
        ("GET", "/api/jobs/list"),
        ("GET", "/api/schedule"),
        ("GET", "/api/master/tags"),
        ("GET", "/api/admin/overview"),
        ("GET", "/api/users"),
        ("GET", "/api/history"),
    ],
)
def test_normal_user_cannot_access_admin_route_groups(
    client, test_db, monkeypatch, method, path
):
    from app.main import app

    test_db.user_create("friend", auth.hash_password("friend-password"), role="user")
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(auth, "request_user", lambda _request: "friend")
    app.dependency_overrides.pop(require_admin, None)
    try:
        response = client.request(method, path)
    finally:
        app.dependency_overrides[require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert response.status_code == 403
    assert "Administratorrechte" in response.json()["detail"]


def test_source_integrity_routes_use_real_admin_user_and_guest_sessions(
    client,
    test_db,
    monkeypatch,
):
    from app.main import app

    config = _Config(
        {
            ("web",): {"auth_disabled": False},
            ("web", "secret_key"): "i" * 48,
        }
    )
    monkeypatch.setattr(auth, "get_config", lambda: config)
    test_db.user_create(
        "integrity-admin",
        auth.hash_password("admin-password"),
        role="admin",
    )
    test_db.user_create(
        "integrity-reader",
        auth.hash_password("reader-password"),
        role="user",
    )
    tokens = {
        "admin": auth.create_session("integrity-admin"),
        "user": auth.create_session("integrity-reader"),
        "guest": auth.create_guest_session(),
    }
    missing_recipe_id = 999_999
    report_path = f"/api/recipes/{missing_recipe_id}/source-integrity"
    mutations = {
        "check": (f"{report_path}/check", None),
        "accept": (f"{report_path}/accept", {"expected_snapshot_id": 1}),
    }

    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    try:
        anonymous_report = client.get(report_path)
        reports = {
            role: client.get(
                report_path,
                headers={"Authorization": f"Bearer {token}"},
            )
            for role, token in tokens.items()
        }
        mutation_responses = {
            (operation, role): client.post(
                path,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            for operation, (path, payload) in mutations.items()
            for role, token in tokens.items()
        }
        anonymous_mutations = {
            operation: client.post(path, json=payload)
            for operation, (path, payload) in mutations.items()
        }
    finally:
        app.dependency_overrides[auth.require_auth] = lambda: None
        app.dependency_overrides[auth.require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert anonymous_report.status_code == 401
    for response in reports.values():
        assert response.status_code == 404
    for operation in mutations:
        assert mutation_responses[(operation, "admin")].status_code == 404
        user_response = mutation_responses[(operation, "user")]
        assert user_response.status_code == 403
        assert "Administratorrechte" in user_response.json()["detail"]
        guest_response = mutation_responses[(operation, "guest")]
        assert guest_response.status_code == 403
        assert "schreibgeschützt" in guest_response.json()["detail"]
        assert anonymous_mutations[operation].status_code == 401


def test_user_roles_are_managed_and_last_admin_is_protected(client, test_db):
    from app.main import app

    last_admin_id = test_db.user_create(
        "last-admin",
        auth.hash_password("admin-password"),
        role="admin",
    )
    app.dependency_overrides[require_admin] = lambda: {
        "username": "operator",
        "role": "admin",
        "full_access": True,
    }

    created = client.post(
        "/api/users",
        json={"username": "friend", "password": "friend-password", "role": "user"},
    )
    assert created.status_code == 200
    friend_id = created.json()["id"]
    assert created.json()["role"] == "user"

    promoted = client.patch(f"/api/users/{friend_id}", json={"role": "admin"})
    assert promoted.status_code == 200
    assert test_db.user_get_by_name("friend")["role"] == "admin"

    demoted = client.patch(f"/api/users/{friend_id}", json={"role": "user"})
    assert demoted.status_code == 200
    blocked_demotion = client.patch(
        f"/api/users/{last_admin_id}", json={"role": "user"}
    )
    blocked_delete = client.delete(f"/api/users/{last_admin_id}")
    assert blocked_demotion.status_code == 400
    assert blocked_delete.status_code == 400
    assert "letzte aktive Administrator" in blocked_demotion.json()["detail"]

    listed = client.get("/api/users").json()["users"]
    assert {item["username"]: item["role"] for item in listed} == {
        "friend": "user",
        "last-admin": "admin",
    }


@pytest.mark.parametrize("operation", ["role", "disable", "delete"])
def test_parallel_admin_removal_keeps_one_active_admin(test_db, operation):
    admin_ids = [
        test_db.user_create("admin-one", "hash", role="admin"),
        test_db.user_create("admin-two", "hash", role="admin"),
    ]
    barrier = threading.Barrier(2)

    def remove_admin(user_id: int) -> str:
        barrier.wait(timeout=5)
        try:
            if operation == "role":
                test_db.user_set_role(user_id, "user")
            elif operation == "disable":
                test_db.user_set_disabled(user_id, True)
            else:
                test_db.user_delete(user_id)
        except LastActiveAdminError:
            return "blocked"
        return "changed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(remove_admin, admin_ids))

    assert sorted(results) == ["blocked", "changed"]
    assert test_db.user_count_active_admins() == 1


class _ConfigStore:
    def __init__(self, data: dict):
        self.data = copy.deepcopy(data)
        self.saved = False

    def all(self):
        return copy.deepcopy(self.data)

    def replace(self, value):
        self.data = copy.deepcopy(value)

    def save(self):
        self.saved = True


def test_bearer_password_change_updates_actor_and_revokes_sessions(
    client, test_db, tmp_path, monkeypatch
):
    from app.main import app

    old_hash = auth.hash_password("old-password")
    admin_id = test_db.user_create("mobile-admin", old_hash, role="admin")
    store = _ConfigStore(
        {
            "web": {"password": old_hash, "session_version": 0},
            "paths": {"data_dir": str(tmp_path)},
        }
    )
    monkeypatch.setattr(api_config, "get_config", lambda: store)
    monkeypatch.setattr(api_config, "invalidate_scraper_job", lambda: None)

    def bearer_actor(request):
        assert request.headers["authorization"] == "Bearer mobile-token"
        return "mobile-admin"

    monkeypatch.setattr(api_config, "request_user", bearer_actor)
    app.dependency_overrides[require_admin] = lambda: {
        "username": "mobile-admin",
        "role": "admin",
        "full_access": True,
    }

    response = client.put(
        "/api/config",
        headers={"Authorization": "Bearer mobile-token"},
        json={"web": {"password": "new-password"}},
    )

    assert response.status_code == 200
    row = test_db.user_get_by_name("mobile-admin")
    assert row["id"] == admin_id
    assert auth.verify_password("new-password", row["password_hash"])
    assert row["session_version"] == 1
    # Nach der Multi-User-Migration ist nur die DB Auth-Quelle. Der Legacy-
    # Config-Hash bleibt unverändert und kann deshalb nicht mit der DB
    # auseinanderlaufen.
    assert store.data["web"]["session_version"] == 0
    assert store.data["web"]["password"] == old_hash
    assert store.saved is True


def test_runtime_paths_cannot_be_changed_through_config_api():
    current = {
        "paths": {
            "recipe_dir": "/data/recipes",
            "logs_dir": "/opt/scrapper/logs",
        }
    }
    api_config._assert_server_managed_paths_unchanged(
        {"paths": {"recipe_dir": "/data/recipes/"}},
        current,
    )

    with pytest.raises(HTTPException) as exc:
        api_config._assert_server_managed_paths_unchanged(
            {"paths": {"recipe_dir": "/"}},
            current,
        )
    assert exc.value.status_code == 400
    assert "serververwaltet" in exc.value.detail


def test_unchanged_internal_einkauf_url_does_not_block_other_config_changes(
    client, monkeypatch
):
    store = _ConfigStore(
        {
            "einkauf": {"api_url": "http://127.0.0.1:8010"},
            "web": {"session_timeout_hours": 24},
        }
    )
    monkeypatch.setattr(api_config, "get_config", lambda: store)
    monkeypatch.setattr(api_config, "invalidate_scraper_job", lambda: None)

    response = client.put(
        "/api/config",
        json={
            "einkauf": {"api_url": "http://127.0.0.1:8010/"},
            "web": {"session_timeout_hours": 48},
        },
    )

    assert response.status_code == 200
    assert store.data["web"]["session_timeout_hours"] == 48
    assert store.saved is True


def test_einkauf_url_cannot_be_changed_through_config_api():
    current = {"einkauf": {"api_url": "http://127.0.0.1:8010"}}

    with pytest.raises(HTTPException) as exc:
        api_config._assert_server_managed_einkauf_url_unchanged(
            {"einkauf": {"api_url": "https://attacker.example"}},
            current,
        )

    assert exc.value.status_code == 400
    assert "serververwaltet" in exc.value.detail
    with pytest.raises(HTTPException, match="muss ein Objekt sein"):
        api_config._assert_server_managed_einkauf_url_unchanged(
            {"einkauf": "replaced"},
            current,
        )


def test_openai_and_shelly_urls_are_server_managed():
    current = {
        "ai": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "external_hdd": {"shelly_url": "http://192.168.1.50"},
    }
    api_config._assert_server_managed_service_urls_unchanged(
        {
            "ai": {"openai": {"base_url": "https://api.openai.com/v1/"}},
            "external_hdd": {"shelly_url": "http://192.168.1.50/"},
        },
        current,
    )

    with pytest.raises(HTTPException, match="serververwaltet"):
        api_config._assert_server_managed_service_urls_unchanged(
            {"external_hdd": {"shelly_url": "http://169.254.169.254"}},
            current,
        )
    with pytest.raises(HTTPException, match="muss ein Objekt sein"):
        api_config._assert_server_managed_service_urls_unchanged(
            {"ai": {"openai": "replaced"}},
            current,
        )


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fe80::1"],
)
def test_outbound_url_validator_blocks_non_public_addresses(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        webhook.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (family, socket.SOCK_STREAM, 6, "", (address, 443))
        ],
    )

    with pytest.raises(ValueError, match="Private oder lokale"):
        webhook.validate_public_https_url("https://service.example/hook")


def test_outbound_url_validator_requires_https_and_blocks_localhost(monkeypatch):
    monkeypatch.setattr(webhook.socket, "getaddrinfo", _public_dns)

    with pytest.raises(ValueError, match="HTTPS"):
        webhook.validate_public_https_url("http://service.example/hook")
    with pytest.raises(ValueError, match="Lokale"):
        webhook.validate_public_https_url("https://localhost/hook")


def test_webhook_does_not_follow_redirects(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(status_code=302, text="redirect")

    monkeypatch.setattr(webhook, "pinned_https_request", fake_request)

    assert webhook._post_one(
        {"name": "test", "url": "https://hooks.example/endpoint"},
        {"event": "test", "timestamp": "now", "summary": {}},
    ) is False
    assert captured["method"] == "POST"


def test_pinned_https_transport_resolves_once_and_preserves_tls_host(monkeypatch):
    dns_calls = []
    pools = []
    requests_seen = []

    def rotating_dns(*args, **kwargs):
        dns_calls.append((args, kwargs))
        address = "93.184.216.34" if len(dns_calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    class FakePool:
        def __init__(self, host, port=None, **kwargs):
            pools.append((host, port, kwargs))

        def urlopen(self, method, url, **kwargs):
            requests_seen.append((method, url, kwargs))
            return SimpleNamespace(
                status=200,
                headers={"content-type": "application/json"},
                data=b'{"ok":true}',
                reason="OK",
            )

        def close(self):
            return None

    monkeypatch.setattr(webhook.socket, "getaddrinfo", rotating_dns)
    monkeypatch.setattr(webhook.urllib3, "HTTPSConnectionPool", FakePool)

    response = webhook.pinned_https_request(
        "POST",
        "https://hooks.example:8443/callback?token=secret",
        json={"event": "test"},
        timeout=(2, 4),
    )

    assert response.status_code == 200
    assert len(dns_calls) == 1
    assert pools[0][0] == "93.184.216.34"
    assert pools[0][1] == 8443
    assert pools[0][2]["server_hostname"] == "hooks.example"
    assert pools[0][2]["assert_hostname"] == "hooks.example"
    assert requests_seen[0][1] == "/callback?token=secret"
    assert requests_seen[0][2]["headers"]["Host"] == "hooks.example:8443"
    assert requests_seen[0][2]["redirect"] is False
    assert requests_seen[0][2]["retries"] is False


def test_private_server_target_requires_exact_literal_allowlist(monkeypatch):
    sessions = []
    calls = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            sessions.append(self)

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs, self.trust_env))
            return SimpleNamespace(status_code=200)

        def close(self):
            return None

    monkeypatch.setattr(webhook.requests, "Session", FakeSession)

    response = webhook.server_configured_request(
        "GET",
        "http://192.168.1.50/api/status",
        trusted_private_bases=("http://192.168.1.50/api",),
        timeout=3,
    )

    assert response.status_code == 200
    assert calls[0][3] is False
    assert calls[0][2]["allow_redirects"] is False
    with pytest.raises(ValueError, match="nicht serverseitig freigegeben"):
        webhook.server_configured_request(
            "GET",
            "http://192.168.1.51/api/status",
            trusted_private_bases=("http://192.168.1.50/api",),
        )
    with pytest.raises(ValueError, match="nicht serverseitig freigegeben"):
        webhook.server_configured_request(
            "GET",
            "http://169.254.169.254/latest/meta-data",
            trusted_private_bases=("http://169.254.169.254",),
        )
    assert len(sessions) == 1


def test_analyzer_routes_requests_through_server_configured_transport(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(analyzer_module, "server_configured_request", fake_request)
    analyzer = analyzer_module.OpenAIAnalyzer(
        "top-secret",
        base_url="http://10.0.0.5:8000/v1",
    )

    analyzer.request("GET", "/models", timeout=5)

    assert captured["url"] == "http://10.0.0.5:8000/v1/models"
    assert captured["trusted_private_bases"] == ("http://10.0.0.5:8000/v1",)
    assert captured["headers"]["Authorization"] == "Bearer top-secret"


def test_audit_openai_uses_server_configured_transport(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [
                    {"message": {"content": '{"suggestions":[{"id":7,"name":"Pasta"}]}'}}
                ]
            },
        )

    monkeypatch.setattr(recipe_audit, "server_configured_request", fake_request)
    result = recipe_audit.ai_suggest_batch(
        [({"id": 7, "name": "video", "description": "x" * 40}, "bad name")],
        {
            "api_key": "secret",
            "base_url": "http://10.0.0.5:8000/v1",
            "model": "demo",
        },
    )

    assert result == {7: "Pasta"}
    assert captured["trusted_private_bases"] == ("http://10.0.0.5:8000/v1",)


def test_shelly_uses_server_configured_literal_ip_transport(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"relays": [{"ison": True}]},
        )

    monkeypatch.setattr(hdd_controller, "server_configured_request", fake_request)
    controller = hdd_controller.HDDController(
        {"enabled": True, "shelly_url": "http://192.168.1.50"}
    )

    assert controller.shelly_status() is True
    assert calls[0][1] == "http://192.168.1.50/status"
    assert calls[0][2]["trusted_private_bases"] == ("http://192.168.1.50",)


def test_einkauf_base_url_blocks_private_target_before_sending(monkeypatch):
    monkeypatch.setattr(
        webhook.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 443))
        ],
    )
    monkeypatch.setattr(
        api_einkauf,
        "get_config",
        lambda: _Config({("einkauf", "api_url"): "https://internal.example"}),
    )
    monkeypatch.setattr(
        webhook.urllib3,
        "HTTPSConnectionPool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("request must not be sent")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        api_einkauf.einkauf_request("GET", "items")
    assert exc.value.status_code == 503


def test_einkauf_redirect_is_blocked(monkeypatch):
    monkeypatch.setattr(
        api_einkauf,
        "get_config",
        lambda: _Config({("einkauf", "api_url"): "https://shop.example"}),
    )
    monkeypatch.setattr(
        api_einkauf,
        "pinned_https_request",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=302,
            content=b"",
            raise_for_status=lambda: None,
        ),
    )

    with pytest.raises(HTTPException) as exc:
        api_einkauf.einkauf_request("GET", "items")
    assert exc.value.status_code == 502


def test_openai_ad_hoc_base_uses_pinned_transport(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api_test,
        "get_config",
        lambda: _Config(
            {
                ("ai", "openai"): {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "configured-secret",
                    "model": "demo-model",
                }
            }
        ),
    )

    def fake_pinned(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [{"id": "demo-model"}]},
        )
        response.raise_for_status = lambda: None
        return response

    monkeypatch.setattr(api_test, "pinned_https_request", fake_pinned)
    result = api_test.test_openai(
        api_test.OpenAITestRequest(
            api_key="request-secret",
            model="demo-model",
            base_url="https://provider.example/v1",
        )
    )

    assert result["ok"] is True
    assert captured["method"] == "GET"
    assert captured["url"] == "https://provider.example/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer request-secret"


def test_openai_configured_base_uses_server_trust_boundary(monkeypatch):
    captured = {}
    configured_base = "http://10.0.0.5:8000/v1"
    monkeypatch.setattr(
        api_test,
        "get_config",
        lambda: _Config(
            {
                ("ai", "openai"): {
                    "base_url": configured_base,
                    "api_key": "configured-secret",
                    "model": "demo-model",
                }
            }
        ),
    )

    def fake_configured(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "demo-model"}]},
        )

    monkeypatch.setattr(api_test, "server_configured_request", fake_configured)
    result = api_test.test_openai()

    assert result["ok"] is True
    assert captured["url"] == f"{configured_base}/models"
    assert captured["trusted_private_bases"] == (configured_base,)
