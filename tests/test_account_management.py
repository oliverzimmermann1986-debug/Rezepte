"""Account security through real authentication and temporary SQLite databases."""
import time

import pytest
from starlette.requests import Request

from app import accounts, auth
from app.db import Database


@pytest.fixture(scope="module")
def password_hash():
    return auth.hash_password("old-pass")  # Existing eight-character passwords remain valid.


@pytest.fixture
def account_api(client, test_db, monkeypatch, password_hash):
    from app import account_security
    from app.main import app
    from app.security import LoginRateLimiter

    class Config:
        def get(self, *keys, default=None):
            return {("web", "secret_key"): "account-management-test-key-" * 3}.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    monkeypatch.setattr(account_security, "recent_auth_limiter", LoginRateLimiter())
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    app.dependency_overrides.pop(auth.require_import, None)
    admin_id = test_db.user_create("admin", password_hash, role="admin")
    user_id = test_db.user_create("anna", password_hash)
    test_db.user_create("bert", password_hash)
    client.headers["Authorization"] = "Bearer " + auth.create_session("anna")
    return client, test_db, user_id, admin_id


def _headers(token):
    return {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("provider,email", [
    ("apple", "private-relay@privaterelay.appleid.com"),
    ("apple", None),
    ("google", "provider-person@example.invalid"),
])
def test_admin_list_includes_real_passwordless_provider_accounts(account_api, provider, email):
    from app import oidc

    client, db, _, _ = account_api
    subject = "synthetic-subject-not-for-admin-list"
    secret = "synthetic-encrypted-grant-not-for-admin-list"
    identity = oidc._bind_identity(db, provider, {"intent": "login", "invitation": ""},
                                   {"sub": subject, "email": email}, secret)
    account = db.user_get_by_name(identity["username"])
    assert account["password_hash"] == ""
    token = auth.create_session(identity["username"], auth_method=provider, expected_identity=identity)
    assert client.get("/api/users", headers=_headers(token)).status_code == 403

    admin = _headers(auth.create_session("admin"))
    response = client.get("/api/users", headers=admin)
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["users"]}
    listed = by_id[identity["user_id"]]
    assert listed["auth_methods"] == [provider]
    assert listed["role"] == "user" and listed["disabled"] is False
    provider_session = client.get("/api/auth/session", headers=_headers(token)).json()
    assert provider_session["role"] == "user" and provider_session["can_import"] is False
    assert set(listed) == {"id", "username", "role", "disabled", "created_at", "last_login_at", "auth_methods"}
    assert subject not in response.text and secret not in response.text
    if email:
        assert email not in response.text
    assert account["session_version"] == db.user_get_by_name(identity["username"])["session_version"]

    assert client.patch(f"/api/users/{identity['user_id']}", json={"disabled": True}, headers=admin).status_code == 200
    response = client.get("/api/users", headers=admin)
    disabled = next(item for item in response.json()["users"] if item["id"] == identity["user_id"])
    assert disabled["disabled"] is True and disabled["auth_methods"] == [provider]


def test_admin_list_auth_methods_are_deterministic_unique_and_private(account_api, password_hash):
    client, db, user_id, admin_id = account_api
    # Insert in reverse display order to catch order derived from row insertion.
    with db.conn() as connection:
        for provider in ("google", "apple"):
            connection.execute(
                "INSERT INTO oidc_identities(user_id,provider,subject,email,token_secret,linked_at) VALUES(?,?,?,?,?,?)",
                (user_id, provider, provider + "-private-subject", "private-contact@example.invalid",
                 provider + "-private-grant", time.time()),
            )
    response = client.get("/api/users", headers=_headers(auth.create_session("admin")))
    assert response.status_code == 200
    items = response.json()["users"]
    assert len({item["id"] for item in items}) == len(items) == 3
    assert next(item for item in items if item["id"] == user_id)["auth_methods"] == ["password", "apple", "google"]
    assert next(item for item in items if item["id"] == admin_id)["auth_methods"] == ["password"]
    assert all(len(item["auth_methods"]) == len(set(item["auth_methods"])) for item in items)
    for sensitive in ("password_hash", "subject", "email", "token_secret", password_hash,
                      "private-contact@example.invalid", "apple-private-grant", "google-private-grant"):
        assert sensitive not in response.text
    assert client.get("/api/users").status_code == 403


@pytest.mark.parametrize("endpoint", ["native", "web"])
@pytest.mark.parametrize("admin_action", ["reset", "recreate", "disable", "role", "revoke", "delete"])
@pytest.mark.parametrize("race_stage", ["verification", "session_insert"])
def test_password_login_rejects_account_changed_during_verification(account_api, monkeypatch, endpoint, admin_action, race_stage):
    client, db, user_id, _ = account_api
    client.headers.pop("Authorization", None)
    client.cookies.clear()
    replacement_hash = auth.hash_password("replacement-password")
    original_verify = auth.verify_password
    original_create = db.session_create
    changed = False

    def change_account():
        nonlocal changed
        assert not changed
        changed = True
        if admin_action == "reset":
            db.user_set_password(user_id, replacement_hash)
        elif admin_action == "disable":
            db.user_set_disabled(user_id, True)
        elif admin_action == "role":
            db.user_update_security(user_id, role="admin")
        elif admin_action == "revoke":
            db.user_revoke_sessions("anna")
        else:
            assert db.user_delete(user_id)
            if admin_action == "recreate":
                assert db.user_create("anna", replacement_hash) != user_id

    def verify_then_change_account(plain, stored):
        accepted = original_verify(plain, stored)
        if accepted and plain == "old-pass" and not changed:
            change_account()
        return accepted

    def create_after_account_change(*args, **kwargs):
        change_account()
        return original_create(*args, **kwargs)

    if race_stage == "verification":
        monkeypatch.setattr(auth, "verify_password", verify_then_change_account)
    else:
        monkeypatch.setattr(db, "session_create", create_after_account_change)
    if endpoint == "native":
        response = client.post("/api/auth/login", json={"username": "anna", "password": "old-pass"})
        token = response.json().get("token", "")
    else:
        response = client.post("/login", data={"username": "anna", "password": "old-pass"},
                               headers={"Origin": "http://testserver"}, follow_redirects=False)
        token = response.cookies.get(auth.SESSION_COOKIE, "")
    assert changed
    current_user = db.user_get_by_name("anna")
    if admin_action in {"reset", "recreate"}:
        assert current_user["password_hash"] == replacement_hash
    accepted_session = bool(token and auth.session_user(token) == "anna")
    assert response.status_code == 401, f"status={response.status_code}; stale password issued valid session={accepted_session}"
    assert not token
    assert not current_user or not db.session_list(current_user["id"])


@pytest.mark.parametrize("admin_action", ["reset", "recreate"])
def test_registration_session_is_bound_to_inserted_account(account_api, monkeypatch, admin_action):
    client, db, _, _ = account_api
    original_register = accounts.register
    replacement_hash = auth.hash_password("replacement-password")

    def register_then_change_account(*args, **kwargs):
        user_id = original_register(*args, **kwargs)
        if admin_action == "reset":
            db.user_set_password(user_id, replacement_hash)
        else:
            assert db.user_delete(user_id)
            assert db.user_create("new-person", replacement_hash) != user_id
        return user_id

    monkeypatch.setattr(accounts, "register", register_then_change_account)
    response = client.post("/api/auth/register", json={"username": "new-person", "password": "registration-password"})
    assert response.status_code == 401 and "token" not in response.json()
    assert not db.session_list(db.user_get_by_name("new-person")["id"])


@pytest.mark.parametrize("endpoint", ["native", "web"])
def test_unchanged_password_login_verifies_once_and_issues_session(account_api, monkeypatch, endpoint):
    client, db, _, _ = account_api
    client.headers.pop("Authorization", None)
    original_verify = auth.verify_password
    checks = []

    def verify(plain, stored):
        checks.append(plain == "old-pass")
        return original_verify(plain, stored)

    monkeypatch.setattr(auth, "verify_password", verify)
    if endpoint == "native":
        response = client.post("/api/auth/login", json={"username": "ANNA", "password": "old-pass"})
        assert response.status_code == 200
        token = response.json()["token"]
    else:
        response = client.post("/login", data={"username": "ANNA", "password": "old-pass"},
                               headers={"Origin": "http://testserver"}, follow_redirects=False)
        assert response.status_code == 303
        token = response.cookies[auth.SESSION_COOKIE]
    assert checks == [True]
    assert auth.session_user(token) == "anna"
    assert "password_hash" not in auth._session_payload(token)


@pytest.mark.parametrize("change", [None, "password", "version", "migrated_user"])
def test_legacy_password_identity_remains_bound_to_verified_config(test_db, monkeypatch, password_hash, change):
    values = {("web", "username"): "legacy-admin", ("web", "password"): password_hash,
              ("web", "secret_key"): "legacy-password-snapshot-test-key-" * 3,
              ("web", "session_version"): 0}

    class Config:
        def get(self, *keys, default=None):
            return values.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    proof = auth.password_login_identity("legacy-admin", "old-pass")
    assert proof and proof["legacy"]
    if change == "password":
        values[("web", "password")] = "replacement-password"
    elif change == "version":
        values[("web", "session_version")] = 1
    elif change == "migrated_user":
        test_db.user_create("legacy-admin", password_hash, role="admin")
    if change:
        with pytest.raises(ValueError):
            auth.create_session("legacy-admin", expected_credentials=proof)
    else:
        token = auth.create_session("legacy-admin", expected_credentials=proof)
        assert auth.session_user(token) == "legacy-admin"


def test_profile_and_session_payload_use_stable_user_identity(account_api):
    client, db, user_id, _ = account_api
    profile = client.get("/api/account/profile")
    assert profile.status_code == 200
    assert profile.json() == {"id": user_id, "username": "anna", "role": "user",
                              "created_at": db.user_get_by_name("anna")["created_at"],
                              "last_login_at": None, "password_enabled": True}
    session = client.get("/api/auth/session").json()
    assert session["id"] == user_id and session["password_enabled"] is True
    assert session["role"] == "user" and session["full_access"] is False
    assert "password_hash" not in str(profile.json())


def test_current_logout_preserves_other_device_and_is_idempotent(account_api):
    client, db, _, _ = account_api
    first = client.headers["Authorization"].removeprefix("Bearer ")
    second = auth.create_session("anna")
    assert first != second
    assert client.post("/api/auth/logout").json() == {"ok": True, "revoked": True}
    assert auth.session_user(first) is None
    assert auth.session_user(second) == "anna"
    assert db.user_get_by_name("anna")["session_version"] == 0
    assert client.post("/api/auth/logout").json() == {"ok": True, "revoked": False}


def test_logout_all_revokes_both_devices_but_not_another_user(account_api):
    client, _, _, _ = account_api
    first = client.headers["Authorization"].removeprefix("Bearer ")
    second, other = auth.create_session("anna"), auth.create_session("bert")
    assert client.post("/api/auth/logout-all").json()["revoked"]
    assert auth.session_user(first) is None and auth.session_user(second) is None
    assert auth.session_user(other) == "bert"


def test_sessions_list_and_individual_revoke_are_scoped(account_api):
    client, _, user_id, _ = account_api
    current = client.headers["Authorization"].removeprefix("Bearer ")
    request = Request({"type": "http", "headers": [(b"user-agent", b"Synthetic iPhone")]})
    second = auth.create_session("anna", request=request)
    other = auth.create_session("bert")
    second_id, other_id = auth._session_payload(second)["sid"], auth._session_payload(other)["sid"]
    sessions = client.get("/api/account/sessions").json()["sessions"]
    assert len(sessions) == 2 and sum(s["is_current"] for s in sessions) == 1
    assert next(s for s in sessions if s["id"] == second_id)["client_label"] == "Synthetic iPhone"
    assert set(sessions[0]) == {"id", "created_at", "last_seen_at", "expires_at", "client_label", "is_current"}
    assert client.delete("/api/account/sessions/" + other_id).status_code == 404
    assert client.delete("/api/account/sessions/" + second_id).status_code == 200
    assert auth.session_user(second) is None and auth.session_user(other) == "bert"
    assert auth.session_user(current) == "anna"
    assert len(client.get("/api/account/sessions").json()["sessions"]) == 1


def test_current_session_can_be_revoked_from_list(account_api):
    client, _, _, _ = account_api
    current = client.get("/api/account/sessions").json()["sessions"][0]
    assert current["is_current"]
    assert client.delete("/api/account/sessions/" + current["id"]).status_code == 200
    assert client.get("/api/account/profile").status_code == 401


def test_admin_revoke_requires_admin_and_preserves_other_accounts(account_api):
    client, _, user_id, _ = account_api
    user_token = client.headers["Authorization"].removeprefix("Bearer ")
    assert client.post(f"/api/users/{user_id}/revoke-sessions").status_code == 403
    admin_token = auth.create_session("admin")
    result = client.post(f"/api/users/{user_id}/revoke-sessions", headers=_headers(admin_token))
    assert result.status_code == 200
    assert auth.session_user(user_token) is None and auth.session_user(admin_token) == "admin"
    assert client.post("/api/users/999999/revoke-sessions", headers=_headers(admin_token)).status_code == 404


def test_password_change_requires_old_password_and_revokes_all(account_api):
    client, db, _, _ = account_api
    first = client.headers["Authorization"].removeprefix("Bearer ")
    second = auth.create_session("anna")
    payload = {"current_password": "wrong", "new_password": "new-password-strong"}
    assert client.post("/api/account/password", json=payload).status_code == 403
    assert auth.session_user(first) == "anna"
    payload["current_password"] = "old-pass"
    assert client.post("/api/account/password", json=payload).json() == {"ok": True, "reauthenticate": True}
    assert auth.session_user(first) is None and auth.session_user(second) is None
    assert auth.check_credentials("anna", "new-password-strong")
    assert not auth.check_credentials("anna", "old-pass")
    assert db.user_get_by_name("anna")["session_version"] == 1


@pytest.mark.parametrize("admin_action", ["reset", "disable", "delete"])
def test_password_change_cannot_overwrite_concurrent_admin_security_action(account_api, monkeypatch, admin_action):
    from app.routes import api_account

    client, db, user_id, _ = account_api
    reset_hash = auth.hash_password("admin-reset-password")

    def hash_after_admin_action(value):
        if admin_action == "reset":
            db.user_set_password(user_id, reset_hash)
        elif admin_action == "disable":
            db.user_set_disabled(user_id, True)
        else:
            assert db.user_delete(user_id)
        return auth.hash_password(value)

    monkeypatch.setattr(api_account, "hash_password", hash_after_admin_action)
    response = client.post("/api/account/password", json={"current_password": "old-pass", "new_password": "self-chosen-password"})
    assert response.status_code == 409
    user = db.user_get_by_name("anna")
    if admin_action == "reset":
        assert user["password_hash"] == reset_hash and user["session_version"] == 1
    elif admin_action == "disable":
        assert user["disabled"] == 1 and not auth.verify_password("self-chosen-password", user["password_hash"])
    else:
        assert user is None


@pytest.mark.parametrize("password", ["123456789", "x" * 73, "ä" * 37])
def test_new_password_policy_shared_by_self_admin_and_registration(account_api, password):
    client, db, user_id, _ = account_api
    assert client.post("/api/account/password", json={"current_password": "old-pass", "new_password": password}).status_code == 422
    admin = _headers(auth.create_session("admin"))
    created = client.post("/api/users", json={"username": "new-person", "password": password}, headers=admin)
    patched = client.patch(f"/api/users/{user_id}", json={"password": password}, headers=admin)
    registered = client.post("/api/auth/register", json={"username": "new-person", "password": password})
    assert created.status_code in {400, 422} and patched.status_code in {400, 422}
    assert registered.status_code == 422
    assert db.user_get_by_name("new-person") is None
    assert auth.check_credentials("anna", "old-pass")


def test_password_accepts_exact_utf8_limit(account_api):
    client, _, _, _ = account_api
    assert client.post("/api/account/password", json={"current_password": "old-pass", "new_password": "ä" * 36}).status_code == 200
    assert auth.check_credentials("anna", "ä" * 36)


@pytest.mark.parametrize("password", ["123456789", "x" * 73, "ä" * 37])
def test_legacy_config_password_change_obeys_same_policy(account_api, monkeypatch, password):
    from app.routes import api_config
    from tests.test_security_rbac import _ConfigStore

    client, db, _, _ = account_api
    before = db.user_get_by_name("admin")
    original = {"web": {"password": before["password_hash"], "session_version": 0}}
    store = _ConfigStore(original)
    monkeypatch.setattr(api_config, "get_config", lambda: store)
    token = auth.create_session("admin")
    response = client.put("/api/config", json={"web": {"password": password}}, headers=_headers(token))
    assert response.status_code == 400
    assert store.saved is False and store.data == original
    assert db.user_get_by_name("admin")["password_hash"] == before["password_hash"]
    assert auth.session_user(token) == "admin"


@pytest.mark.parametrize("provider", ["apple", "google"])
def test_fresh_provider_session_can_set_password_for_passwordless_account(account_api, provider):
    client, db, user_id, _ = account_api
    with db.conn() as connection:
        connection.execute("UPDATE users SET password_hash='' WHERE id=?", (user_id,))
    token = auth.create_session("anna", auth_method=provider)
    client.headers.update(_headers(token))
    assert client.get("/api/account/profile").json()["password_enabled"] is False
    assert client.post("/api/account/password", json={"new_password": "new-local-password"}).status_code == 200
    assert auth.session_user(token) is None and auth.check_credentials("anna", "new-local-password")


@pytest.mark.parametrize("method,age", [("apple", 301), ("google", 301), ("password", 0), ("apple", -60)])
def test_recent_auth_does_not_trust_stale_local_or_future_session(account_api, method, age):
    client, db, _, _ = account_api
    token = auth.create_session("anna", auth_method=method)
    sid = auth._session_payload(token)["sid"]
    with db.conn() as connection:
        connection.execute("UPDATE user_sessions SET authenticated_at=? WHERE id=?", (time.time() - age, sid))
    assert client.post("/api/account/password", json={"new_password": "new-local-password"}, headers=_headers(token)).status_code == 403


def test_request_activity_does_not_refresh_authentication_age(account_api):
    client, db, _, _ = account_api
    token = auth.create_session("anna", auth_method="google")
    sid = auth._session_payload(token)["sid"]
    old = time.time() - 600
    with db.conn() as connection:
        connection.execute("UPDATE user_sessions SET authenticated_at=?,last_seen_at=? WHERE id=?", (old, old, sid))
    assert client.get("/api/account/profile", headers=_headers(token)).status_code == 200
    row = db.session_get_active(sid, db.user_get_by_name("anna")["id"])
    assert row["authenticated_at"] == old and row["last_seen_at"] > old


def test_activity_metadata_write_failure_does_not_invalidate_valid_login(account_api, monkeypatch):
    _, db, _, _ = account_api
    token = auth.create_session("anna")
    sid = auth._session_payload(token)["sid"]
    with db.conn() as connection:
        connection.execute("UPDATE user_sessions SET last_seen_at=? WHERE id=?", (time.time() - 600, sid))
    monkeypatch.setattr(db, "session_touch", lambda _sid: (_ for _ in ()).throw(RuntimeError("busy")))
    assert auth.session_user(token) == "anna"


@pytest.mark.parametrize("change", [{"role": "admin"}, {"disabled": True}])
def test_admin_security_change_revokes_individual_sessions(account_api, change):
    client, db, user_id, _ = account_api
    token = client.headers["Authorization"].removeprefix("Bearer ")
    second = auth.create_session("anna")
    assert client.patch(f"/api/users/{user_id}", json=change,
                        headers=_headers(auth.create_session("admin"))).status_code == 200
    assert auth.session_user(token) is None and auth.session_user(second) is None
    assert db.session_list(user_id) == []


def test_sensitive_password_failures_are_rate_limited(account_api):
    client, _, _, _ = account_api
    from app.account_security import recent_auth_limiter
    for _ in range(recent_auth_limiter.max_fails):
        assert client.post("/api/account/password", json={"current_password": "wrong", "new_password": "new-password"}).status_code == 403
    assert client.post("/api/account/password", json={"current_password": "old-pass", "new_password": "new-password"}).status_code == 429


def test_self_delete_requires_confirmation_and_removes_sessions(account_api):
    client, db, user_id, _ = account_api
    token = client.headers["Authorization"].removeprefix("Bearer ")
    assert client.request("DELETE", "/api/account/profile", json={"current_password": "wrong"}).status_code == 403
    assert client.request("DELETE", "/api/account/profile", json={"current_password": "old-pass"}).status_code == 200
    assert db.user_get_by_name("anna") is None and auth.session_user(token) is None
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=?", (user_id,)).fetchone()[0] == 0


def test_self_delete_cannot_use_confirmation_superseded_by_admin(account_api, monkeypatch):
    from app.routes import api_account

    client, db, user_id, _ = account_api
    recent_auth = api_account.require_recent_auth

    def stale_confirmation(request, current_password):
        user = recent_auth(request, current_password)
        db.user_revoke_sessions_by_id(user_id)
        return user

    monkeypatch.setattr(api_account, "require_recent_auth", stale_confirmation)
    response = client.request("DELETE", "/api/account/profile", json={"current_password": "old-pass"})
    assert response.status_code == 409
    assert db.user_get_by_name("anna")["id"] == user_id


def test_self_delete_last_admin_is_blocked(account_api):
    client, db, _, _ = account_api
    token = auth.create_session("admin")
    response = client.request("DELETE", "/api/account/profile", json={"current_password": "old-pass"}, headers=_headers(token))
    assert response.status_code == 400 and "letzte aktive Administrator" in response.json()["detail"]
    assert auth.session_user(token) == "admin"


def test_self_delete_populated_household_is_blocked(account_api):
    client, db, user_id, _ = account_api
    account_id = accounts.view(db, user_id)["id"]
    with db.conn() as connection:
        connection.execute("INSERT INTO shopping_cart(name,added_at,account_id) VALUES('Milch',?,?)", (time.time(), account_id))
    result = client.request("DELETE", "/api/account/profile", json={"current_password": "old-pass"})
    assert result.status_code == 409 and "Haushalts mit Daten" in result.json()["detail"]
    assert db.user_get_by_name("anna")


def test_self_delete_owner_transfers_household_and_preserves_data(account_api):
    client, db, user_id, _ = account_api
    account_id = accounts.view(db, user_id)["id"]
    partner = db.user_get_by_name("bert")["id"]
    with db.conn() as connection:
        connection.execute("INSERT INTO account_members VALUES(?,?,?)", (account_id, partner, time.time()))
        connection.execute("INSERT INTO shopping_cart(name,added_at,account_id) VALUES('Milch',?,?)", (time.time(), account_id))
    assert client.request("DELETE", "/api/account/profile", json={"current_password": "old-pass"}).status_code == 200
    assert accounts.view(db, partner)["is_owner"] is True
    with db.conn() as connection:
        assert connection.execute("SELECT name FROM shopping_cart WHERE account_id=?", (account_id,)).fetchone()[0] == "Milch"


@pytest.mark.parametrize("path,method", [("/api/account/profile", "GET"), ("/api/account/sessions", "GET"),
                                       ("/api/account/password", "POST"), ("/api/account/profile", "DELETE")])
def test_guest_has_no_personal_account_management(account_api, path, method):
    client, _, _, _ = account_api
    response = client.request(method, path, headers=_headers(auth.create_guest_session()), json={"new_password": "long-password"})
    assert response.status_code == 403


def test_old_tokens_unknown_sid_and_expired_sessions_are_rejected(account_api):
    _, db, user_id, _ = account_api
    old = {"uid": user_id, "user": "anna", "ver": 0}
    assert auth.session_user(auth._serializer().dumps(old)) is None
    assert auth.session_user(auth._serializer().dumps({**old, "sid": "nonexistent"})) is None
    token = auth.create_session("anna")
    sid = auth._session_payload(token)["sid"]
    with db.conn() as connection:
        connection.execute("UPDATE user_sessions SET expires_at=? WHERE id=?", (time.time() - 1, sid))
    assert auth.session_user(token) is None


def test_session_id_cannot_be_reassigned_to_other_user(account_api):
    _, db, user_id, _ = account_api
    other = auth._session_payload(auth.create_session("bert"))
    forged = auth._serializer().dumps({"uid": user_id, "user": "anna", "ver": 0, "sid": other["sid"]})
    assert auth.session_user(forged) is None


@pytest.mark.parametrize("change", ["recreated-name", "unlinked", "revoked", "different-subject"])
def test_provider_session_handoff_cannot_survive_identity_change(account_api, change, password_hash):
    from app import oidc

    _, db, user_id, _ = account_api
    with db.conn() as connection:
        connection.execute("INSERT INTO oidc_identities(user_id,provider,subject,linked_at) VALUES(?,'google','verified-subject',?)",
                           (user_id, time.time()))
    identity = {"user_id": user_id, "version": 0, "provider": "google", "subject": "verified-subject"}
    if change == "recreated-name":
        assert db.user_delete(user_id)
        replacement = db.user_create("anna", password_hash)
        assert replacement != user_id
    elif change == "unlinked":
        oidc.disconnect(db, user_id, "google")
    elif change == "revoked":
        db.user_revoke_sessions_by_id(user_id)
    else:
        with db.conn() as connection:
            connection.execute("UPDATE oidc_identities SET subject='different-subject' WHERE user_id=?", (user_id,))
    with db.conn() as connection:
        count = connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
    with pytest.raises(ValueError, match="Anmeldeverknüpfung"):
        auth.create_session("anna", auth_method="google", expected_identity=identity)
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == count


def test_provider_session_handoff_accepts_only_bound_identity(account_api):
    _, db, user_id, _ = account_api
    with db.conn() as connection:
        connection.execute("INSERT INTO oidc_identities(user_id,provider,subject,linked_at) VALUES(?,'apple','verified-subject',?)",
                           (user_id, time.time()))
    identity = {"user_id": user_id, "version": 0, "provider": "apple", "subject": "verified-subject"}
    token = auth.create_session("anna", auth_method="apple", expected_identity=identity)
    assert auth.session_user(token) == "anna"
    with pytest.raises(ValueError):
        auth.create_session("anna", auth_method="google", expected_identity=identity)


def test_schema267_upgrade_preserves_users_and_creates_sessions(tmp_path, password_hash):
    path = tmp_path / "migration.db"
    original = Database(path)
    user_id = original.user_create("kept", password_hash, role="admin")
    account_id = accounts.view(original, user_id)["id"]
    with original.conn() as connection:
        for table in ("oidc_identities", "oidc_flows", "oidc_exchanges", "oidc_revocations"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("DROP TABLE user_sessions")
        connection.execute("DELETE FROM schema_migrations WHERE version>=267")
        before = tuple(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    upgraded = Database(path)
    with upgraded.conn() as connection:
        assert tuple(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()) == before
        assert connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=267").fetchone()[0] == 1
    assert accounts.view(upgraded, user_id)["id"] == account_id
    Database(path)  # Idempotent initialization.
