"""Registration and invitation ownership, expiry and single-use enforcement."""
from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer

from app import accounts, auth
from app.routes import api_auth
from app.security import LoginRateLimiter


@pytest.fixture
def account_client(client, test_db, monkeypatch):
    from app.main import app

    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("account-test-key-" * 4))
    monkeypatch.setattr(api_auth, "registration_limiter", LoginRateLimiter())
    test_db.user_create("operator", "fake-hash", role="admin")
    owner_id = test_db.user_create("owner", "fake-hash")
    client.headers["Authorization"] = "Bearer " + auth.create_session("owner")
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    yield client, owner_id
    app.dependency_overrides.clear()
    app.dependency_overrides.update(overrides)


def test_public_registration_creates_user_without_admin_privileges(account_client, test_db):
    client, _ = account_client
    client.headers.pop("Authorization")
    response = client.post("/api/auth/register", json={"username": "new-member", "password": "strong-test-password", "role": "admin"})
    assert response.status_code == 201
    assert response.json()["role"] == "user" and response.json()["is_admin"] is False
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    assert client.get("/api/account").json()["members"][0]["username"] == "new-member"
    assert client.get("/api/users").status_code == 403
    assert test_db.user_get_by_name("new-member")["role"] == "user"


def test_registration_cannot_bootstrap_an_admin(client, test_db, monkeypatch):
    response = client.post("/api/auth/register", json={"username": "outsider", "password": "strong-test-password"})
    assert response.status_code == 503
    assert test_db.user_get_by_name("outsider") is None


@pytest.mark.parametrize("password", ["short", "ä" * 40])
def test_invalid_registration_password_creates_no_account(account_client, test_db, password):
    client, _ = account_client
    assert client.post("/api/auth/register", json={"username": "new-member", "password": password}).status_code == 422
    assert test_db.user_get_by_name("new-member") is None


def test_invitation_join_is_single_use_and_keeps_personal_credentials(account_client, test_db):
    client, _ = account_client
    invitation = client.post("/api/account/invitations").json()
    owner_account = client.get("/api/account").json()["id"]
    response = client.post("/api/auth/register", json={"username": "partner", "password": "partner-test-password", "invitation_token": invitation["token"]})
    assert response.status_code == 201
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    account = client.get("/api/account").json()
    assert account["id"] == owner_account and not account["is_owner"]
    assert [item["username"] for item in account["members"]] == ["owner", "partner"]
    assert client.post("/api/account/invitations").status_code == 403
    assert auth.verify_password("partner-test-password", test_db.user_get_by_name("partner")["password_hash"])
    reused = client.post("/api/auth/register", json={"username": "third", "password": "third-test-password", "invitation_token": invitation["token"]})
    assert reused.status_code == 400 and test_db.user_get_by_name("third") is None
    client.headers["Authorization"] = "Bearer " + auth.create_session("owner")
    assert client.post("/api/account/invitations").status_code == 409
    with test_db.conn() as connection:
        row = connection.execute("SELECT token_hash FROM account_invitations WHERE id=?", (invitation["id"],)).fetchone()
        assert row["token_hash"] != invitation["token"]


@pytest.mark.parametrize("invalid", ["expired", "revoked", "replaced", "owner-disabled"])
def test_invalid_invitation_creates_no_user(account_client, test_db, invalid):
    client, owner_id = account_client
    invitation = client.post("/api/account/invitations").json()
    if invalid == "revoked":
        assert client.delete(f"/api/account/invitations/{invitation['id']}").status_code == 200
    elif invalid == "replaced":
        assert client.post("/api/account/invitations").status_code == 201
    else:
        with test_db.conn() as connection:
            if invalid == "expired":
                connection.execute("UPDATE account_invitations SET expires_at=? WHERE id=?", (time.time() - 1, invitation["id"]))
            else:
                connection.execute("UPDATE users SET disabled=1 WHERE id=?", (owner_id,))
    response = client.post("/api/auth/register", json={"username": "partner", "password": "partner-test-password", "invitation_token": invitation["token"]})
    assert response.status_code == 400
    assert test_db.user_get_by_name("partner") is None


def test_other_account_cannot_see_members_or_revoke_invitation(account_client, test_db):
    client, _ = account_client
    invitation = client.post("/api/account/invitations").json()
    test_db.user_create("other-person", "fake-hash")
    client.headers["Authorization"] = "Bearer " + auth.create_session("other-person")
    assert [member["username"] for member in client.get("/api/account").json()["members"]] == ["other-person"]
    assert client.delete(f"/api/account/invitations/{invitation['id']}").status_code == 404


def test_existing_person_can_accept_invitation(account_client, test_db):
    client, _ = account_client
    invitation = client.post("/api/account/invitations").json()
    test_db.user_create("existing-person", "fake-hash")
    client.headers["Authorization"] = "Bearer " + auth.create_session("existing-person")
    response = client.post("/api/account/invitations/accept", json={"token": invitation["token"]})
    assert response.status_code == 200
    assert [member["username"] for member in client.get("/api/account").json()["members"]] == ["owner", "existing-person"]


def test_parallel_invitation_acceptance_admits_exactly_one_person(account_client, test_db):
    _, owner_id = account_client
    invitation = accounts.invite(test_db, owner_id)

    def join(username):
        try:
            accounts.register(test_db, username, "fake-hash", invitation_token=invitation["token"])
            return "joined"
        except HTTPException:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(join, ["first-person", "second-person"])) == ["joined", "rejected"]
    assert len(accounts.view(test_db, owner_id)["members"]) == 2


def test_browser_guest_and_registration_use_same_origin_and_private_cookies(account_client):
    client, _ = account_client
    client.headers.pop("Authorization")
    assert client.post("/login/guest", data={}).status_code == 403
    response = client.post("/login/guest", data={}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/session").json()["role"] == "guest"
    signup = client.post("/register", data={"username": "browser-member", "password": "strong-test-password", "password_confirm": "strong-test-password"},
                         headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert signup.status_code == 303 and signup.headers["location"] == "/account"
    assert client.get("/api/session").json()["role"] == "user"


def test_browser_invitation_link_preserves_token_for_existing_login(account_client):
    client, _ = account_client
    invitation = client.post("/api/account/invitations").json()
    page = client.get(invitation["invite_path"])
    assert page.status_code == 200 and page.headers["referrer-policy"] == "strict-origin"
    assert 'name="invitation_token" value="' + invitation["token"] + '"' in page.text
    assert "/login?next=%2Faccount%3Finvite%3D" + invitation["token"] in page.text
    assert "{LOGIN}" not in page.text


def test_browser_registration_does_not_echo_passwords_or_unescaped_input(account_client, test_db):
    client, _ = account_client
    client.headers.pop("Authorization")
    response = client.post("/register", data={"username": '<img src=x onerror="bad">', "password": "sensitive-test-password",
                                                "password_confirm": "different-test-password"},
                           headers={"Origin": "http://testserver"})
    assert response.status_code == 422
    assert "sensitive-test-password" not in response.text and "different-test-password" not in response.text
    assert '<img src=x onerror="bad">' not in response.text
    assert test_db.user_get_by_name('<img src=x onerror="bad">') is None


def test_account_schema_upgrade_preserves_data_and_does_not_repeat_backups(tmp_path):
    from app.db import Database

    path = tmp_path / "existing-household.db"
    previous = Database(path)
    user_id = previous.user_create("existing-owner", "preserved-password-hash", role="admin")
    recipe_id = previous.recipe_upsert(url="https://example.invalid/old", name="Bestandsrezept", type="Hauptgericht",
                                      category="Haushalt", folder_path=str(tmp_path / "archive"), description="Erhalten",
                                      thumb_filename=None, video_filename=None, source_added_at=123)
    with previous.conn() as connection:
        for table in ("account_invitations", "account_members", "user_accounts"):
            connection.execute("DROP TABLE " + table)
        # Der simulierte Altstand muss auch die späteren Schema-266-Trigger entfernen.
        for table in ("shopping_cart", "shopping_recurring"):
            for operation in ("insert", "update"):
                connection.execute(f"DROP TRIGGER {table}_finite_amount_{operation}")
        connection.execute("DELETE FROM schema_migrations WHERE version>=231")
        connection.execute("UPDATE recipes SET name='Eigener__Bestandsname_' WHERE id=?", (recipe_id,))
    current = Database(path)
    assert current.user_get_by_name("existing-owner")["password_hash"] == "preserved-password-hash"
    assert current.recipe_get(recipe_id)["name"] == "Eigener__Bestandsname_"
    assert accounts.view(current, user_id)["members"][0]["username"] == "existing-owner"
    invitation = accounts.invite(current, user_id)
    assert invitation["token"] and invitation["id"] > 0
    with current.conn() as connection:
        from app.db import CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    backups = set((tmp_path / "backups").glob("pre-migration-*.db"))
    assert len(backups) == 1
    Database(path)
    assert set((tmp_path / "backups").glob("pre-migration-*.db")) == backups
