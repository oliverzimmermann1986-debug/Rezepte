"""Durable provider revocation without retaining already-invalid tokens."""
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi import HTTPException

from app import oidc


@pytest.fixture
def revocation_db(test_db, monkeypatch):
    class Config:
        def get(self, *keys, default=None):
            return {("web", "secret_key"): "provider-revocation-test-key-" * 3}.get(keys, default)

    monkeypatch.setattr(oidc, "get_config", lambda: Config())
    monkeypatch.setenv("REZEPTE_PUBLIC_URL", "https://recipes.example.test")
    monkeypatch.setenv("REZEPTE_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("REZEPTE_GOOGLE_CLIENT_SECRET", "test-secret")
    test_db.user_create("admin", "unusable", role="admin")
    return test_db


def _queue(db, *, provider="google", token="private-grant"):
    with db.conn() as connection:
        connection.execute("INSERT INTO oidc_revocations(provider,token_secret,created_at,next_attempt) VALUES(?,?,?,0)",
                           (provider, oidc._seal({"token": token, "refresh": True}), time.time()))


def _pending(db):
    with db.conn() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM oidc_revocations")]


def test_google_requests_offline_access_for_future_revocation(revocation_db):
    result = oidc.begin(revocation_db, "google", platform="native", intent="login",
                        code_challenge=oidc.challenge(secrets.token_urlsafe(32)))
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["access_type"] == ["offline"]
    assert query["scope"] == ["openid email"]


@pytest.mark.parametrize("replacement", ["access", "refresh", "missing"])
def test_existing_refresh_grant_survives_later_access_only_login(revocation_db, replacement):
    db = revocation_db
    flow = {"intent": "login", "invitation": ""}
    claims = {"sub": "stable-google-subject", "email": None}
    original = oidc._seal({"token": "original-refresh", "refresh": True})
    first = oidc._bind_identity(db, "google", flow, claims, original)
    incoming = None if replacement == "missing" else oidc._seal({"token": "new-token", "refresh": replacement == "refresh"})
    second = oidc._bind_identity(db, "google", flow, claims, incoming)
    assert first["user_id"] == second["user_id"]
    with db.conn() as connection:
        saved = connection.execute("SELECT token_secret FROM oidc_identities").fetchone()[0]
    assert oidc._unseal(saved) == {"token": "new-token" if replacement == "refresh" else "original-refresh", "refresh": True}
    assert "original-refresh" not in saved and "new-token" not in saved


def test_lost_success_response_then_invalid_token_finishes_revocation(revocation_db, monkeypatch):
    db = revocation_db
    _queue(db)
    calls = []

    class Response:
        status_code = 400
        def json(self):
            return {"error": "invalid_token", "error_description": "Token expired or revoked"}

    def revoke(url, **kwargs):
        calls.append(kwargs)
        assert url == oidc.PROVIDERS["google"]["revoke"]
        assert kwargs["data"] == {"token": "private-grant"}
        assert kwargs["allow_redirects"] is False
        if len(calls) == 1:
            raise requests.Timeout("response lost after successful provider revocation")
        return Response()

    monkeypatch.setattr(oidc.requests, "post", revoke)
    oidc.retry_revocations(db)
    pending = _pending(db)
    assert len(pending) == 1 and pending[0]["attempts"] == 1
    assert pending[0]["next_attempt"] > time.time()
    with db.conn() as connection:
        connection.execute("UPDATE oidc_revocations SET next_attempt=0")
    oidc.retry_revocations(db)
    assert _pending(db) == []
    oidc.retry_revocations(db)
    assert len(calls) == 2


@pytest.mark.parametrize("status,payload", [(400, {"error": "invalid_request"}),
                                           (503, {"error": "invalid_token"}),
                                           (400, ["invalid_token"]), (400, None)])
def test_only_exact_google_terminal_error_removes_outbox(revocation_db, monkeypatch, status, payload):
    _queue(revocation_db)

    class Response:
        status_code = status
        def json(self):
            if payload is None:
                raise ValueError("invalid json")
            return payload

    monkeypatch.setattr(oidc.requests, "post", lambda *args, **kwargs: Response())
    oidc.retry_revocations(revocation_db)
    pending = _pending(revocation_db)
    assert len(pending) == 1 and pending[0]["attempts"] == 1


def test_apple_400_never_uses_google_invalid_token_exception(revocation_db, monkeypatch):
    _queue(revocation_db, provider="apple")
    monkeypatch.setattr(oidc, "configuration", lambda _: {"client_id": "apple-client"})
    monkeypatch.setattr(oidc, "_client_secret", lambda *_: "synthetic-client-secret")

    class Response:
        status_code = 400
        def json(self):
            return {"error": "invalid_token"}

    monkeypatch.setattr(oidc.requests, "post", lambda *args, **kwargs: Response())
    oidc.retry_revocations(revocation_db)
    assert _pending(revocation_db)[0]["attempts"] == 1


@pytest.mark.parametrize("change", ["revoked", "disabled"])
def test_disconnect_checks_confirmed_account_state_in_transaction(revocation_db, change):
    db = revocation_db
    user_id = db.user_create("member", "stored-password-hash")
    with db.conn() as connection:
        connection.execute("INSERT INTO oidc_identities(user_id,provider,subject,token_secret,linked_at) VALUES(?,'google','subject',?,?)",
                           (user_id, oidc._seal({"token": "refresh-grant", "refresh": True}), time.time()))
    if change == "revoked":
        db.user_revoke_sessions_by_id(user_id)
    else:
        db.user_set_disabled(user_id, True)
    with pytest.raises(HTTPException) as result:
        oidc.disconnect(db, user_id, "google", expected_version=0)
    assert result.value.status_code == (409 if change == "revoked" else 401)
    assert len(oidc.identity_list(db, user_id)) == 1
    assert _pending(db) == []
