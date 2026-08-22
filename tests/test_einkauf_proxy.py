"""Tests für die serverseitige Verbindung zur bestehenden Einkaufsliste."""

from types import SimpleNamespace
from urllib.parse import quote

from app.routes import api_einkauf
from fastapi import HTTPException
import pytest


class StubConfig:
    def __init__(self, values):
        self.values = values

    def get(self, section, key, default=None):
        return self.values.get((section, key), default)


def test_status_reports_only_connection_state(monkeypatch):
    config = StubConfig({("einkauf", "api_url"): "http://einkauf:8010/"})
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)

    assert api_einkauf.status() == {
        "configured": True,
        "target": "http://einkauf:8010",
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
        ("einkauf", "api_url"): "http://einkauf:8010/",
        ("einkauf", "app_token"): "app-secret",
    })
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return SimpleNamespace(
            content=b'{"id": 17}',
            raise_for_status=lambda: None,
            json=lambda: {"id": 17},
        )

    monkeypatch.setattr(api_einkauf.requests, "request", fake_request)

    result = api_einkauf.einkauf_request(
        "POST",
        "/recurring",
        {"name": "Milch", "interval_days": 7},
    )

    assert result == {"id": 17}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://einkauf:8010/recurring"
    assert captured["json"]["name"] == "Milch"
    assert captured["headers"]["x-app-token"] == "app-secret"
    assert captured["allow_redirects"] is False


def test_routes_are_registered_and_reachable(client, monkeypatch):
    config = StubConfig({("einkauf", "api_url"): "http://einkauf:8010/"})
    monkeypatch.setattr(api_einkauf, "get_config", lambda: config)
    monkeypatch.setattr(
        api_einkauf.requests,
        "request",
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
