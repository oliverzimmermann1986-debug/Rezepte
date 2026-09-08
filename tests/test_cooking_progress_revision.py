"""Offline step indices may only attach to the exact step list that created them."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tests.test_recipe_lifecycle_features import _recipe


def _fingerprint(test_db, recipe_id):
    material = "\n".join(
        f"step-{step['id']}:{step['instruction']}" for step in test_db.recipe_steps_get(recipe_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def test_server_announces_atomic_progress_revision_support(client):
    assert "cooking-progress-revision-v1" in client.get("/api/system/info").json()["capabilities"]


def test_progress_matches_swift_utf8_step_fingerprint(client, test_db, tmp_path, monkeypatch):
    recipe_id, _ = _recipe(test_db, tmp_path, name="Fingerprint Unicode")
    with test_db.conn() as c:
        c.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,))
        c.executemany(
            "INSERT INTO recipe_steps(id,recipe_id,step_number,instruction) VALUES (?, ?, ?, ?)",
            [(17, recipe_id, 1, "Öl erhitzen."), (42, recipe_id, 2, "Mit 🍅 servieren.")],
        )
    monkeypatch.setattr("app.routes.api_recipes._actor", lambda request: "anna")
    # Shared fixed vector for the native XCTest: includes IDs, LF and UTF-8.
    expected = "f0c630c77a4be4892a41b3123ccfdfc73f55a99cc86a06c2e5a1c7d85d6a4a54"
    assert _fingerprint(test_db, recipe_id) == expected
    response = client.put(
        f"/api/recipes/{recipe_id}/cooking-progress",
        json={"completed_steps": [0], "active_step": 1, "servings": 2, "expected_step_fingerprint": expected},
    )
    assert response.status_code == 200, response.text
    assert response.json()["completed_steps"] == [0]
    assert response.json()["active_step"] == 1
    assert [step["id"] for step in client.get(f"/api/recipes/{recipe_id}").json()["steps"]] == [17, 42]


@pytest.mark.parametrize("mutation", ["reorder", "rewrite", "replace-identical-text"])
def test_old_offline_progress_cannot_overwrite_current_same_length_steps(
    client, test_db, tmp_path, monkeypatch, mutation
):
    recipe_id, _ = _recipe(test_db, tmp_path, name=f"Gleiche Anzahl {mutation}")
    monkeypatch.setattr("app.routes.api_recipes._actor", lambda request: "anna")
    expected = _fingerprint(test_db, recipe_id)
    steps = test_db.recipe_steps_get(recipe_id)
    if mutation == "replace-identical-text":
        test_db.recipe_steps_set(recipe_id, steps)
    else:
        with test_db.conn() as c:
            if mutation == "reorder":
                c.execute("UPDATE recipe_steps SET step_number=4-step_number WHERE recipe_id=?", (recipe_id,))
            else:
                c.execute(
                    "UPDATE recipe_steps SET instruction='Eine neue Zubereitungsanweisung' WHERE id=?",
                    (steps[0]["id"],),
                )
    assert len(test_db.recipe_steps_get(recipe_id)) == len(steps)
    assert _fingerprint(test_db, recipe_id) != expected
    current = test_db.recipe_cooking_progress_set(
        recipe_id,
        "anna",
        completed_steps=[],
        active_step=0,
        servings=4,
        expected_step_fingerprint=_fingerprint(test_db, recipe_id),
    )
    response = client.put(
        f"/api/recipes/{recipe_id}/cooking-progress",
        json={
            "completed_steps": [0, 1],
            "active_step": 2,
            "servings": 2,
            "expected_step_fingerprint": expected,
        },
    )
    assert response.status_code == 409, response.text
    stored = test_db.recipe_cooking_progress_get(recipe_id, "anna")
    assert stored["completed_steps"] == []
    assert stored["active_step"] == 0
    assert stored["servings"] == 4
    assert stored["updated_at"] == current["updated_at"]


@pytest.mark.parametrize("include_null", [False, True])
def test_old_clients_without_fingerprint_remain_compatible(client, test_db, tmp_path, include_null):
    recipe_id, _ = _recipe(test_db, tmp_path, name="Alter Client")
    payload = {"completed_steps": [0], "active_step": 1, "servings": 2}
    if include_null:
        payload["expected_step_fingerprint"] = None
    response = client.put(f"/api/recipes/{recipe_id}/cooking-progress", json=payload)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("fingerprint", ["", "z" * 64, "a" * 63, "a" * 65, "A" * 64])
def test_malformed_expected_step_fingerprint_rejected(client, test_db, tmp_path, fingerprint):
    recipe_id, _ = _recipe(test_db, tmp_path, name="Ungültiger Fingerprint")
    response = client.put(
        f"/api/recipes/{recipe_id}/cooking-progress",
        json={"completed_steps": [0], "active_step": 1, "expected_step_fingerprint": fingerprint},
    )
    assert response.status_code == 422


def test_step_fingerprint_revalidated_inside_writer_transaction(test_db, tmp_path):
    recipe_id, _ = _recipe(test_db, tmp_path, name="Gleichzeitige Schrittänderung")
    expected = _fingerprint(test_db, recipe_id)
    started = Event()

    def send_old_progress():
        started.set()
        return test_db.recipe_cooking_progress_set(
            recipe_id,
            "anna",
            completed_steps=[0],
            active_step=1,
            servings=2,
            expected_step_fingerprint=expected,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with test_db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE recipe_steps SET instruction=instruction || ' Neu.' WHERE recipe_id=?", (recipe_id,)
            )
            future = executor.submit(send_old_progress)
            assert started.wait(timeout=2)
        with pytest.raises(RuntimeError, match="Zubereitung wurde geändert"):
            future.result(timeout=5)
    assert test_db.recipe_cooking_progress_get(recipe_id, "anna") is None
