"""Provider signatures, browser CSRF bindings and native one-use handoffs."""
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import auth, oidc


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def provider_api(client, test_db, monkeypatch, signing_key):
    from app.main import app
    from app.routes import api_oidc
    from app.security import LoginRateLimiter

    class Config:
        def get(self, *keys, default=None):
            return {("web", "secret_key"): "oidc-testing-secret-" * 4}.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    monkeypatch.setattr(oidc, "get_config", lambda: Config())
    monkeypatch.setattr(oidc, "_keys", {})
    monkeypatch.setattr(api_oidc, "flow_limiter", LoginRateLimiter(max_fails=100))
    monkeypatch.setenv("REZEPTE_PUBLIC_URL", "https://testserver")
    monkeypatch.setenv("REZEPTE_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("REZEPTE_GOOGLE_CLIENT_SECRET", "client-secret-never-public")
    for key in ("CLIENT_ID", "TEAM_ID", "KEY_ID", "PRIVATE_KEY_PATH"):
        monkeypatch.delenv("REZEPTE_APPLE_" + key, raising=False)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    test_db.user_create("admin", "unusable", role="admin")
    test_db.user_create("alice", auth.hash_password("alice-password"))
    client.base_url = "https://testserver"
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="test-key", use="sig", alg="RS256")
    state = {"claims": {}, "nonce": "", "subject": "google-subject", "token_calls": []}

    def http(method, url, **kwargs):
        if url == oidc.PROVIDERS["google"]["jwks"]:
            return {"keys": [key]}
        assert url == oidc.PROVIDERS["google"]["token"] and method == "POST"
        state["token_calls"].append(kwargs["data"])
        claims = {"iss": "https://accounts.google.com", "aud": "test-client", "sub": state["subject"],
                  "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": state["nonce"],
                  "email": "alice@example.test", "email_verified": True, **state["claims"]}
        return {"id_token": jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"}),
                "refresh_token": "private-provider-grant"}

    monkeypatch.setattr(oidc, "_json_request", http)
    return client, test_db, state


def start(api, *, intent="login", password="", provider="google"):
    client, db, state = api
    verifier = secrets.token_urlsafe(32)
    response = client.post(f"/api/auth/{provider}/start", json={
        "intent": intent, "code_challenge": oidc.challenge(verifier), "current_password": password})
    assert response.status_code == 200, response.text
    result = response.json()
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    state["nonce"] = params["nonce"][0]
    return result["flow_id"], verifier


def callback(api, flow):
    client, _, _ = api
    response = client.get("/api/auth/google/callback", params={"state": flow, "code": "provider-code"},
                          follow_redirects=False)
    assert response.status_code == 303, response.text
    assert "no-store" in response.headers["cache-control"]
    location = urlsplit(response.headers["location"])
    assert (location.scheme, location.netloc, location.path) == ("de.mausbaeren.rezepte", "auth", "/callback")
    query = parse_qs(location.query)
    assert query["flow_id"] == [flow]
    assert "private-provider-grant" not in response.headers["location"]
    return query


def complete(api, flow, verifier):
    query = callback(api, flow)
    assert "code" in query, query
    return api[0].post("/api/auth/exchange", json={"code": query["code"][0], "code_verifier": verifier})


def login_local(api):
    token = auth.create_session("alice")
    api[0].headers["Authorization"] = "Bearer " + token
    return token


def test_provider_discovery_does_not_expose_configuration(provider_api):
    client, _, _ = provider_api
    response = client.get("/api/auth/providers")
    assert response.json() == {"providers": [
        {"id": "apple", "name": "Apple", "enabled": False},
        {"id": "google", "name": "Google", "enabled": True}]}
    assert "secret" not in response.text
    assert client.post("/api/auth/apple/start", json={"code_challenge": "A" * 43}).status_code == 503
    page = client.get("/login").text
    assert 'href="/auth/google/start"' in page and 'href="/auth/apple/start"' not in page


def test_native_login_single_use_pkce_and_no_email_autolinking(provider_api):
    client, db, state = provider_api
    flow, verifier = start(provider_api)
    query = callback(provider_api, flow)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
        saved = c.execute("SELECT data_secret FROM oidc_exchanges").fetchone()[0]
        assert "private-provider-grant" not in saved and "google-subject" not in saved
    code = query["code"][0]
    assert client.post("/api/auth/exchange", json={"code": code, "code_verifier": "A" * 43}).status_code == 400
    response = client.post("/api/auth/exchange", json={"code": code, "code_verifier": verifier})
    assert response.status_code == 200, response.text
    assert response.json()["username"].startswith("alice_") and response.json()["username"] != "alice"
    assert response.json()["password_enabled"] is False
    assert response.json()["role"] == "user"
    assert auth.session_user(response.json()["token"]) == response.json()["username"]
    assert client.post("/api/auth/exchange", json={"code": code, "code_verifier": verifier}).status_code == 400
    assert client.get("/api/auth/google/callback", params={"state": flow, "code": "x"}).status_code == 400
    assert state["token_calls"][0]["code_verifier"] != verifier


def test_new_google_account_can_edit_own_recipes_but_cannot_start_ai_or_imports(provider_api):
    from tests.test_ai_operation_permissions import assert_provider_user_can_edit_but_cannot_start_jobs

    client, db, _ = provider_api
    flow, verifier = start(provider_api)
    response = complete(provider_api, flow, verifier)
    assert response.status_code == 200, response.text
    result = response.json()
    client.headers["Authorization"] = "Bearer " + result["token"]
    assert_provider_user_can_edit_but_cannot_start_jobs(client, db, result["username"])


def test_google_link_preserves_existing_administrator_role(provider_api):
    client, db, _ = provider_api
    user = db.user_get_by_name("alice")
    db.user_set_role(user["id"], "admin")
    login_local(provider_api)
    flow, verifier = start(provider_api, intent="link", password="alice-password")
    response = complete(provider_api, flow, verifier)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == user["id"] and response.json()["role"] == "admin"
    assert db.user_get_by_name("alice")["role"] == "admin"
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    assert client.get("/api/users").status_code == 200


@pytest.mark.parametrize("claims", [
    {"iss": "https://attacker.test"}, {"aud": "other-client"}, {"nonce": "wrong"},
    {"azp": "other-client"}, {"exp": 1}, {"iat": int(time.time()) + 3600}, {"sub": ""},
])
def test_invalid_provider_claims_never_create_identity(provider_api, claims):
    _, db, state = provider_api
    flow, verifier = start(provider_api)
    state["claims"] = claims
    assert callback(provider_api, flow)["error"] == ["cancelled"]
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM oidc_exchanges").fetchone()[0] == 0


def test_forged_signature_and_malformed_jwks_rejected(provider_api, signing_key, monkeypatch):
    config = oidc.configuration("google")
    claims = {"iss": "https://accounts.google.com", "aud": "test-client", "sub": "s", "nonce": "n",
              "iat": int(time.time()), "exp": int(time.time()) + 300}
    forged = jwt.encode(claims, "a" * 40, algorithm="HS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException):
        oidc.verify_identity("google", forged, config, "n")
    encoded = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})
    monkeypatch.setattr(oidc, "_json_request", lambda *a, **k: {"keys": [None]})
    with pytest.raises(HTTPException):
        oidc.verify_identity("google", encoded, config, "n")


def test_link_requires_account_reauthentication(provider_api):
    client, _, _ = provider_api
    login_local(provider_api)
    response = client.post("/api/auth/google/start", json={"intent": "link", "code_challenge": "A" * 43})
    assert response.status_code == 403
    flow, verifier = start(provider_api, intent="link", password="alice-password")
    response = complete(provider_api, flow, verifier)
    assert response.status_code == 200 and response.json()["username"] == "alice"
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    assert client.get("/api/account/identities").json()["identities"][0]["provider"] == "google"
    # Reconfirming the SAME identity proves possession without a local password.
    flow, verifier = start(provider_api, intent="link")
    assert complete(provider_api, flow, verifier).status_code == 200


@pytest.mark.parametrize("revoke", ["current", "all", "disabled"])
def test_link_cannot_finish_after_origin_session_is_revoked(provider_api, revoke):
    client, db, _ = provider_api
    login_local(provider_api)
    flow, verifier = start(provider_api, intent="link", password="alice-password")
    if revoke == "disabled":
        with db.conn() as c:
            c.execute("UPDATE users SET disabled=1 WHERE username='alice'")
    else:
        client.post("/api/auth/logout" + ("-all" if revoke == "all" else ""))
    query = callback(provider_api, flow)
    result = client.post("/api/auth/exchange", json={"code": query["code"][0], "code_verifier": verifier})
    assert result.status_code == 401
    assert oidc.identity_list(db, db.user_get_by_name("alice")["id"]) == []


def test_reconfirm_cannot_switch_provider_subject(provider_api):
    client, db, state = provider_api
    login_local(provider_api)
    flow, verifier = start(provider_api, intent="link", password="alice-password")
    assert complete(provider_api, flow, verifier).status_code == 200
    flow, verifier = start(provider_api, intent="link")
    state["subject"] = "another-google-account"
    assert complete(provider_api, flow, verifier).status_code == 409
    with db.conn() as c:
        assert c.execute("SELECT subject FROM oidc_identities").fetchone()[0] == "google-subject"


def test_reconfirm_cannot_turn_into_new_link_after_disconnect(provider_api):
    client, db, state = provider_api
    login_local(provider_api)
    flow, verifier = start(provider_api, intent="link", password="alice-password")
    assert complete(provider_api, flow, verifier).status_code == 200
    flow, verifier = start(provider_api, intent="link")
    oidc.disconnect(db, db.user_get_by_name("alice")["id"], "google")
    state["subject"] = "attacker-account"
    assert complete(provider_api, flow, verifier).status_code == 409
    assert oidc.identity_list(db, db.user_get_by_name("alice")["id"]) == []


def test_flow_expiry_and_duplicate_callback_parameters(provider_api):
    client, db, _ = provider_api
    flow, _ = start(provider_api)
    response = client.get("/api/auth/google/callback", params=[("state", flow), ("state", flow), ("code", "x")])
    assert response.status_code == 400
    with db.conn() as c:
        c.execute("UPDATE oidc_flows SET expires_at=0")
    assert client.get("/api/auth/google/callback", params={"state": flow, "code": "x"}).status_code == 400


def test_google_web_link_requires_origin_and_password(provider_api):
    client, _, state = provider_api
    client.cookies.set(auth.SESSION_COOKIE, auth.create_session("alice"))
    assert client.post("/auth/google/link", headers={"Origin": "https://testserver"}).status_code == 403
    response = client.post("/auth/google/link", data={"current_password": "alice-password"},
                           headers={"Origin": "https://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    state["nonce"] = query["nonce"][0]
    response = client.get("/api/auth/google/callback", params={"state": query["state"][0], "code": "x"},
                          follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/account"
    assert client.get("/api/account/profile").json()["username"] == "alice"


def test_web_cookie_binding_and_cookie_session(provider_api):
    client, _, state = provider_api
    response = client.get("/auth/google/start", follow_redirects=False)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    state["nonce"] = query["nonce"][0]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=none" in cookie
    saved = dict(client.cookies)
    client.cookies.clear()
    params = {"state": query["state"][0], "code": "code"}
    assert client.get("/api/auth/google/callback", params=params).status_code == 400
    client.cookies.update(saved)
    response = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client.get("/api/account/profile").status_code == 200


def test_apple_cross_site_exception_does_not_weaken_other_routes(provider_api):
    client, _, _ = provider_api
    client.cookies.set(auth.SESSION_COOKIE, auth.create_session("alice"))
    assert client.post("/api/account/password", json={"new_password": "new-password"}).status_code == 403
    assert client.post("/auth/google/link").status_code == 403
    # No origin required here, but no valid state/binding means no login.
    assert client.post("/api/auth/apple/callback", data={"state": "A" * 43, "code": "x"}).status_code == 400
    assert client.post("/api/auth/google/callback", data={"state": "A" * 43, "code": "x"}).status_code == 403


def test_disconnect_preserves_login_and_revokes_provider_sessions(provider_api, monkeypatch):
    client, db, _ = provider_api
    flow, verifier = start(provider_api)
    result = complete(provider_api, flow, verifier).json()
    client.headers["Authorization"] = "Bearer " + result["token"]
    response = client.request("DELETE", "/api/account/identities/google", json={})
    assert response.status_code == 409
    with db.conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password("new-password"), result["id"]))
    response = client.request("DELETE", "/api/account/identities/google", json={})
    assert response.status_code == 200
    assert auth.session_user(result["token"]) is None
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM oidc_revocations").fetchone()[0] == 1
    calls = []
    class Response:
        status_code = 200
    monkeypatch.setattr(oidc.requests, "post", lambda url, **kw: calls.append((url, kw)) or Response())
    oidc.retry_revocations(db)
    assert calls[0][1]["data"]["token"] == "private-provider-grant"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM oidc_revocations").fetchone()[0] == 0


def test_exchange_expiry_and_provider_mixup(provider_api):
    client, db, _ = provider_api
    flow, verifier = start(provider_api)
    assert client.get("/api/auth/apple/callback", params={"state": flow, "code": "x"}).status_code == 400
    code = callback(provider_api, flow)["code"][0]
    with db.conn() as c:
        c.execute("UPDATE oidc_exchanges SET expires_at=0")
    assert client.post("/api/auth/exchange", json={"code": code, "code_verifier": verifier}).status_code == 400


def test_access_log_strips_callback_secrets():
    from app.main import ProviderCallbackLogFilter
    record = logging.LogRecord("uvicorn.access", 20, "", 0, "%s - %s %s %s %s", (
        "client", "GET", "/api/auth/google/callback?code=secret&state=private", "1.1", 303), None)
    assert ProviderCallbackLogFilter().filter(record)
    assert "secret" not in record.getMessage() and "private" not in record.getMessage()


def test_auth_validation_never_echoes_secret_inputs(provider_api):
    client, _, _ = provider_api
    response = client.post("/api/auth/exchange", json={"code": "private-code", "code_verifier": "private-verifier"})
    assert response.status_code == 422
    assert "private-code" not in response.text and "private-verifier" not in response.text
    response = client.post("/api/auth/register", json={"username": "someone", "password": "secret"})
    assert response.status_code == 422 and "secret" not in response.text
