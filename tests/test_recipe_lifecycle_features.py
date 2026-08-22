import json
from pathlib import Path

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
