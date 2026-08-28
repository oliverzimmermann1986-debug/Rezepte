def test_native_login_returns_bearer_session(client, test_db, monkeypatch):
    import app.routes.api_auth as api_auth

    test_db.user_create("anna", "unused-test-hash", role="user")
    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "check_credentials", lambda username, password: (
        username == "anna" and password == "geheim"
    ))
    monkeypatch.setattr(api_auth, "create_session", lambda username: f"token-for-{username}")

    response = client.post(
        "/api/auth/login",
        json={"username": "anna", "password": "geheim"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "token": "token-for-anna",
        "token_type": "bearer",
        "expires_in": 1209600,
        "username": "anna",
        "role": "user",
        "is_admin": False,
        "full_access": False,
        "read_only": False,
    }


def test_native_login_rejects_bad_credentials(client, monkeypatch):
    import app.routes.api_auth as api_auth

    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "check_credentials", lambda *_: False)

    response = client.post(
        "/api/auth/login",
        json={"username": "anna", "password": "falsch"},
    )

    assert response.status_code == 401


def test_native_login_rate_limits_repeated_failures(client, monkeypatch):
    import app.routes.api_auth as api_auth

    class FakeLimiter:
        def __init__(self):
            self.failures = set()

        def is_blocked(self, key):
            return (key in self.failures, 12 if key in self.failures else 0)

        def record_fail(self, key):
            self.failures.add(key)

        def record_success(self, key):
            self.failures.discard(key)

    monkeypatch.setattr(api_auth, "login_limiter", FakeLimiter())
    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "check_credentials", lambda *_: False)

    assert client.post(
        "/api/auth/login", json={"username": "anna", "password": "falsch"}
    ).status_code == 401
    blocked = client.post(
        "/api/auth/login", json={"username": "anna", "password": "falsch"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "13"


def test_native_login_uses_cloudflare_as_only_auth_when_local_auth_is_disabled(
    client, monkeypatch
):
    import app.routes.api_auth as api_auth

    monkeypatch.setattr(api_auth, "auth_disabled", lambda: True)
    monkeypatch.setattr(api_auth, "request_is_from_trusted_proxy", lambda _request: True)
    monkeypatch.setattr(api_auth, "check_credentials", lambda *_: False)
    monkeypatch.setattr(
        api_auth,
        "create_session",
        lambda username: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "oliver", "password": "nicht-ausgewertet"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "token": "cloudflare-access",
        "token_type": "bearer",
        "expires_in": 1209600,
        "username": "local",
        "role": "admin",
        "is_admin": True,
        "full_access": True,
        "read_only": False,
    }


def test_native_guest_login_creates_no_database_user(client, test_db, monkeypatch):
    import app.routes.api_auth as api_auth

    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "create_guest_session", lambda: "signed-guest-token")

    response = client.post("/api/auth/guest")

    assert response.status_code == 200
    assert response.json() == {
        "token": "signed-guest-token",
        "token_type": "bearer",
        "expires_in": 1209600,
        "username": "Gast",
        "role": "guest",
        "is_admin": False,
        "full_access": False,
        "read_only": True,
    }
    assert test_db.user_get_by_name("Gast") is None


def test_native_session_accepts_authenticated_request(client, test_db, monkeypatch):
    import app.routes.api_auth as api_auth

    test_db.user_create("anna", "unused-test-hash", role="user")
    monkeypatch.setattr(api_auth, "request_user", lambda request: (
        "anna" if request.headers.get("authorization") == "Bearer valid-token" else None
    ))

    response = client.get(
        "/api/auth/session",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "anna",
        "role": "user",
        "is_admin": False,
        "full_access": False,
        "read_only": False,
    }


def test_native_session_reports_guest_as_read_only(client, monkeypatch):
    import app.routes.api_auth as api_auth

    monkeypatch.setattr(api_auth, "request_user", lambda _request: "Gast")
    monkeypatch.setattr(api_auth, "request_is_guest", lambda _request: True)

    response = client.get(
        "/api/auth/session",
        headers={"Authorization": "Bearer signed-guest-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "Gast",
        "role": "guest",
        "is_admin": False,
        "full_access": False,
        "read_only": True,
    }


def test_guest_can_read_recipes_but_cannot_write(client, monkeypatch):
    from app import auth
    from app.main import app

    class Config:
        def get(self, *keys, default=None):
            values = {
                ("web",): {"auth_disabled": False},
                ("web", "secret_key"): "g" * 48,
            }
            return values.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    guest_token = auth.create_guest_session()
    assert auth.session_user(guest_token) == "Gast"
    assert auth.session_is_guest(guest_token) is True
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    try:
        read_response = client.get(
            "/api/recipes",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
        write_response = client.post(
            "/api/cart/add",
            headers={"Authorization": f"Bearer {guest_token}"},
            json={"name": "Milch"},
        )
        admin_response = client.get(
            "/api/admin/overview",
            headers={"Authorization": f"Bearer {guest_token}"},
        )
    finally:
        app.dependency_overrides[auth.require_auth] = lambda: None
        app.dependency_overrides[auth.require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert read_response.status_code == 200
    assert write_response.status_code == 403
    assert write_response.json()["detail"] == "Der Gastzugang ist schreibgeschützt."
    assert admin_response.status_code == 403
    assert admin_response.json()["detail"] == "Der Gastzugang ist schreibgeschützt."


def test_native_logout_revokes_server_sessions(client, monkeypatch):
    import app.routes.api_auth as api_auth

    class FakeDb:
        def user_revoke_sessions(self, username):
            assert username == "anna"
            return True

    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(api_auth, "request_is_guest", lambda _request: False)
    monkeypatch.setattr(api_auth, "request_user", lambda _request: "anna")
    monkeypatch.setattr(api_auth, "get_db", lambda: FakeDb())

    response = client.post(
        "/api/auth/logout",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "revoked": True}


def test_privacy_page_is_public_and_describes_native_data_handling(client):
    response = client.get("/privacy")

    assert response.status_code == 200
    assert "iOS-Schlüsselbund" in response.text
    assert "keine Telemetrie" in response.text


def test_login_next_value_is_html_escaped(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "auth_disabled", lambda: False)
    response = client.get(
        "/login",
        params={"next": '/\"><script>alert(1)</script>'},
    )

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
