"""Personal cooking state must never pass to a recreated login or another household."""
import pytest

from app import accounts
from app.db import Database
from tests.test_tenants import _recipe, households  # noqa: F401


def _replace_anna(client, db, users, login):
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    login("operator")
    assert client.delete(f"/api/users/{users['anna'][0]}").status_code == 200
    uid = accounts.register(db, "anna", "unused-password-hash")
    assert uid != users["anna"][0]
    aid = accounts.view(db, uid)["id"]
    assert aid != users["anna"][1]
    login("anna")
    return aid


def test_recreated_login_cannot_read_previous_cooking_progress(households):
    client, db, users, login = households
    rid = _recipe(db, "PersonalProgress", "https://recipes.example/personal-progress")
    login("anna")
    saved = client.put(f"/api/recipes/{rid}/cooking-progress",
                       json={"completed_steps": [0], "active_step": 0, "servings": 7})
    assert saved.status_code == 200, saved.text
    _replace_anna(client, db, users, login)
    current = client.get(f"/api/recipes/{rid}/cooking-progress")
    assert current.status_code == 200 and current.json()["exists"] is False
    fresh = client.put(f"/api/recipes/{rid}/cooking-progress",
                       json={"completed_steps": [], "active_step": 0, "servings": 2})
    assert fresh.status_code == 200
    assert fresh.json()["started_at"] > saved.json()["started_at"]


def test_recreated_login_cannot_replay_previous_households_completion(households):
    client, db, users, login = households
    rid = _recipe(db, "PersonalCompletion", "https://recipes.example/personal-completion")
    login("anna")
    headers = {"Idempotency-Key": "completion-after-retry"}
    old = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 7}, headers=headers)
    assert old.status_code == 200
    aid = _replace_anna(client, db, users, login)
    new = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 7}, headers=headers)
    assert new.status_code == 200, new.text
    assert new.json()["entry"]["account_id"] == aid
    assert new.json()["entry"]["id"] != old.json()["entry"]["id"]
    replay = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 7}, headers=headers)
    assert replay.json()["entry"] == new.json()["entry"]
    assert client.get(f"/api/recipes/{rid}/cook-history").json()["summary"]["count"] == 1
    login("bert")
    assert client.get(f"/api/recipes/{rid}/cook-history").json()["items"] == [old.json()["entry"]]


def test_completion_retry_cannot_return_a_foreign_household_row(households):
    client, db, users, login = households
    rid = _recipe(db, "ForeignCompletion", "https://recipes.example/foreign-completion")
    login("anna")
    headers = {"Idempotency-Key": "legacy-stale-account"}
    old = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 3}, headers=headers)
    assert old.status_code == 200
    with db.conn() as c:
        c.execute("UPDATE recipe_cook_history SET account_id=? WHERE id=?",
                  (users["bert"][1], old.json()["entry"]["id"]))
    new = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 4}, headers=headers)
    assert new.status_code == 200, new.text
    assert new.json()["entry"]["account_id"] == users["anna"][1]
    assert new.json()["entry"]["id"] != old.json()["entry"]["id"]
    login("bert")
    assert client.get(f"/api/recipes/{rid}/cook-history").json()["summary"]["count"] == 1


def test_cooking_progress_survives_legitimate_household_join(households):
    client, db, users, login = households
    rid = _recipe(db, "MoveProgress", "https://recipes.example/move-progress")
    login("bert")
    saved = client.put(f"/api/recipes/{rid}/cooking-progress",
                       json={"completed_steps": [0], "active_step": 0, "servings": 5})
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    assert client.get(f"/api/recipes/{rid}/cooking-progress").json() == saved.json()


@pytest.mark.parametrize("data", ["progress", "completion"])
def test_deleted_users_personal_retry_state_is_removed(households, data):
    client, db, users, login = households
    rid = _recipe(db, "DeletePersonal", "https://recipes.example/delete-personal")
    login("anna")
    if data == "progress":
        response = client.put(f"/api/recipes/{rid}/cooking-progress", json={"completed_steps": [0]})
        table = "recipe_cooking_progress"
    else:
        response = client.post(f"/api/recipes/{rid}/cooking-complete", json={}, headers={"Idempotency-Key": "delete-me"})
        table = "recipe_cooking_completion_requests"
    assert response.status_code == 200
    _replace_anna(client, db, users, login)
    with db.conn() as c:
        assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE username='anna'").fetchone()[0] == 0


def test_v262_migration_preserves_rows_but_never_binds_recreated_or_missing_names(households):
    _, db, users, _ = households
    rid = _recipe(db, "LegacyProgress", "https://recipes.example/legacy-progress")
    for username in ("anna", "bert", "missing"):
        db.recipe_cooking_complete(rid, username, servings=7, idempotency_key="legacy-retry",
                                   account_id=users.get(username, (0, 0))[1])
        db.recipe_cooking_progress_set(rid, username, completed_steps=[0], active_step=0, servings=7)
    with db.conn() as c:
        c.execute("UPDATE users SET created_at=50 WHERE username='anna'")
        c.execute("UPDATE users SET created_at=150 WHERE username='bert'")
        previous = {}
        for table, timestamp in (("recipe_cooking_progress", "started_at"),
                                 ("recipe_cooking_completion_requests", "created_at")):
            c.execute(f"UPDATE {table} SET {timestamp}=100")
            c.execute(f"DROP INDEX IF EXISTS idx_{table}_user")
            c.execute(f"ALTER TABLE {table} DROP COLUMN user_id")
            previous[table] = [dict(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY username")]
        previous_history = [dict(row) for row in c.execute("SELECT * FROM recipe_cook_history ORDER BY id")]
        c.execute("DELETE FROM schema_migrations WHERE version=263")
    migrated = Database(db.path)  # Migration connections intentionally use tuple rows.
    assert migrated.recipe_cooking_progress_get(rid, "anna")["completed_steps"] == [0]
    assert migrated.recipe_cooking_progress_get(rid, "bert") is None
    accounts.register(migrated, "missing", "unused-password-hash")
    assert migrated.recipe_cooking_progress_get(rid, "missing") is None
    for table in previous:
        with migrated.conn() as c:
            rows = [dict(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY username")]
        assert [row.pop("user_id") for row in rows] == [users["anna"][0], None, None]
        assert rows == previous[table]
    with migrated.conn() as c:
        assert [dict(row) for row in c.execute("SELECT * FROM recipe_cook_history ORDER BY id")] == previous_history
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        from app.db import CURRENT_SCHEMA_VERSION
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
    old_id = previous_history[1]["id"]
    fresh = migrated.recipe_cooking_complete(rid, "bert", servings=4, idempotency_key="legacy-retry",
                                             account_id=users["bert"][1])
    assert fresh["id"] != old_id
    Database(db.path)  # Reopening cannot silently assign the ambiguous rows.
    assert migrated.recipe_cooking_progress_get(rid, "missing") is None


def test_completion_retry_survives_legitimate_household_join(households):
    client, db, users, login = households
    rid = _recipe(db, "MoveCompletion", "https://recipes.example/move-completion")
    login("bert")
    headers = {"Idempotency-Key": "join-retry"}
    first = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 3}, headers=headers)
    invitation = accounts.invite(db, users["anna"][0])
    accounts.accept(db, users["bert"][0], invitation["token"])
    replay = client.post(f"/api/recipes/{rid}/cooking-complete", json={"servings": 3}, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["entry"]["id"] == first.json()["entry"]["id"]
    assert replay.json()["entry"]["account_id"] == users["anna"][1]
    assert replay.json()["summary"]["count"] == 1
