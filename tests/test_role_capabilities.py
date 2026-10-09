"""Four persisted roles through real sessions and server-side authorization."""
import sqlite3
import time

import pytest
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
from starlette.requests import Request

from app import accounts, auth
from app.db import Database
from app.tenancy import scope_for_request


ROLES = ("guest", "user", "full_user", "admin")


@pytest.fixture(scope="module")
def role_password():
    return auth.hash_password("role-test-password")


@pytest.fixture
def roles_api(client, test_db, monkeypatch, role_password):
    from app.main import app
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("role-test-secret-" * 4))
    for dependency in (auth.require_auth, auth.require_admin, auth.require_import):
        app.dependency_overrides.pop(dependency, None)
    users = {role: test_db.user_create("role-" + role, role_password, role=role) for role in ROLES}
    return client, test_db, users


def _headers(role):
    return {"Authorization": "Bearer " + auth.create_session("role-" + role)}


def _request(token, method="POST", path="/api/pending/import-url"):
    return Request({"type": "http", "method": method, "path": path,
                    "headers": [(b"authorization", ("Bearer " + token).encode())]})


@pytest.mark.parametrize("role", ROLES)
def test_role_login_session_and_server_gates(roles_api, role):
    client, _, users = roles_api
    response = client.post("/api/auth/login", json={"username": "role-" + role, "password": "role-test-password"})
    assert response.status_code == 200, response.text
    payload = response.json()
    expected = {"role": role, "is_admin": role == "admin", "full_access": role == "admin",
                "can_import": role in {"full_user", "admin"}, "read_only": role == "guest"}
    assert {key: payload[key] for key in expected} == expected
    assert payload["id"] == users[role] and payload["username"] == "role-" + role
    headers = {"Authorization": "Bearer " + payload["token"]}
    session = client.get("/api/auth/session", headers=headers)
    assert session.status_code == 200
    assert {key: session.json()[key] for key in expected} == expected
    assert client.get("/api/users", headers=headers).status_code == (200 if role == "admin" else 403)
    assert client.get("/api/account/imports", headers=headers).status_code == (200 if expected["can_import"] else 403)
    assert client.post("/api/auth/logout", headers=headers).json()["revoked"] is True
    assert client.get("/api/auth/session", headers=headers).status_code == 401


@pytest.mark.parametrize("role", ROLES)
def test_import_guard_uses_persisted_role_not_token_claims(roles_api, role):
    _, _, _ = roles_api
    token = auth.create_session("role-" + role)
    claims = auth._session_payload(token)
    token = auth._serializer().dumps({**claims, "role": "admin", "is_admin": True, "can_import": True})
    request = _request(token)
    if role in {"full_user", "admin"}:
        result = auth._require_import(request)
        assert result["role"] == role and result["can_import"] is True
        assert result["full_access"] is (role == "admin")
    else:
        with pytest.raises(HTTPException) as error:
            auth._require_import(request)
        assert error.value.status_code == 403


@pytest.mark.parametrize("role", ROLES)
def test_admin_can_create_all_four_roles_and_nonadmins_cannot(roles_api, role):
    client, db, _ = roles_api
    data = {"username": "created-account", "password": "created-password", "role": role}
    for caller in ("guest", "user", "full_user"):
        assert client.post("/api/users", json=data, headers=_headers(caller)).status_code == 403
    response = client.post("/api/users", json=data, headers=_headers("admin"))
    assert response.status_code == 200, response.text
    assert db.user_get_by_name(data["username"])["role"] == role


@pytest.mark.parametrize("next_role", ["guest", "full_user", "admin"])
def test_role_change_revokes_all_sessions_and_last_admin_stays_protected(roles_api, next_role):
    client, db, users = roles_api
    tokens = [auth.create_session("role-user"), auth.create_session("role-user")]
    before = db.user_get_by_name("role-user")["session_version"]
    response = client.patch(f"/api/users/{users['user']}", json={"role": next_role}, headers=_headers("admin"))
    assert response.status_code == 200
    assert db.user_get_by_name("role-user")["session_version"] == before + 1
    assert all(auth.session_user(token) is None for token in tokens)
    fresh = client.get("/api/auth/session", headers=_headers("user"))
    assert fresh.status_code == 200 and fresh.json()["role"] == next_role
    if next_role == "admin":
        db.user_set_role(users["user"], "user")
    for demotion in ("guest", "user", "full_user"):
        response = client.patch(f"/api/users/{users['admin']}", json={"role": demotion}, headers=_headers("admin"))
        assert response.status_code == 400
        assert db.user_get_by_name("role-admin")["role"] == "admin"


def test_restart_preserves_roles_and_never_promotes_existing_users(roles_api):
    _, db, _ = roles_api
    before = {u["username"]: (u["role"], db.user_get_by_name(u["username"])["session_version"]) for u in db.user_list()}
    reopened = Database(db.path)
    assert {u["username"]: (u["role"], reopened.user_get_by_name(u["username"])["session_version"]) for u in reopened.user_list()} == before


@pytest.mark.parametrize("before,after", [(before, after) for before in ROLES for after in ROLES if before != after])
def test_every_role_transition_invalidates_existing_sessions(roles_api, role_password, before, after):
    client, db, _ = roles_api
    uid = db.user_create("changing-role", role_password, role=before)
    token = auth.create_session("changing-role")
    response = client.patch(f"/api/users/{uid}", json={"role": after}, headers=_headers("admin"))
    assert response.status_code == 200
    assert auth.session_user(token) is None
    fresh = client.get("/api/auth/session", headers={"Authorization": "Bearer " + auth.create_session("changing-role")})
    assert fresh.status_code == 200 and fresh.json()["role"] == after
    assert fresh.json()["can_import"] is (after in {"full_user", "admin"})


@pytest.mark.parametrize("role", ["guest", "full_user"])
@pytest.mark.parametrize("provider", ["apple", "google"])
def test_returning_provider_login_preserves_admin_assigned_role(roles_api, role, provider):
    from app import oidc
    client, db, users = roles_api
    claims = {"sub": "role-subject", "role": "admin", "can_import": True}
    with db.conn() as connection:
        connection.execute("INSERT INTO oidc_identities(user_id,provider,subject,linked_at) VALUES(?,?,?,?)",
                           (users[role], provider, claims["sub"], time.time()))
    identity = oidc._bind_identity(db, provider, {"intent": "login", "invitation": ""}, claims, "")
    token = auth.create_session(identity["username"], auth_method=provider, expected_identity=identity)
    session = client.get("/api/auth/session", headers={"Authorization": "Bearer " + token})
    assert session.status_code == 200 and session.json()["role"] == role
    assert session.json()["is_admin"] is False
    assert session.json()["can_import"] is (role == "full_user")


def test_named_guest_keeps_identity_security_and_global_read_only_scope(roles_api):
    client, db, users = roles_api
    guest_id = users["guest"]
    account_id = accounts.view(db, guest_id)["id"]
    ids = []
    for name, owner in (("Global", None), ("Private", account_id)):
        rid = db.recipe_upsert(url="https://fixture.invalid/" + name, name=name, type="Test", category="Test",
                               folder_path="/role-test/" + name, description=name, thumb_filename=None,
                               video_filename=None, source_added_at=time.time())
        db.recipe_set_extraction_result(rid, ingredients=[{"name": "Wasser"}], status="ok")
        with db.conn() as connection:
            connection.execute("UPDATE recipes SET owner_account_id=? WHERE id=?", (owner, rid))
        ids.append(rid)
    token = auth.create_session("role-guest")
    request = _request(token, "GET", "/api/recipes")
    assert auth.request_is_guest(request) is False
    assert auth.request_is_read_only(request) is True
    scope = scope_for_request(request)
    assert scope.is_guest and scope.account_id == -1 and scope.user_id == guest_id
    headers = {"Authorization": "Bearer " + token}
    profile = client.get("/api/account/profile", headers=headers)
    assert profile.status_code == 200 and profile.json()["id"] == guest_id
    account = client.get("/api/account", headers=headers).json()
    assert account["is_guest"] is False and account["read_only"] is True
    assert account["data_scope"] == "global_read_only" and not account["members"]
    assert client.get(f"/api/recipes/{ids[0]}", headers=headers).status_code == 200
    assert client.get(f"/api/recipes/{ids[1]}", headers=headers).status_code == 404
    assert client.get("/api/recipes?library=mine", headers=headers).json()["items"] == []
    for path, body in (("/api/account/invitations", {}),
                       ("/api/account/invitations/accept", {"token": "x" * 32}),
                       (f"/api/recipes/{ids[0]}/duplicate", {"new_name": "Forbidden"})):
        assert client.post(path, json=body, headers=headers).status_code == 403
    other_session = auth.create_session("role-guest")
    other_id = auth._session_payload(other_session)["sid"]
    assert client.delete(f"/api/account/sessions/{other_id}", headers=headers).status_code == 200
    assert auth.session_user(other_session) is None
    foreign = auth.create_session("role-user")
    foreign_id = auth._session_payload(foreign)["sid"]
    assert client.delete(f"/api/account/sessions/{foreign_id}", headers=headers).status_code == 404
    assert auth.session_user(foreign) == "role-user"
    changed = client.post("/api/account/password", headers=headers,
                          json={"current_password": "role-test-password", "new_password": "updated-guest-password"})
    assert changed.status_code == 200 and changed.json()["reauthenticate"] is True
    assert auth.session_user(token) is None


def test_anonymous_guest_cannot_use_named_guest_security_exceptions(roles_api):
    client, _, _ = roles_api
    headers = {"Authorization": "Bearer " + auth.create_guest_session()}
    assert client.get("/api/auth/session", headers=headers).json()["can_import"] is False
    assert client.post("/api/account/password", headers=headers,
                       json={"current_password": "role-test-password", "new_password": "new-guest-password"}).status_code == 403
    assert client.delete("/api/account/sessions/not-theirs", headers=headers).status_code == 403


def test_unknown_persisted_role_has_consistent_guest_payload_and_write_guard(roles_api):
    client, db, users = roles_api
    with db.conn() as connection:
        connection.execute("UPDATE users SET role='unknown-role' WHERE id=?", (users["user"],))
    response = client.post("/api/auth/login", json={"username": "role-user", "password": "role-test-password"})
    assert response.status_code == 200
    payload = response.json()
    headers = {"Authorization": "Bearer " + payload["token"]}
    for contract in (payload, client.get("/api/auth/session", headers=headers).json(),
                     client.get("/api/session", headers=headers).json()):
        assert contract["role"] == "guest" and contract["read_only"] is True
        assert contract["is_admin"] is False and contract["full_access"] is False
        assert contract["can_import"] is False
    assert client.post("/api/cart/add", headers=headers, json={"name": "Forbidden"}).status_code == 403
    assert client.get("/api/account/imports", headers=headers).status_code == 403


def test_restart_normalizes_unknown_role_to_guest_and_revokes_sessions(roles_api):
    _, db, users = roles_api
    token = auth.create_session("role-user")
    before = db.user_get_by_name("role-user")["session_version"]
    with db.conn() as connection:
        connection.execute("UPDATE users SET role='unknown-role' WHERE id=?", (users["user"],))
    reopened = Database(db.path)
    migrated = reopened.user_get_by_name("role-user")
    assert migrated["role"] == "guest" and migrated["session_version"] == before + 1
    assert auth.session_user(token) is None
    again = Database(db.path).user_get_by_name("role-user")
    assert again["role"] == "guest" and again["session_version"] == before + 1


def test_legacy_nullable_role_migrates_to_guest_once(tmp_path, role_password):
    path = tmp_path / "legacy-null-role.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL, "
                           "role TEXT, disabled INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 0, "
                           "created_at REAL NOT NULL, last_login_at REAL)")
        connection.execute("INSERT INTO users(username,password_hash,role,session_version,created_at) VALUES(?,?,NULL,7,?)",
                           ("legacy-null", role_password, time.time()))
    for _ in range(2):
        user = Database(path).user_get_by_name("legacy-null")
        assert user["role"] == "guest" and user["session_version"] == 8


def test_own_imports_expose_only_file_flag_and_keep_household_filter(roles_api):
    client, db, users = roles_api
    own = accounts.view(db, users["full_user"])["id"]
    foreign = accounts.view(db, users["user"])["id"]
    for name, owner, video, frame in (("empty", own, None, None),
                                     ("video", own, "/private-internal/video.mp4", None),
                                     ("frame", own, None, "/private-internal/frame.jpg"),
                                     ("foreign", foreign, "/private-internal/foreign.mp4", None),
                                     ("global", None, "/private-internal/global.mp4", None)):
        db.pending_add("https://fixture.invalid/" + name, "recipe", owner_account_id=owner,
                       video_path=video, frame_path=frame, ai_suggestion={"name": name})
    response = client.get("/api/account/imports", headers=_headers("full_user"))
    assert response.status_code == 200
    assert {item["name"]: item["has_file"] for item in response.json()["items"]} == {"empty": False, "video": True, "frame": True}
    assert all(type(item["has_file"]) is bool for item in response.json()["items"])
    assert "/private-internal/" not in response.text
    assert "video_path" not in response.text and "frame_path" not in response.text
    assert "foreign" not in response.text and "global" not in response.text
