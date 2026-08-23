import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.db import CookingCompletionConflictError
from app.recipes import manage


def _recipe(
    db,
    root: Path,
    *,
    name: str,
    category: str = "Pasta",
    source_url: str | None = None,
) -> tuple[int, Path]:
    folder = root / "Hauptgericht" / category / manage.sanitize_filename(name)
    folder.mkdir(parents=True)
    url = source_url or f"https://example.test/{manage.sanitize_filename(name)}"
    (folder / "info.json").write_text(
        json.dumps({
            "name": name,
            "type": "Hauptgericht",
            "category": category,
            "url": url,
        }),
        encoding="utf-8",
    )
    (folder / "description.txt").write_text("Eine sichere Beschreibung", encoding="utf-8")
    recipe_id = db.recipe_upsert(
        url=url,
        name=name,
        type="Hauptgericht",
        category=category,
        folder_path=str(folder),
        description="Eine sichere Beschreibung",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [{
            "name": "Tomate",
            "canonical_name": "tomate",
            "amount": 2,
            "unit": "Stück",
            "raw": "2 Tomaten",
        }],
    )
    db.recipe_steps_set(recipe_id, [
        {"instruction": "Tomaten schneiden"},
        {"instruction": "Alles kochen", "timer_seconds": 300},
        {"instruction": "Servieren"},
    ])
    with db.conn() as connection:
        connection.execute("UPDATE recipes SET servings=2 WHERE id=?", (recipe_id,))
    return recipe_id, folder


def test_cooking_progress_completion_and_history(client, test_db, tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    recipe_id, _folder = _recipe(test_db, root, name="Kochlauf")
    monkeypatch.setattr("app.routes.api_recipes._actor", lambda _request: "oliver")

    initial = client.get(f"/api/recipes/{recipe_id}/cooking-progress")
    assert initial.status_code == 200
    assert initial.json()["exists"] is False
    assert initial.json()["step_count"] == 3

    saved = client.put(
        f"/api/recipes/{recipe_id}/cooking-progress",
        json={"completed_steps": [0], "active_step": 1, "servings": 4},
    )
    assert saved.status_code == 200
    assert saved.json()["completed_steps"] == [0]
    assert saved.json()["username"] == "oliver"

    completed = client.post(
        f"/api/recipes/{recipe_id}/cooking-complete",
        json={"servings": 4},
    )
    assert completed.status_code == 200
    assert completed.json()["summary"]["count"] == 1
    assert completed.json()["entry"]["cooked_by"] == "oliver"

    after = client.get(f"/api/recipes/{recipe_id}/cooking-progress").json()
    assert after["exists"] is False
    history = client.get(f"/api/recipes/{recipe_id}/cook-history").json()
    assert history["summary"]["last_servings"] == 4
    assert [item["cooked_by"] for item in history["items"]] == ["oliver"]
    detail = client.get(f"/api/recipes/{recipe_id}").json()
    assert detail["cook_summary"]["count"] == 1
    assert len(detail["cook_history"]) == 1


def test_cooking_progress_rejects_stale_step_index(client, test_db, tmp_path):
    recipe_id, _folder = _recipe(test_db, tmp_path / "recipes", name="Ungültig")

    response = client.put(
        f"/api/recipes/{recipe_id}/cooking-progress",
        json={"completed_steps": [9], "active_step": 0, "servings": 2},
    )

    assert response.status_code == 400
    assert test_db.recipe_cooking_progress_get(recipe_id, "unknown") is None


def test_progress_write_revalidates_after_concurrent_step_replacement(
    test_db, tmp_path
):
    recipe_id, _folder = _recipe(
        test_db, tmp_path / "recipes", name="Parallel neue Schritte"
    )
    checked_old_steps = threading.Event()

    def stale_progress_write():
        # Simuliert die alte Route: Der Client/Request hat noch drei Schritte
        # gesehen, bevor der konkurrierende Editor committed.
        assert len(test_db.recipe_steps_get(recipe_id)) == 3
        checked_old_steps.set()
        return test_db.recipe_cooking_progress_set(
            recipe_id,
            "oliver",
            completed_steps=[2],
            active_step=2,
            servings=2,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with test_db.conn() as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,)
            )
            writer.execute(
                "INSERT INTO recipe_steps "
                "(recipe_id, step_number, instruction, timer_seconds) "
                "VALUES (?, 1, 'Neue einzige Anweisung', NULL)",
                (recipe_id,),
            )
            writer.execute(
                "DELETE FROM recipe_cooking_progress WHERE recipe_id=?",
                (recipe_id,),
            )
            future = executor.submit(stale_progress_write)
            assert checked_old_steps.wait(timeout=2)

        with pytest.raises(ValueError, match="passt nicht zur Schrittliste"):
            future.result(timeout=3)

    assert test_db.recipe_cooking_progress_get(recipe_id, "oliver") is None


def test_step_update_atomically_clears_progress_for_every_user(client, test_db, tmp_path):
    recipe_id, _folder = _recipe(test_db, tmp_path / "recipes", name="Neue Reihenfolge")
    for username in ("oliver", "freundin"):
        test_db.recipe_cooking_progress_set(
            recipe_id,
            username,
            completed_steps=[0],
            active_step=1,
            servings=2,
        )

    response = client.put(
        f"/api/recipes/{recipe_id}/steps",
        json={
            "steps": [
                {"instruction": "Neu vorbereiten"},
                {"instruction": "Tomaten schneiden"},
                {"instruction": "Alles kochen", "timer_seconds": 300},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["cleared_cooking_progress"] == 2
    assert test_db.recipe_cooking_progress_get(recipe_id, "oliver") is None
    assert test_db.recipe_cooking_progress_get(recipe_id, "freundin") is None
    assert [step["instruction"] for step in test_db.recipe_steps_get(recipe_id)] == [
        "Neu vorbereiten",
        "Tomaten schneiden",
        "Alles kochen",
    ]


def test_ingredient_update_revokes_previous_verification(client, test_db, tmp_path):
    recipe_id, _folder = _recipe(test_db, tmp_path / "recipes", name="Neu prüfen")
    test_db.recipe_set_verified(recipe_id, True, "oliver")

    response = client.put(
        f"/api/recipes/{recipe_id}/ingredients",
        json={
            "ingredients": [
                {"name": "Kartoffel", "amount": 4, "unit": "Stück", "raw": "4 Kartoffeln"}
            ]
        },
    )

    assert response.status_code == 200, response.text
    recipe = test_db.recipe_get(recipe_id)
    assert recipe["user_verified"] == 0
    assert recipe["verified_at"] is None
    assert recipe["verified_by"] is None
    assert test_db.recipe_ingredients_get(recipe_id)[0]["name"] == "Kartoffel"


def test_verification_rechecks_ingredients_after_concurrent_replacement(
    test_db, tmp_path
):
    recipe_id, _folder = _recipe(
        test_db, tmp_path / "recipes", name="Parallel leere Zutaten"
    )
    checked_old_ingredients = threading.Event()

    def stale_verification():
        assert test_db.recipe_ingredients_get(recipe_id)
        checked_old_ingredients.set()
        test_db.recipe_set_verified(recipe_id, True, "oliver")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with test_db.conn() as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,)
            )
            writer.execute(
                "UPDATE recipes SET user_verified=0, verified_at=NULL, "
                "verified_by=NULL WHERE id=?",
                (recipe_id,),
            )
            future = executor.submit(stale_verification)
            assert checked_old_ingredients.wait(timeout=2)

        with pytest.raises(ValueError, match="leere Zutatenliste"):
            future.result(timeout=3)

    recipe = test_db.recipe_get(recipe_id)
    assert test_db.recipe_ingredients_get(recipe_id) == []
    assert recipe["user_verified"] == 0


def test_cooking_complete_idempotency_replays_without_duplicate_history(
    client, test_db, tmp_path, monkeypatch
):
    recipe_id, _folder = _recipe(
        test_db, tmp_path / "recipes", name="Abschluss Retry"
    )
    monkeypatch.setattr("app.routes.api_recipes._actor", lambda _request: "oliver")
    headers = {"Idempotency-Key": "cook-session-42"}

    first = client.post(
        f"/api/recipes/{recipe_id}/cooking-complete",
        json={"servings": 4},
        headers=headers,
    )
    replay = client.post(
        f"/api/recipes/{recipe_id}/cooking-complete",
        json={"servings": 4},
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert first.json()["summary"]["count"] == 1
    assert len(test_db.recipe_cook_history(recipe_id)) == 1

    conflict = client.post(
        f"/api/recipes/{recipe_id}/cooking-complete",
        json={"servings": 2},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert "anderen Portionszahl" in conflict.json()["detail"]
    assert len(test_db.recipe_cook_history(recipe_id)) == 1


def test_concurrent_cooking_complete_with_same_key_is_inserted_once(
    test_db, tmp_path
):
    recipe_id, _folder = _recipe(
        test_db, tmp_path / "recipes", name="Paralleler Abschluss"
    )
    barrier = threading.Barrier(3)

    def complete(servings: int):
        barrier.wait(timeout=2)
        return test_db.recipe_cooking_complete(
            recipe_id,
            "oliver",
            servings=servings,
            idempotency_key="same-mobile-request",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(complete, 4) for _ in range(2)]
        barrier.wait(timeout=2)
        results = [future.result(timeout=3) for future in futures]

    assert results[0] == results[1]
    assert len(test_db.recipe_cook_history(recipe_id)) == 1


def test_concurrent_cooking_complete_rejects_key_reuse_with_other_servings(
    test_db, tmp_path
):
    recipe_id, _folder = _recipe(
        test_db, tmp_path / "recipes", name="Paralleler Konflikt"
    )
    barrier = threading.Barrier(3)

    def complete(servings: int):
        barrier.wait(timeout=2)
        try:
            return ("ok", test_db.recipe_cooking_complete(
                recipe_id,
                "oliver",
                servings=servings,
                idempotency_key="conflicting-mobile-request",
            ))
        except CookingCompletionConflictError as exc:
            return ("conflict", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(complete, servings) for servings in (2, 4)]
        barrier.wait(timeout=2)
        outcomes = [future.result(timeout=3) for future in futures]

    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "ok"]
    assert len(test_db.recipe_cook_history(recipe_id)) == 1


def test_duplicate_recipe_creates_video_free_independent_variant(
    client, test_db, tmp_path, monkeypatch
):
    root = tmp_path / "recipes"
    recipe_id, folder = _recipe(test_db, root, name="Original")
    (folder / "Original.jpg").write_bytes(b"cover")
    (folder / "original.pdf").write_bytes(b"pdf")
    (folder / "Original.mp4").write_bytes(b"video")
    with test_db.conn() as connection:
        connection.execute(
            "UPDATE recipes SET thumb_filename='Original.jpg', "
            "video_filename='Original.mp4' WHERE id=?",
            (recipe_id,),
        )
    test_db.recipe_tags_set(recipe_id, ["Familie"])
    test_db.recipe_auto_tags_set(recipe_id, ["vegan"])
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    response = client.post(
        f"/api/recipes/{recipe_id}/duplicate",
        json={"new_name": "Original mild"},
    )

    assert response.status_code == 200
    payload = response.json()
    variant = test_db.recipe_get(payload["recipe_id"])
    target = Path(variant["folder_path"])
    assert variant["url"] is None
    assert variant["video_filename"] is None
    assert variant["thumb_filename"] == "Original.jpg"
    assert (target / "Original.jpg").read_bytes() == b"cover"
    assert (target / "original.pdf").read_bytes() == b"pdf"
    assert not list(target.glob("*.mp4"))
    assert json.loads((target / "info.json").read_text(encoding="utf-8"))["variant_of"] == recipe_id
    assert len(test_db.recipe_ingredients_get(variant["id"])) == 1
    assert len(test_db.recipe_steps_get(variant["id"])) == 3
    assert {tag["name"] for tag in test_db.recipe_tags_get(variant["id"])} == {
        "Familie", "vegan",
    }


def test_bulk_edit_moves_categories_and_updates_only_manual_tags(
    client, test_db, tmp_path, monkeypatch
):
    root = tmp_path / "recipes"
    first_id, _first = _recipe(test_db, root, name="Erstes")
    second_id, _second = _recipe(test_db, root, name="Zweites")
    for recipe_id in (first_id, second_id):
        test_db.recipe_tags_set(recipe_id, ["Alt"])
        test_db.recipe_auto_tags_set(recipe_id, ["vegan"])
    monkeypatch.setattr(manage, "_recipe_root", lambda: root.resolve())

    response = client.post(
        "/api/recipes/bulk-edit",
        json={
            "recipe_ids": [first_id, second_id],
            "category": "Feierabend",
            "add_tags": ["Schnell"],
            "remove_tags": ["Alt"],
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(response.json()["updated"]) == 2
    for recipe_id, name in ((first_id, "Erstes"), (second_id, "Zweites")):
        recipe = test_db.recipe_get(recipe_id)
        assert recipe["category"] == "Feierabend"
        assert Path(recipe["folder_path"]) == (
            root / "Hauptgericht" / "Feierabend" / manage.sanitize_filename(name)
        ).resolve()
        tags = {(tag["name"], tag["auto"]) for tag in test_db.recipe_tags_get(recipe_id)}
        assert tags == {("Schnell", 0), ("vegan", 1)}
        versions = test_db.recipe_versions_list(recipe_id)
        assert len(versions) == 1
        assert versions[0]["reason"] == "Massenpflege: Kategorie oder Tags geändert"
