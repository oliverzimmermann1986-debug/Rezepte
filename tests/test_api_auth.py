def test_native_login_returns_bearer_session(client, monkeypatch):
    import app.routes.api_auth as api_auth

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
    }


def test_native_session_accepts_authenticated_request(client, monkeypatch):
    import app.routes.api_auth as api_auth

    monkeypatch.setattr(api_auth, "request_user", lambda request: (
        "anna" if request.headers.get("authorization") == "Bearer valid-token" else None
    ))

    response = client.get(
        "/api/auth/session",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "anna", "full_access": True}


def test_privacy_page_is_public_and_describes_native_data_handling(client):
    response = client.get("/privacy")

    assert response.status_code == 200
    assert "iOS-Schlüsselbund" in response.text
    assert "keine Telemetrie" in response.text
