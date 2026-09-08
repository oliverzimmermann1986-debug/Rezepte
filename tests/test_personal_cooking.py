"""Real-session privacy, optimistic concurrency and atomic review contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import auth
from app.db import Database
from app.recipes import personal_cooking as service
from tests.conftest import _create_recipe


class _Config:
    def get(self, *parts, default=None):
        return {("web",): {"auth_disabled": False}, ("web", "secret_key"): "m" * 48}.get(parts, default)


@pytest.fixture
def identities(client, test_db, monkeypatch):
    """Do not bypass auth or admin dependencies in this feature's API tests."""
    from app.main import app

    monkeypatch.setattr(auth, "get_config", lambda: _Config())
    for username, role in (("anna", "user"), ("ben", "user"), ("owner", "admin")):
        test_db.user_create(username, "unused-password-hash", role=role)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    return {
        **{
            name: {"Authorization": f"Bearer {auth.create_session(name)}"}
            for name in ("anna", "ben", "owner")
        },
        "guest": {"Authorization": f"Bearer {auth.create_guest_session()}"},
    }


@pytest.fixture
def recipe(test_db, tmp_path):
    row = _create_recipe(
        test_db,
        name="Kochgedächtnis",
        folder_path=str(tmp_path / "recipe"),
        url="https://example.test/cooking-memory",
        description="Originalquelle: 200 g Tomaten behutsam köcheln lassen.",
    )
    recipe_id = row["id"]
    test_db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [
            {
                "name": "Tomaten",
                "canonical_name": "tomate",
                "amount": 200,
                "unit": "g",
                "raw": "200 g Tomaten",
            }
        ],
    )
    test_db.recipe_steps_set(
        recipe_id,
        [
            {"instruction": "Tomaten vorsichtig schneiden."},
            {"instruction": "Tomaten köcheln lassen.", "timer_seconds": 300},
        ],
    )
    test_db.recipe_set_servings(recipe_id, 2)
    return recipe_id


def _note(**changes):
    return {
        "client_entry_id": "note-1",
        "note": "Sehr gut gelungen.",
        "adjustments": "Weniger Salz.",
        "next_time": "Deckel früher abnehmen.",
        "servings": 2,
        **changes,
    }


def _proposal(client, recipe, headers, **changes):
    current = client.get(f"/api/recipes/{recipe}/import-review", headers=headers)
    assert current.status_code == 200, current.text
    return {
        "client_request_id": "correction-1",
        "expected_revision": current.json()["revision"],
        "ingredients": [{"name": "Tomaten", "amount": 300, "unit": "g", "raw": None}],
        "steps": [
            {"instruction": "Tomaten fein würfeln."},
            {"instruction": "Tomaten zugedeckt köcheln lassen.", "timer_seconds": 600},
        ],
        "servings": 3,
        "reason": "Menge und Kochzeit mit Original abgeglichen.",
        **changes,
    }


def test_memory_is_personal_not_even_admin_sees_others(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/cooking-memory"
    saved = client.post(path, headers=identities["anna"], json=_note())
    assert saved.status_code == 200, saved.text
    assert saved.json()["entry"]["step_is_current"] is True
    own = client.get(path, headers=identities["anna"])
    assert own.headers["cache-control"] == "private, no-store"
    assert own.json()["total"] == 1
    assert own.json()["items"][0]["adjustments"] == "Weniger Salz."
    for other in ("ben", "owner"):
        assert client.get(path, headers=identities[other]).json() == {"items": [], "total": 0}
        denied = client.delete(f"{path}/{saved.json()['entry']['id']}", headers=identities[other])
        assert denied.status_code == 404
    detail = client.get(f"/api/recipes/{recipe}", headers=identities["owner"]).text
    assert "Weniger Salz" not in detail
    assert "Sehr gut gelungen" not in detail
    assert test_db.recipe_cook_history(recipe) == []


@pytest.mark.parametrize("role,expected", [(None, 401), ("guest", 403)])
def test_personal_features_reject_anonymous_and_guest(client, recipe, identities, role, expected):
    headers = identities[role] if role else {}
    for suffix in ("cooking-memory", "import-review"):
        assert client.get(f"/api/recipes/{recipe}/{suffix}", headers=headers).status_code == expected
    assert (
        client.post(f"/api/recipes/{recipe}/cooking-memory", headers=headers, json=_note()).status_code
        == expected
    )


def test_memory_idempotency_deletion_and_step_snapshot(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/cooking-memory"
    headers = identities["anna"]
    payload = _note(step_number=2, step_instruction="Tomaten köcheln lassen.")
    first = client.post(path, headers=headers, json=payload)
    entry_id = first.json()["entry"]["id"]
    assert client.post(path, headers=headers, json=payload).json()["entry"]["id"] == entry_id
    assert client.post(path, headers=headers, json={**payload, "note": "Anders"}).status_code == 409
    test_db.recipe_steps_set(recipe, [{"instruction": "Eine ganz neue Anweisung."}])
    existing = client.get(path, headers=headers).json()["items"][0]
    assert existing["step_is_current"] is False
    assert existing["step_instruction"] == "Tomaten köcheln lassen."
    assert (
        client.post(path, headers=headers, json={**payload, "client_entry_id": "new-step"}).status_code == 409
    )
    assert client.delete(f"{path}/{entry_id}", headers=headers).status_code == 200
    assert client.delete(f"{path}/{entry_id}", headers=headers).status_code == 200
    assert client.get(path, headers=headers).json()["total"] == 0
    assert client.post(path, headers=headers, json=payload).status_code == 410
    with test_db.conn() as c:
        deleted = dict(c.execute("SELECT * FROM recipe_cooking_memory WHERE id=?", (entry_id,)).fetchone())
    assert deleted["note"] == deleted["adjustments"] == deleted["next_time"] == ""
    assert deleted["step_instruction"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"note": " ", "adjustments": "", "next_time": ""},
        {"note": "x" * 2001},
        {"username": "owner"},
        {"step_number": 1},
        {"step_instruction": "text"},
        {"step_number": 0, "step_instruction": "text"},
        {"servings": 0},
        {"client_entry_id": "bad/id"},
    ],
)
def test_memory_rejects_malformed_content(client, recipe, identities, changes):
    response = client.post(
        f"/api/recipes/{recipe}/cooking-memory", headers=identities["anna"], json=_note(**changes)
    )
    assert response.status_code == 422


def test_member_proposal_requires_admin_and_keeps_original_audit(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/import-review"
    payload = _proposal(client, recipe, identities["anna"])
    original = test_db.recipe_snapshot(recipe)
    submitted = client.post(path, headers=identities["anna"], json=payload)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "pending"
    assert body["review"]["can_apply"] is False
    assert body["correction"]["username"] == "anna"
    assert test_db.recipe_snapshot(recipe) == original
    correction_id = body["correction"]["id"]
    assert (
        client.post(path, headers=identities["anna"], json=payload).json()["correction"]["id"]
        == correction_id
    )
    assert client.get(path, headers=identities["ben"]).json()["corrections"] == []
    assert client.post(f"{path}/{correction_id}/apply", headers=identities["anna"]).status_code == 403
    review = client.get(path, headers=identities["owner"]).json()
    assert review["can_apply"] is True
    assert review["corrections"][0]["before"]["ingredients"][0]["amount"] == 200
    assert review["corrections"][0]["proposed"]["ingredients"][0]["amount"] == 300
    result = client.post(f"{path}/{correction_id}/apply", headers=identities["owner"])
    assert result.status_code == 200, result.text
    applied = result.json()["correction"]
    assert applied["status"] == "applied"
    assert applied["applied_by"] == "owner"
    assert applied["username"] == "anna"
    assert applied["before"]["source"]["description"] == original["recipe"]["description"]
    assert test_db.recipe_get(recipe)["description"] == original["recipe"]["description"]
    assert test_db.recipe_ingredients_get(recipe)[0]["amount"] == 300
    version = test_db.recipe_version_get(applied["version_id"])
    assert version["snapshot"]["ingredients"][0]["amount"] == 200
    assert version["snapshot"]["steps"] == original["steps"]
    assert len(test_db.recipe_versions_list(recipe)) == 1
    assert client.post(f"{path}/{correction_id}/apply", headers=identities["owner"]).status_code == 200
    assert len(test_db.recipe_versions_list(recipe)) == 1


def test_admin_direct_apply_invalidates_safety_and_clears_progress_only_for_steps(
    client, test_db, recipe, identities
):
    path = f"/api/recipes/{recipe}/import-review"
    test_db.recipe_set_verified(recipe, True, "owner")
    test_db.recipe_tags_set(recipe, ["laktosefrei", "Familie"])
    test_db.recipe_auto_tags_set(recipe, ["vegan", "italienisch"])
    test_db.recipe_cooking_progress_set(recipe, "anna", completed_steps=[0], active_step=1, servings=2)
    with test_db.conn() as c:
        c.execute(
            "UPDATE recipes SET calories_per_serving=123, nutrition_claim_owner='old', "
            "extraction_claim_owner='old' WHERE id=?",
            (recipe,),
        )
    payload = _proposal(
        client, recipe, identities["owner"], ingredients=[{"name": "Vollmilch", "amount": 300, "unit": "ml"}]
    )
    response = client.post(path, headers=identities["owner"], json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    current = test_db.recipe_get(recipe)
    assert current["user_verified"] == 0
    assert current["nutrition_claim_owner"] is None
    assert current["extraction_claim_owner"] is None
    assert current["calories_per_serving"] is None
    assert test_db.recipe_cooking_progress_get(recipe, "anna") is None
    names = {tag["name"] for tag in test_db.recipe_tags_get(recipe)}
    assert {"Familie", "italienisch"} <= names
    assert not {"laktosefrei", "vegan"} & names
    assert client.post(path, headers=identities["owner"], json=payload).status_code == 200
    assert len(test_db.recipe_versions_list(recipe)) == 1
    assert (
        client.post(path, headers=identities["owner"], json={**payload, "reason": "changed"}).status_code
        == 409
    )


def test_revision_checked_on_submit_and_approval_without_partial_writes(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/import-review"
    payload = _proposal(client, recipe, identities["anna"])
    submitted = client.post(path, headers=identities["anna"], json=payload).json()
    correction_id = submitted["correction"]["id"]
    test_db.recipe_set_servings(recipe, 4)
    current = test_db.recipe_snapshot(recipe)
    assert client.post(path, headers=identities["owner"], json=payload).status_code == 409
    assert client.post(f"{path}/{correction_id}/apply", headers=identities["owner"]).status_code == 409
    assert test_db.recipe_snapshot(recipe) == current
    assert test_db.recipe_versions_list(recipe) == []
    assert client.get(path, headers=identities["anna"]).json()["corrections"][0]["status"] == "pending"


@pytest.mark.parametrize(
    "changes",
    [
        {"ingredients": []},
        {"steps": []},
        {"reason": " "},
        {"expected_revision": "old"},
        {"ingredients": [{"name": " "}]},
        {"ingredients": [{"name": "Milch", "amount": -1}]},
        {"ingredients": [{"name": "Milch", "amount": "NaN"}]},
        {"steps": [{"instruction": " "}]},
        {"steps": [{"instruction": "Kochen", "timer_seconds": 86401}]},
        {"can_apply": True},
    ],
)
def test_import_review_rejects_bad_data_and_privilege_fields(client, recipe, identities, changes):
    payload = _proposal(client, recipe, identities["anna"], **changes)
    assert (
        client.post(
            f"/api/recipes/{recipe}/import-review", headers=identities["anna"], json=payload
        ).status_code
        == 422
    )


def test_ingredients_only_correction_preserves_active_steps(client, test_db, recipe, identities):
    test_db.recipe_cooking_progress_set(recipe, "anna", completed_steps=[0], active_step=1, servings=2)
    steps = [
        {"instruction": s["instruction"], "timer_seconds": s["timer_seconds"]}
        for s in test_db.recipe_steps_get(recipe)
    ]
    payload = _proposal(client, recipe, identities["owner"], steps=steps)
    assert (
        client.post(
            f"/api/recipes/{recipe}/import-review", headers=identities["owner"], json=payload
        ).status_code
        == 200
    )
    assert test_db.recipe_cooking_progress_get(recipe, "anna")["completed_steps"] == [0]


def test_review_rollback_on_audit_failure(client, test_db, recipe, identities):
    payload = _proposal(client, recipe, identities["owner"])
    original = test_db.recipe_snapshot(recipe)
    with test_db.conn() as c:
        c.execute(
            "CREATE TRIGGER fail_version BEFORE INSERT ON recipe_versions BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
        )
    with pytest.raises(Exception, match="audit unavailable"):
        client.post(f"/api/recipes/{recipe}/import-review", headers=identities["owner"], json=payload)
    assert test_db.recipe_snapshot(recipe) == original
    with test_db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM recipe_import_corrections").fetchone()[0] == 0


def test_concurrent_correction_compare_and_swap_allows_one_writer(test_db, recipe):
    from app.routes.api_personal_cooking import ImportCorrectionCreate

    revision = service.import_review(test_db, recipe, "owner", True)["revision"]

    def apply(amount):
        payload = ImportCorrectionCreate(
            client_request_id=f"writer-{amount}",
            expected_revision=revision,
            ingredients=[{"name": "Tomaten", "amount": amount, "unit": "g"}],
            steps=[{"instruction": "Tomaten vorsichtig kochen."}],
            servings=2,
            reason="Korrektur",
        ).prepared()
        try:
            return service.submit_correction(test_db, recipe, "owner", True, payload)["status"]
        except service.RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply, [301, 302]))
    assert sorted(results) == ["applied", "conflict"]
    assert len(test_db.recipe_versions_list(recipe)) == 1


def test_migration_270_preserves_existing_recipe_and_memory_on_reopen(test_db, recipe):
    original = test_db.recipe_snapshot(recipe)
    with test_db.conn() as c:
        c.execute("DELETE FROM schema_migrations WHERE version=270")
        c.execute("DROP TABLE recipe_cooking_memory")
        c.execute("DROP TABLE recipe_import_corrections")
    reopened = Database(test_db.path)
    assert reopened.recipe_snapshot(recipe) == original
    note = service.memory_create(reopened, recipe, "anna", _note(step_number=None, step_instruction=None))
    reopened_again = Database(test_db.path)
    assert service.memory_list(reopened_again, recipe, "anna")["items"][0]["id"] == note["id"]
    with reopened_again.conn() as c:
        assert c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 270


def test_deleted_recipe_rejects_memory_and_corrections(client, test_db, recipe, identities):
    payload = _proposal(client, recipe, identities["anna"])
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET deleted_at=1 WHERE id=?", (recipe,))
    for suffix in ("cooking-memory", "import-review"):
        assert client.get(f"/api/recipes/{recipe}/{suffix}", headers=identities["anna"]).status_code == 404
    assert (
        client.post(
            f"/api/recipes/{recipe}/cooking-memory", headers=identities["anna"], json=_note()
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/recipes/{recipe}/import-review", headers=identities["anna"], json=payload
        ).status_code
        == 404
    )


def test_withdrawal_is_owner_or_admin_only_and_never_reverts_applied(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/import-review"
    payload = _proposal(client, recipe, identities["anna"])
    correction_id = client.post(path, headers=identities["anna"], json=payload).json()["correction"]["id"]
    assert client.delete(f"{path}/{correction_id}", headers=identities["ben"]).status_code == 404
    withdrawn = client.delete(f"{path}/{correction_id}", headers=identities["anna"])
    assert withdrawn.json()["correction"]["status"] == "withdrawn"
    assert client.post(f"{path}/{correction_id}/apply", headers=identities["owner"]).status_code == 409
    assert client.post(path, headers=identities["anna"], json=payload).json()["status"] == "withdrawn"
    second = client.post(path, headers=identities["anna"], json={**payload, "client_request_id": "second"})
    second_id = second.json()["correction"]["id"]
    assert client.delete(f"{path}/{second_id}", headers=identities["owner"]).status_code == 200
    applied = client.post(path, headers=identities["owner"], json=payload).json()["correction"]["id"]
    assert client.delete(f"{path}/{applied}", headers=identities["owner"]).status_code == 409


def test_account_deletion_purges_private_memory_before_username_reuse(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/import-review"
    client.post(f"/api/recipes/{recipe}/cooking-memory", headers=identities["anna"], json=_note())
    payload = _proposal(client, recipe, identities["anna"])
    correction_id = client.post(path, headers=identities["anna"], json=payload).json()["correction"]["id"]
    test_db.user_delete(test_db.user_get_by_name("anna")["id"])
    test_db.user_create("anna", "different-password", role="user")
    assert service.memory_list(test_db, recipe, "anna")["items"] == []
    assert service.import_review(test_db, recipe, "anna", False)["corrections"] == []
    shared_audit = service.import_review(test_db, recipe, "owner", True)["corrections"][0]
    assert shared_audit["id"] == correction_id
    assert shared_audit["username"] == ""
    assert shared_audit["status"] == "withdrawn"


def test_import_quality_respects_existing_source_observations(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/import-review"
    source_url = test_db.recipe_get(recipe)["url"]

    def issue_ids():
        report = client.get(path, headers=identities["anna"]).json()
        return {issue["id"] for issue in report["quality"]["issues"]}

    assert "source-unchecked" in issue_ids()
    test_db.recipe_source_snapshot_create(
        recipe,
        source_url=source_url,
        content_sha256="a" * 64,
        content_text="Original",
        state="baseline",
        checked_at=1,
        force_baseline=True,
    )
    assert "source-unchecked" not in issue_ids()
    test_db.recipe_source_snapshot_create(
        recipe,
        source_url=source_url,
        content_sha256="b" * 64,
        content_text="Änderung",
        state="changed",
        checked_at=2,
    )
    assert "source-changed" in issue_ids()
    test_db.recipe_source_snapshot_create(
        recipe,
        source_url=source_url,
        content_sha256=None,
        content_text=None,
        state="unavailable",
        checked_at=3,
    )
    assert "source-unavailable" in issue_ids()


@pytest.mark.parametrize("create_first", [False, True])
def test_client_id_cancellation_never_resurrects_note(client, test_db, recipe, identities, create_first):
    path = f"/api/recipes/{recipe}/cooking-memory"
    payload = _note()
    if create_first:
        assert client.post(path, headers=identities["anna"], json=payload).status_code == 200
    canceled = client.delete(f"{path}/client/{payload['client_entry_id']}", headers=identities["anna"])
    assert canceled.status_code == 200, canceled.text
    assert canceled.json() == {"ok": True}
    assert canceled.headers["cache-control"] == "private, no-store"
    assert (
        client.delete(f"{path}/client/{payload['client_entry_id']}", headers=identities["anna"]).status_code
        == 200
    )
    for retry in (payload, {**payload, "note": "Late changed content"}):
        assert client.post(path, headers=identities["anna"], json=retry).status_code == 410
    assert client.get(path, headers=identities["anna"]).json() == {"items": [], "total": 0}
    with test_db.conn() as c:
        rows = c.execute(
            "SELECT * FROM recipe_cooking_memory WHERE recipe_id=? AND username='anna'",
            (recipe,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["deleted_at"] is not None
    assert rows[0]["note"] == rows[0]["adjustments"] == rows[0]["next_time"] == rows[0]["request_hash"] == ""


def test_client_id_cancellation_is_account_and_recipe_scoped(client, test_db, recipe, identities):
    path = f"/api/recipes/{recipe}/cooking-memory"
    for name in ("anna", "ben", "owner"):
        assert client.post(path, headers=identities[name], json=_note()).status_code == 200
    assert client.delete(f"{path}/client/note-1", headers=identities["anna"]).status_code == 200
    for other in ("ben", "owner"):
        assert client.get(path, headers=identities[other]).json()["total"] == 1
    missing = client.delete("/api/recipes/999999/cooking-memory/client/note-1", headers=identities["anna"])
    assert missing.status_code == 404
    assert client.delete(f"{path}/client/new-note", headers=identities["guest"]).status_code == 403
    assert client.delete(f"{path}/client/new-note").status_code == 401
    with test_db.conn() as c:
        c.execute("UPDATE recipes SET deleted_at=1 WHERE id=?", (recipe,))
    assert client.delete(f"{path}/client/new-note", headers=identities["anna"]).status_code == 404


@pytest.mark.parametrize("client_id", ["bad%20id", "bad%40id", "x" * 101])
def test_client_id_cancellation_validates_identifier(client, recipe, identities, client_id):
    response = client.delete(
        f"/api/recipes/{recipe}/cooking-memory/client/{client_id}", headers=identities["anna"]
    )
    assert response.status_code == 422, response.text


def test_concurrent_create_cancel_always_finishes_invisible(test_db, recipe):
    for iteration in range(6):
        start = Barrier(2)
        client_id = f"race-{iteration}"

        def create():
            start.wait(timeout=5)
            try:
                service.memory_create(test_db, recipe, "anna", _note(client_entry_id=client_id))
                return "created"
            except service.EntryDeleted:
                return "canceled-before-create"

        def cancel():
            start.wait(timeout=5)
            service.memory_cancel(test_db, recipe, "anna", client_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            created = executor.submit(create)
            canceled = executor.submit(cancel)
            assert created.result(timeout=10) in {"created", "canceled-before-create"}
            assert canceled.result(timeout=10) is None
        assert service.memory_list(test_db, recipe, "anna")["items"] == []
        with pytest.raises(service.EntryDeleted):
            service.memory_create(test_db, recipe, "anna", _note(client_entry_id=client_id))
