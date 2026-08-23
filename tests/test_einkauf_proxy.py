"""Tests für die serverseitige Verbindung zur bestehenden Einkaufsliste."""

from types import SimpleNamespace
from urllib.parse import quote

from app.routes import api_einkauf, api_shopping
from fastapi import HTTPException
import pytest


class StubConfig:
    def __init__(self, values):
        self.values = values

    def get(self, section, key, default=None):
        return self.values.get((section, key), default)


def test_status_reports_only_connection_state(monkeypatch):
    config = StubConfig({("einkauf", "api_url"): "http://127.0.0.1:8010/"})
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)

    assert api_einkauf.status() == {
        "configured": True,
        "target": "http://127.0.0.1:8010",
    }


def test_proxy_auth_headers_stay_server_side(monkeypatch):
    config = StubConfig({
        ("einkauf", "app_token"): "app-secret",
        ("einkauf", "cf_access_client_id"): "client-id",
        ("einkauf", "cf_access_client_secret"): "client-secret",
    })
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)

    assert api_einkauf._auth_headers() == {
        "x-app-token": "app-secret",
        "CF-Access-Client-Id": "client-id",
        "CF-Access-Client-Secret": "client-secret",
    }


def test_internal_request_forwards_json_and_auth(monkeypatch):
    config = StubConfig({
        ("einkauf", "api_url"): "http://127.0.0.1:8010/",
        ("einkauf", "app_token"): "app-secret",
    })
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(
            content=b'{"id": 17}',
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"id": 17},
        )

    monkeypatch.setattr(api_einkauf, "_send_einkauf_request", fake_request)

    result = api_einkauf.einkauf_request(
        "POST",
        "/recurring",
        {"name": "Milch", "interval_days": 7},
    )

    assert result == {"id": 17}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8010/recurring"
    assert captured["base_url"] == "http://127.0.0.1:8010"
    assert captured["json"]["name"] == "Milch"
    assert captured["headers"]["x-app-token"] == "app-secret"


def test_routes_are_registered_and_reachable(client, monkeypatch):
    config = StubConfig({("einkauf", "api_url"): "http://127.0.0.1:8010/"})
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)
    monkeypatch.setattr(
        api_einkauf,
        "_send_einkauf_request",
        lambda *args, **kwargs: SimpleNamespace(
            content=b'{"ok":true}',
            status_code=200,
            headers={"content-type": "application/json"},
        ),
    )

    status = client.get("/api/einkauf/status")
    proxied = client.get("/api/einkauf/items")

    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert proxied.status_code == 200
    assert proxied.json() == {"ok": True}


def test_internal_target_is_exact_server_allowlist(monkeypatch):
    assert api_einkauf.is_server_managed_internal_einkauf_url(
        "http://127.0.0.1:8010/"
    )
    assert not api_einkauf.is_server_managed_internal_einkauf_url(
        "http://127.0.0.1:8011"
    )
    assert not api_einkauf.is_server_managed_internal_einkauf_url(
        "http://einkauf:8010"
    )


def test_internal_request_disables_environment_proxies(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            sessions.append(self)

        def request(self, *_args, **_kwargs):
            assert self.trust_env is False
            return SimpleNamespace(status_code=200, content=b"{}")

        def close(self):
            return None

    monkeypatch.setattr(api_einkauf.requests, "Session", FakeSession)
    response = api_einkauf._send_einkauf_request(
        "GET",
        "http://127.0.0.1:8010/items",
        base_url="http://127.0.0.1:8010",
        headers={},
    )

    assert response.status_code == 200
    assert len(sessions) == 1


def test_cart_push_uses_central_einkauf_transport(monkeypatch):
    calls = []

    class FakeDb:
        def cart_list(self):
            return [{"id": 7, "name": "Milch", "amount": 1, "unit": "l", "checked": 0}]

        def cart_clear(self, *, only_checked):
            return 1

    def fake_response(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(api_shopping, "get_db", lambda: FakeDb())
    monkeypatch.setattr(
        api_shopping,
        "einkauf_status",
        lambda: {"configured": True, "target": "http://127.0.0.1:8010"},
    )
    monkeypatch.setattr(api_shopping, "einkauf_response", fake_response)

    result = api_shopping.push_to_einkauf(
        api_shopping.PushPayload(consolidate=True, clear_after=False)
    )

    assert result["ok"] is True
    assert result["pushed"] == 1
    assert [(method, path) for method, path, _kwargs in calls] == [
        ("POST", "items"),
        ("POST", "consolidate"),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "items/../admin",
        "items/%2e%2e/admin",
        "items/%252e%252e/admin",
        "items\\..\\admin",
        "items//admin",
    ],
)
def test_proxy_path_rejects_normalization_bypasses(path):
    with pytest.raises(HTTPException) as exc:
        api_einkauf._validated_proxy_path(path)
    assert exc.value.status_code == 400


def test_proxy_path_allows_only_normalized_public_areas():
    assert api_einkauf._validated_proxy_path("items/17") == "items/17"
    with pytest.raises(HTTPException) as exc:
        api_einkauf._validated_proxy_path("admin/restore")
    assert exc.value.status_code == 404


def test_failed_import_can_be_permanently_discarded(client, test_db):
    url = "https://example.test/kaput/rezept"
    test_db.download_failure_record(url, "Download fehlgeschlagen")

    response = client.post(
        f"/api/pending/failed/{quote(url, safe='')}/discard"
    )

    assert response.status_code == 200
    assert response.json()["discarded"] is True
    assert test_db.download_failure_attempts(url) == 0
    assert test_db.history_get(url)["name"] == "(verworfen)"
