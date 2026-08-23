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
    }


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
    }


def test_native_logout_revokes_server_sessions(client, monkeypatch):
    import app.routes.api_auth as api_auth

    class FakeDb:
        def user_revoke_sessions(self, username):
            assert username == "anna"
            return True

    monkeypatch.setattr(api_auth, "auth_disabled", lambda: False)
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
