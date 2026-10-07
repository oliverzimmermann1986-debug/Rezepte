"""Apple form_post with real ES256 client assertions and RS256 ID signatures.

All HTTP transport is synthetic; no Apple account or external service is used.
"""
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app import auth, oidc


@pytest.fixture(scope="module")
def apple_keys():
    return (ec.generate_private_key(ec.SECP256R1()),
            rsa.generate_private_key(public_exponent=65537, key_size=2048))


@pytest.fixture
def apple_api(client, test_db, monkeypatch, tmp_path, apple_keys):
    from app.main import app
    from app.routes import api_oidc
    from app.security import LoginRateLimiter

    class Config:
        def get(self, *keys, default=None):
            return {("web", "secret_key"): "apple-synthetic-session-key-" * 3}.get(keys, default)

    client_key, provider_key = apple_keys
    private_path = tmp_path / "synthetic-apple-signing.p8"
    private_path.write_bytes(client_key.private_bytes(serialization.Encoding.PEM,
                                                     serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    monkeypatch.setattr(auth, "get_config", lambda: Config())
    monkeypatch.setattr(oidc, "get_config", lambda: Config())
    monkeypatch.setattr(oidc, "_keys", {})
    monkeypatch.setattr(api_oidc, "flow_limiter", LoginRateLimiter(max_fails=100))
    for key, value in {"REZEPTE_PUBLIC_URL": "https://testserver", "REZEPTE_APPLE_CLIENT_ID": "test.apple.service",
                       "REZEPTE_APPLE_TEAM_ID": "TESTTEAM", "REZEPTE_APPLE_KEY_ID": "TESTKEY",
                       "REZEPTE_APPLE_PRIVATE_KEY_PATH": str(private_path)}.items():
        monkeypatch.setenv(key, value)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    test_db.user_create("admin", "unusable", role="admin")
    client.base_url = "https://testserver"
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(provider_key.public_key()))
    jwk.update(kid="apple-synthetic-key", use="sig", alg="RS256")
    state = {"nonce": "", "claims": {}, "token_calls": [], "revocations": []}

    class Response:
        status_code = 200
        def __init__(self, body):
            self.body = body
        def json(self):
            return self.body

    def assert_client_secret(data):
        secret = data["client_secret"]
        assert jwt.get_unverified_header(secret)["kid"] == "TESTKEY"
        claims = jwt.decode(secret, client_key.public_key(), algorithms=["ES256"],
                            audience="https://appleid.apple.com", issuer="TESTTEAM")
        assert claims["sub"] == "test.apple.service" and claims["exp"] - claims["iat"] == 300
        assert data["client_id"] == "test.apple.service"

    def request(method, url, **kwargs):
        assert kwargs["timeout"] == 10 and kwargs["allow_redirects"] is False
        if url == oidc.PROVIDERS["apple"]["jwks"]:
            assert method == "GET"
            return Response({"keys": [jwk]})
        assert method == "POST" and url == oidc.PROVIDERS["apple"]["token"]
        data = kwargs["data"]
        assert_client_secret(data)
        assert data["grant_type"] == "authorization_code"
        assert data["redirect_uri"] == "https://testserver/api/auth/apple/callback"
        assert "code_verifier" not in data
        state["token_calls"].append(data)
        claims = {"iss": "https://appleid.apple.com", "aud": "test.apple.service", "sub": "apple-unique-subject",
                  "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": state["nonce"],
                  "email": "apple-user@privaterelay.appleid.com", "email_verified": "true", **state["claims"]}
        return Response({"id_token": jwt.encode(claims, provider_key, algorithm="RS256",
                                                 headers={"kid": "apple-synthetic-key"}),
                         "refresh_token": "apple-private-refresh-grant", "access_token": "apple-private-access"})

    def revoke(url, **kwargs):
        assert url == oidc.PROVIDERS["apple"]["revoke"]
        assert kwargs["timeout"] == 10 and kwargs["allow_redirects"] is False
        data = kwargs["data"]
        assert_client_secret(data)
        assert data["token_type_hint"] == "refresh_token"
        assert data["token"] == "apple-private-refresh-grant"
        state["revocations"].append(data)
        return Response({})

    monkeypatch.setattr(oidc.requests, "request", request)
    monkeypatch.setattr(oidc.requests, "post", revoke)
    return client, test_db, state


def _web_start(api):
    client, _, state = api
    response = client.get("/auth/apple/start", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["response_mode"] == ["form_post"] and query["response_type"] == ["code"]
    assert query["client_id"] == ["test.apple.service"]
    assert query["scope"] == ["email"]
    state["nonce"] = query["nonce"][0]
    return query["state"][0]


def _post_callback(client, flow):
    return client.post("/api/auth/apple/callback", data={"state": flow, "code": "apple-single-use-code"},
                       headers={"Origin": "https://appleid.apple.com"}, follow_redirects=False)


def test_apple_web_form_post_signatures_session_and_account_deletion(apple_api):
    client, db, state = apple_api
    flow = _web_start(apple_api)
    result = _post_callback(client, flow)
    assert result.status_code == 303 and result.headers["location"] == "/"
    token = client.cookies.get(auth.SESSION_COOKIE)
    assert token and auth.session_user(token)
    profile = client.get("/api/account/profile").json()
    assert profile["role"] == "user" and profile["password_enabled"] is False
    active = db.session_get_active(auth._session_payload(token)["sid"], profile["id"])
    assert active["auth_method"] == "apple" and active["authenticated_at"] >= time.time() - 10
    assert len(state["token_calls"]) == 1
    with db.conn() as connection:
        identity = dict(connection.execute("SELECT * FROM oidc_identities").fetchone())
    assert identity["subject"] == "apple-unique-subject" and identity["user_id"] == profile["id"]
    assert "apple-private-refresh-grant" not in identity["token_secret"]
    assert _post_callback(client, flow).status_code == 400
    deleted = client.request("DELETE", "/api/account/profile", json={}, headers={"Origin": "https://testserver"})
    assert deleted.status_code == 200
    assert auth.session_user(token) is None
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM oidc_revocations").fetchone()[0] == 1
    oidc.retry_revocations(db)
    assert len(state["revocations"]) == 1
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oidc_revocations").fetchone()[0] == 0


def test_apple_native_form_post_requires_app_verifier_before_user_creation(apple_api):
    client, db, state = apple_api
    verifier = secrets.token_urlsafe(32)
    start = client.post("/api/auth/apple/start", json={"code_challenge": oidc.challenge(verifier)})
    assert start.status_code == 200
    query = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    state["nonce"] = query["nonce"][0]
    response = _post_callback(client, start.json()["flow_id"])
    assert response.status_code == 303
    result = parse_qs(urlsplit(response.headers["location"]).query)
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    payload = {"code": result["code"][0], "code_verifier": "A" * 43}
    assert client.post("/api/auth/exchange", json=payload).status_code == 400
    payload["code_verifier"] = verifier
    response = client.post("/api/auth/exchange", json=payload)
    assert response.status_code == 200 and response.json()["password_enabled"] is False
    assert "apple-private" not in response.text
    assert auth.session_user(response.json()["token"]) == response.json()["username"]
    assert client.post("/api/auth/exchange", json=payload).status_code == 400


@pytest.mark.parametrize("claims", [{"aud": "another.apple.service"}, {"nonce": "replayed-nonce"},
                                     {"iss": "https://accounts.google.com"}, {"exp": 1}])
def test_invalid_apple_identity_never_creates_account(apple_api, claims):
    client, db, state = apple_api
    flow = _web_start(apple_api)
    state["claims"] = claims
    result = _post_callback(client, flow)
    assert result.status_code == 303 and result.headers["location"] == "/login?provider_error=1"
    assert client.cookies.get(auth.SESSION_COOKIE) is None
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oidc_identities").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
