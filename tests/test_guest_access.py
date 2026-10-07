"""Guest sessions must read application data without any mutation privileges."""
import pytest
from itsdangerous import URLSafeTimedSerializer

from app import auth


@pytest.fixture
def guest(client, monkeypatch):
    from app.main import app

    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("guest-test-key-" * 4))
    old = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    response = client.post("/api/auth/guest")
    assert response.status_code == 200
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    yield client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def test_guest_session_is_read_only_and_creates_no_user(guest, test_db):
    for path in ("/api/auth/session", "/api/session"):
        response = guest.get(path)
        assert response.status_code == 200
    assert response.json() == {"username": "Gast", "role": "guest", "is_admin": False, "full_access": False, "read_only": True}
    assert test_db.user_list() == []
    assert guest.get("/api/recipes").status_code == 200
    assert guest.get("/api/cart").status_code == 200
    assert guest.get("/api/meal-plan").status_code == 200


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/api/recipes/1/favorite", {}),
    ("POST", "/api/recipes/1/rating", {"rating": 4}),
    ("POST", "/api/cart/add", {"name": "Gast-Zutat"}),
    ("DELETE", "/api/cart/1", None),
    ("POST", "/api/meal-plan/items", {"recipe_id": 1, "planned_for": "2026-10-05", "planned_servings": 2}),
    ("POST", "/api/recipes/1/generate-image", {}),
    ("POST", "/api/pending", {"url": "https://example.invalid/guest"}),
    ("GET", "/api/config", None),
    ("GET", "/api/users", None),
    ("GET", "/api/admin/overview", None),
])
def test_guest_cannot_create_change_or_administer(guest, method, path, body):
    assert guest.request(method, path, json=body).status_code == 403


def test_guest_does_not_trigger_extraction(guest, monkeypatch):
    from app.routes import api_recipes

    calls = []
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", lambda: calls.append("extract"))
    assert guest.get("/api/recipes").status_code == 200
    assert calls == []


def test_guest_logout_cannot_revoke_an_actual_user_named_guest(guest, test_db):
    test_db.user_create("Gast", "fake-hash")
    before = test_db.user_get_by_name("Gast")["session_version"]
    assert guest.post("/api/auth/logout").status_code == 200
    assert test_db.user_get_by_name("Gast")["session_version"] == before


def test_tampered_guest_token_is_rejected(guest):
    guest.headers["Authorization"] += "tampered"
    assert guest.get("/api/auth/session").status_code == 401
    assert guest.get("/api/recipes").status_code == 401


def test_guest_never_inherits_proxy_admin_access(guest, monkeypatch):
    from app import security
    monkeypatch.setattr(security, "request_is_from_trusted_proxy", lambda _request: True)
    assert guest.get("/api/session").json()["role"] == "guest"
    assert guest.post("/api/cart/add", json={"name": "blocked"}).status_code == 403
    assert guest.get("/api/users").status_code == 403
    guest.headers["Authorization"] += "invalid"
    assert guest.get("/api/recipes").status_code == 401
    assert guest.get("/api/users").status_code == 401


def test_guest_entry_and_registration_do_not_require_a_proxy(client, test_db, monkeypatch):
    from app import security

    test_db.user_create("operator", "unused", role="admin")
    monkeypatch.setattr(security, "request_is_from_trusted_proxy", lambda _request: False)
    result = client.post("/api/auth/guest")
    assert result.status_code == 200 and result.json()["role"] == "guest"
    result = client.post("/api/auth/register", json={"username": "new-user", "password": "strong-test-password"})
    assert result.status_code == 201 and result.json()["role"] == "user"


def test_expired_guest_session_is_rejected(guest, monkeypatch):
    monkeypatch.setattr(auth, "GUEST_MAX_AGE", -1)
    assert guest.get("/api/recipes").status_code == 401


def test_guest_endpoint_registered_once_and_reports_enforced_lifetime(client):
    from app.routes.api_auth import router
    assert len([route for route in router.routes if route.path == "/api/auth/guest" and "POST" in route.methods]) == 1
    result = client.post("/api/auth/guest")
    assert result.status_code == 200
    assert result.json()["expires_in"] == auth.GUEST_MAX_AGE


def test_guest_account_view_does_not_create_account_rows(guest, test_db):
    assert guest.get("/api/account").json()["is_guest"] is True
    with test_db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM user_accounts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM account_members").fetchone()[0] == 0
