"""Import permissions with signed sessions and the real authorization dependencies."""
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app import auth
from app.routes import api_pending, api_share
from tests.conftest import _create_recipe


class _Config:
    def __init__(self):
        self.values = {
            ("web",): {"auth_disabled": False},
            ("web", "secret_key"): "import-permissions-test-secret-key-123456789",
            ("web", "share_enabled"): True,
        }

    def get(self, *parts, default=None):
        return self.values.get(parts, default)

    def set(self, *parts):
        self.values[parts[:-1]] = parts[-1]

    def save(self):
        pass


@pytest.fixture
def signed_sessions(client, test_db, monkeypatch):
    config = _Config()
    monkeypatch.setattr(auth, "get_config", lambda: config)
    monkeypatch.setattr(api_share, "get_config", lambda: config)
    for role in ("admin", "user"):
        test_db.user_create(f"import-{role}", "unused-test-hash", role=role)
    headers = {
        role: {"Authorization": f"Bearer {auth.create_session(f'import-{role}')}"}
        for role in ("admin", "user")
    }
    headers["guest"] = {"Authorization": f"Bearer {auth.create_guest_session()}"}
    headers["anonymous"] = {}
    saved_overrides = client.app.dependency_overrides.copy()
    client.app.dependency_overrides.pop(auth.require_auth, None)
    client.app.dependency_overrides.pop(auth.require_admin, None)
    try:
        yield headers
    finally:
        client.app.dependency_overrides.clear()
        client.app.dependency_overrides.update(saved_overrides)


def _jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.parametrize("role", ["anonymous", "guest", "user"])
def test_import_and_staging_routes_deny_non_admin_sessions(
    client, test_db, signed_sessions, monkeypatch, role
):
    def unexpected_import(*_args, **_kwargs):
        pytest.fail("A denied request reached the import pipeline")

    monkeypatch.setattr(api_pending, "enqueue", unexpected_import)
    monkeypatch.setattr(api_pending, "get_scraper_job", unexpected_import)
    monkeypatch.setattr(api_pending, "_assert_upload_capacity", unexpected_import)
    # A guard regression must not start a real background worker in this test.
    monkeypatch.setattr(
        api_pending, "_reanalyze_lock", SimpleNamespace(acquire=lambda **_kwargs: False)
    )
    routes = [
        ("GET", "/api/pending", {}),
        ("POST", "/api/pending/import-url", {"json": {"url": "https://example.com/pasta"}}),
        ("POST", "/api/pending/import-file", {
            "files": {"file": ("recipe.jpg", _jpeg_bytes(), "image/jpeg")},
        }),
        ("POST", "/api/pending/scan-photo", {}),
        ("POST", "/api/pending/bulk-skip", {}),
        ("GET", "/api/pending/file?url=missing", {}),
        ("POST", "/api/pending", {}),
        ("POST", "/api/pending/reanalyze", {}),
        ("POST", "/api/pending/reanalyze-all", {}),
        ("GET", "/api/pending/reanalyze/progress", {}),
        ("GET", "/api/pending/failed", {}),
        ("POST", "/api/pending/failed/retry", {}),
        ("POST", "/api/pending/failed/discard", {}),
        ("POST", "/api/pending/failed/missing/retry", {}),
        ("POST", "/api/pending/failed/missing/discard", {}),
        ("POST", "/api/pending/failed/clear-all", {}),
        ("GET", "/api/share/token", {}),
        ("POST", "/api/share/token/rotate", {}),
        ("POST", "/api/share/tokens", {"json": {"name": "Phone"}}),
        ("DELETE", "/api/share/tokens/missing", {}),
        ("POST", "/api/share/disable", {}),
    ]
    expected = 401 if role == "anonymous" else 403
    for method, path, kwargs in routes:
        response = client.request(method, path, headers=signed_sessions[role], **kwargs)
        assert response.status_code == expected, (role, method, path, response.text)
        if role == "user":
            assert "Administratorrechte" in response.json()["detail"]
    assert test_db.pending_list() == []
    assert test_db.share_intake_tokens_list() == []


def test_admin_can_import_url_and_file_and_read_inbox(
    client, test_db, signed_sessions, monkeypatch
):
    queued = []
    attachments = []

    def enqueue(kind, payload, **kwargs):
        queued.append((kind, payload, kwargs))
        return "permission-test-task"

    def process_attachment(attachment, url):
        attachments.append((attachment, url))
        test_db.pending_add(url, "recipe", ai_suggestion={"name": "Photo recipe"})
        return {"status": "pending", "name": "Photo recipe"}

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "enqueue", enqueue)
    monkeypatch.setattr(api_pending, "_assert_upload_capacity", lambda _size: None)
    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    monkeypatch.setattr(
        api_pending, "get_scraper_job", lambda: SimpleNamespace(process_attachment=process_attachment)
    )
    headers = signed_sessions["admin"]
    url_response = client.post(
        "/api/pending/import-url", headers=headers,
        json={"url": "https://example.com/pasta", "type": "recipe"},
    )
    file_response = client.post(
        "/api/pending/import-file", headers=headers,
        files={"file": ("recipe.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"client_request_id": "admin-permission-test"},
    )
    inbox_response = client.get("/api/pending", headers=headers)

    assert url_response.status_code == 200, url_response.text
    assert url_response.json()["accepted"] is True
    assert queued[0][0:2] == ("share_ingest", {"url": "https://example.com/pasta", "type": "recipe"})
    assert file_response.status_code == 200, file_response.text
    assert file_response.json()["ok"] is True
    assert len(attachments) == 1
    assert inbox_response.status_code == 200
    assert {item["url"] for item in inbox_response.json()} == {
        url_response.json()["url"], file_response.json()["url"],
    }


@pytest.mark.parametrize("role", ["guest", "user", "admin"])
def test_recipe_read_rating_and_favorite_keep_existing_permissions(
    client, test_db, signed_sessions, tmp_path, role
):
    recipe = _create_recipe(test_db, name="Pasta", folder_path=str(tmp_path / "recipe"))
    headers = signed_sessions[role]
    listing = client.get("/api/recipes", headers=headers)
    detail = client.get(f"/api/recipes/{recipe['id']}", headers=headers)
    rating = client.post(f"/api/recipes/{recipe['id']}/rating?value=4", headers=headers)
    favorite = client.post(f"/api/recipes/{recipe['id']}/favorite", headers=headers)

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert detail.status_code == 200
    expected_write_status = 403 if role == "guest" else 200
    assert rating.status_code == expected_write_status
    assert favorite.status_code == expected_write_status
    saved = test_db.recipe_get(recipe["id"])
    assert (saved["rating"] or 0) == (0 if role == "guest" else 4)
    assert bool(saved["is_favorite"]) is (role != "guest")


def test_share_intake_requires_separate_admin_issued_credential(
    client, signed_sessions, monkeypatch
):
    queued = []
    monkeypatch.setattr(api_share, "_consume_rate_limit", lambda _key: None)
    monkeypatch.setattr(api_share, "enqueue", lambda *args, **kwargs: queued.append(args) or "share-task")
    for role in ("anonymous", "guest", "user", "admin"):
        denied = client.post(
            "/api/share", headers=signed_sessions[role], json={"url": "https://example.com/pasta"}
        )
        assert denied.status_code == 401, (role, denied.text)
    assert queued == []

    created = client.post(
        "/api/share/tokens", headers=signed_sessions["admin"], json={"name": "Admin shortcut"}
    )
    assert created.status_code == 200, created.text
    credential = created.json()
    accepted = client.post(
        "/api/share", headers={"X-Share-Token": credential["token"]},
        json={"url": "https://example.com/pasta"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True
    assert len(queued) == 1

    revoked = client.delete(f"/api/share/tokens/{credential['id']}", headers=signed_sessions["admin"])
    assert revoked.status_code == 200
    replay = client.post(
        "/api/share", headers={"X-Share-Token": credential["token"]},
        json={"url": "https://example.com/pasta"},
    )
    assert replay.status_code == 401
    assert len(queued) == 1
