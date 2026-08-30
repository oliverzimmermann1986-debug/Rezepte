from __future__ import annotations

import gzip
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import Database


def _recipe(db: Database, folder: Path, *, name: str = "Test") -> int:
    return db.recipe_upsert(
        url=f"https://example.test/{name}",
        name=name,
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description="Eine ausreichend lange Beschreibung für die Extraktion.",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )


def test_extraction_claim_and_publish_are_atomic(test_db: Database, tmp_path: Path):
    rid = _recipe(test_db, tmp_path / "recipe")

    claimed = test_db.recipes_claim_extraction(limit=10, owner="worker-a")
    assert [row["id"] for row in claimed] == [rid]
    assert test_db.recipes_claim_extraction(limit=10, owner="worker-b") == []

    assert test_db.recipe_apply_extraction_result(
        rid,
        ingredients=[{
            "name": "Tomate",
            "canonical_name": "tomate",
            "amount": 2,
            "unit": "Stück",
            "raw": "2 Tomaten",
        }],
        steps=[{"instruction": "Tomaten schneiden.", "timer_seconds": 30}],
        servings=2,
        auto_tags=["Vegetarisch"],
        claim_owner="worker-b",
    ) is False
    assert test_db.recipe_ingredients_get(rid) == []

    assert test_db.recipe_apply_extraction_result(
        rid,
        ingredients=[{
            "name": "Tomate",
            "canonical_name": "tomate",
            "amount": 2,
            "unit": "Stück",
            "raw": "2 Tomaten",
        }],
        steps=[{"instruction": "Tomaten schneiden.", "timer_seconds": 30}],
        servings=2,
        auto_tags=["Vegetarisch"],
        claim_owner="worker-a",
    ) is True

    recipe = test_db.recipe_get(rid)
    assert recipe["ingredients_status"] == "ok"
    assert recipe["extraction_claim_owner"] is None
    assert recipe["servings"] == 2
    assert test_db.recipe_steps_get(rid)[0]["instruction"] == "Tomaten schneiden."
    assert test_db.recipe_tags_get(rid)[0]["name"] == "Vegetarisch"


def test_background_task_active_dedupe(test_db: Database):
    first = test_db.background_task_enqueue(
        "share_ingest",
        {"url": "https://example.test/a"},
        dedupe_key="same",
    )
    second = test_db.background_task_enqueue(
        "share_ingest",
        {"url": "https://example.test/a"},
        dedupe_key="same",
    )
    assert second == first


def test_share_intake_tokens_are_hashed_and_individually_revocable(test_db: Database):
    first = "device-a." + "a" * 40
    second = "device-b." + "b" * 40
    test_db.share_intake_token_create(
        "device-a", hashlib.sha256(first.encode()).hexdigest(), "Telefon", "admin",
    )
    test_db.share_intake_token_create(
        "device-b", hashlib.sha256(second.encode()).hexdigest(), "Tablet", "admin",
    )

    assert test_db.share_intake_token_consume(hashlib.sha256(first.encode()).hexdigest())["id"] == "device-a"
    listed = test_db.share_intake_tokens_list()
    assert all("token_hash" not in item for item in listed)
    assert test_db.share_intake_token_revoke("device-a") is True
    assert test_db.share_intake_token_consume(hashlib.sha256(first.encode()).hexdigest()) is None
    assert test_db.share_intake_token_consume(hashlib.sha256(second.encode()).hexdigest())["id"] == "device-b"


def test_nutrition_endpoint_uses_owner_claim_and_publishes_atomically(
    client, test_db: Database, tmp_path: Path, monkeypatch,
):
    import app.routes.api_recipes as api_recipes

    rid = _recipe(test_db, tmp_path / "nutrition", name="Nährwerte")
    test_db.recipe_set_extraction_result(
        rid,
        "ok",
        [
            {"name": "A", "canonical_name": "a"},
            {"name": "B", "canonical_name": "b"},
            {"name": "C", "canonical_name": "c"},
        ],
    )

    class Analyzer:
        @staticmethod
        def compute_nutrition(ingredients, servings):
            return {
                "calories": 420,
                "protein_g": 20,
                "carbs_g": 40,
                "fat_g": 12,
                "per_ingredient": {"0": 100, "1": 140, "2": 180},
            }

    monkeypatch.setattr(api_recipes, "build_analyzer", lambda config: Analyzer())
    response = client.post(f"/api/recipes/{rid}/nutrition")

    assert response.status_code == 200
    recipe = test_db.recipe_get(rid)
    assert recipe["calories_per_serving"] == 420
    assert recipe["nutrition_claim_owner"] is None
    assert [row["calories"] for row in test_db.recipe_ingredients_get(rid)] == [100, 140, 180]


def test_ingredient_mutation_revokes_claim_and_rejects_stale_nutrition_owner(
    client, test_db: Database, tmp_path: Path,
):
    rid = _recipe(test_db, tmp_path / "stale-owner", name="Alter Owner")
    ingredients = [
        {"name": "A", "canonical_name": "a"},
        {"name": "B", "canonical_name": "b"},
        {"name": "C", "canonical_name": "c"},
    ]
    test_db.recipe_set_extraction_result(rid, "ok", ingredients)
    test_db.recipe_set_nutrition(
        rid,
        400,
        10,
        20,
        30,
        ingredient_calories={
            item["id"]: 100
            for item in test_db.recipe_ingredients_get(rid)
        },
    )
    assert test_db.recipe_claim_nutrition(rid, "old-worker") is True

    response = client.put(
        f"/api/recipes/{rid}/ingredients",
        json={"ingredients": [
            {"name": "Neu A"},
            {"name": "Neu B"},
            {"name": "Neu C"},
        ]},
    )

    assert response.status_code == 200, response.text
    recipe = test_db.recipe_get(rid)
    for field in (
        "calories_per_serving",
        "protein_g",
        "carbs_g",
        "fat_g",
        "nutrition_computed_at",
        "nutrition_claimed_at",
        "nutrition_claim_owner",
    ):
        assert recipe[field] is None
    assert all(
        item["calories"] is None
        for item in test_db.recipe_ingredients_get(rid)
    )
    with pytest.raises(RuntimeError, match="nicht mehr gültig"):
        test_db.recipe_set_nutrition(
            rid,
            999,
            99,
            99,
            99,
            claim_owner="old-worker",
        )
    assert test_db.recipe_get(rid)["calories_per_serving"] is None


def test_nutrition_endpoint_claims_before_authoritative_ingredient_snapshot(
    client, test_db: Database, tmp_path: Path, monkeypatch,
):
    import app.routes.api_recipes as api_recipes

    rid = _recipe(test_db, tmp_path / "claim-first", name="Claim zuerst")
    test_db.recipe_set_extraction_result(
        rid,
        "ok",
        [
            {"name": "Alt A", "canonical_name": "alt-a"},
            {"name": "Alt B", "canonical_name": "alt-b"},
            {"name": "Alt C", "canonical_name": "alt-c"},
        ],
    )
    real_claim = test_db.recipe_claim_nutrition
    observed = []

    def mutate_then_claim(recipe_id, owner):
        test_db.recipe_set_extraction_result(
            recipe_id,
            "ok",
            [
                {"name": "Neu A", "canonical_name": "neu-a"},
                {"name": "Neu B", "canonical_name": "neu-b"},
                {"name": "Neu C", "canonical_name": "neu-c"},
            ],
        )
        return real_claim(recipe_id, owner)

    class Analyzer:
        @staticmethod
        def compute_nutrition(ingredients, servings):
            observed.extend(item["name"] for item in ingredients)
            return {
                "calories": 300,
                "protein_g": 10,
                "carbs_g": 20,
                "fat_g": 30,
                "per_ingredient": {},
            }

    monkeypatch.setattr(test_db, "recipe_claim_nutrition", mutate_then_claim)
    monkeypatch.setattr(api_recipes, "build_analyzer", lambda config: Analyzer())

    response = client.post(f"/api/recipes/{rid}/nutrition")

    assert response.status_code == 200, response.text
    assert observed == ["Neu A", "Neu B", "Neu C"]


def test_extraction_worker_rejects_nutrition_after_concurrent_ingredient_change(
    test_db: Database, tmp_path: Path, monkeypatch,
):
    from app.recipes import indexer

    rid = _recipe(test_db, tmp_path / "worker-owner", name="Worker Owner")
    claimed = test_db.recipes_claim_extraction(limit=1, owner="extract-worker")
    assert [row["id"] for row in claimed] == [rid]
    observed = []

    class Analyzer:
        @staticmethod
        def translate_to_german(_description):
            return None

        @staticmethod
        def compute_nutrition(ingredients, servings):
            observed.extend(item["name"] for item in ingredients)
            test_db.recipe_set_extraction_result(
                rid,
                "ok",
                [{
                    "name": "Parallel geändert",
                    "canonical_name": "parallel-geaendert",
                }],
            )
            return {
                "calories": 777,
                "protein_g": 7,
                "carbs_g": 7,
                "fat_g": 7,
            }

    monkeypatch.setattr(
        indexer,
        "analyze_recipe_with_video_fallback",
        lambda *_args, **_kwargs: SimpleNamespace(
            content={
                "ingredients": [
                    {"name": "A"},
                    {"name": "B"},
                    {"name": "C"},
                ],
                "steps": [{"instruction": "Kochen."}],
                "tags": [],
                "servings": 2,
            },
            used_video=False,
            reason="test",
            frame_text_count=0,
            transcribed=False,
        ),
    )

    indexer._extract_for_recipe(
        test_db,
        Analyzer(),
        claimed[0],
        "extract-worker",
    )

    assert observed == ["A", "B", "C"]
    recipe = test_db.recipe_get(rid)
    assert recipe["calories_per_serving"] is None
    assert recipe["nutrition_claim_owner"] is None
    assert [
        item["name"] for item in test_db.recipe_ingredients_get(rid)
    ] == ["Parallel geändert"]


def test_compressed_backup_is_complete_and_valid(test_db: Database, tmp_path: Path):
    _recipe(test_db, tmp_path / "recipe")
    target = tmp_path / "backup.db.gz"

    result = test_db.backup_to(target, compress=True, verify=True)

    assert result["ok"] is True
    assert result["verified"] is True
    unpacked = tmp_path / "unpacked.db"
    with gzip.open(target, "rb") as source:
        unpacked.write_bytes(source.read())
    with sqlite3.connect(unpacked) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    assert not list(tmp_path.glob(".tmp-*"))


def test_password_change_invalidates_existing_session(
    test_db: Database,
    monkeypatch,
):
    import app.auth as auth

    class Config:
        def get(self, *keys, default=None):
            values = {
                ("web", "secret_key"): "x" * 48,
                ("web", "username"): "admin",
                ("web", "session_version"): 0,
            }
            return values.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    user_id = test_db.user_create("admin", auth.hash_password("first-password"))
    token = auth.create_session("admin")
    assert auth.session_user(token) == "admin"

    test_db.user_set_password(user_id, auth.hash_password("second-password"))
    assert auth.session_user(token) is None


def test_config_login_cannot_bypass_existing_database_users(
    test_db: Database,
    monkeypatch,
):
    import app.auth as auth

    config_hash = auth.hash_password("legacy-password")

    class Config:
        def get(self, *keys, default=None):
            values = {
                ("web", "secret_key"): "x" * 48,
                ("web", "username"): "admin",
                ("web", "password"): config_hash,
                ("web", "session_version"): 0,
            }
            return values.get(keys, default)

    monkeypatch.setattr(auth, "get_config", lambda: Config())
    test_db.user_create("other-user", auth.hash_password("other-password"))

    assert auth.check_credentials("admin", "legacy-password") is False
    with pytest.raises(ValueError):
        auth.create_session("admin")


def test_share_intake_only_accepts_supported_https_hosts():
    from fastapi import HTTPException
    from app.routes.api_share import _normalized_share_url

    assert (
        _normalized_share_url(
            "https://WWW.TIKTOK.COM/@cook/video/123#comments"
        )
        == "https://www.tiktok.com/@cook/video/123"
    )
    with pytest.raises(HTTPException):
        _normalized_share_url("http://www.tiktok.com/@cook/video/123")
    with pytest.raises(HTTPException):
        _normalized_share_url("https://tiktok.com.attacker.test/video/123")
    with pytest.raises(HTTPException):
        _normalized_share_url("https://127.0.0.1/internal")


def test_busy_share_ingest_stays_retryable(test_db, monkeypatch):
    from contextlib import contextmanager
    from app.routes import api_share

    @contextmanager
    def busy(_name):
        yield None

    monkeypatch.setattr(api_share, "file_lock_or_none", busy)

    result = api_share.run_share_ingest_task({
        "url": "https://www.tiktok.com/@cook/video/123",
        "type": "recipe",
    })

    assert result["ok"] is False
    assert result["retry"] is True
    assert "belegt" in result["error"]


def test_soft_delete_with_files_can_restore_quarantine(
    test_db: Database,
    tmp_path: Path,
    monkeypatch,
):
    import app.recipes.manage as manage

    recipe_root = tmp_path / "recipes"
    trash_root = tmp_path / "trash"
    folder = recipe_root / "Hauptgericht" / "Test" / "Rezept"
    folder.mkdir(parents=True)
    (folder / "description.txt").write_text("Beschreibung", encoding="utf-8")
    rid = _recipe(test_db, folder)

    class Config:
        def get(self, *keys, default=None):
            values = {
                ("paths", "recipe_dir"): str(recipe_root),
                ("safety", "trash_dir"): str(trash_root),
            }
            return values.get(keys, default)

    monkeypatch.setattr(manage, "get_config", lambda: Config())
    deleted = manage.safe_delete_recipe(
        test_db,
        rid,
        delete_files=True,
        hard=False,
    )
    assert deleted["folder_deleted"] is True
    assert not folder.exists()

    restored = manage.safe_restore_recipe(test_db, rid)
    assert restored["ok"] is True
    assert restored["files_restored"] is True
    assert folder.is_dir()
    assert test_db.recipe_get(rid)["deleted_at"] is None


def test_extract_frame_rejects_recipe_folder_outside_root(
    client,
    test_db: Database,
    tmp_path: Path,
    monkeypatch,
):
    from app.routes import api_recipes

    recipe_root = tmp_path / "recipes"
    recipe_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.mp4").write_bytes(b"not-a-real-video")
    recipe_id = _recipe(test_db, outside, name="Unsicherer Pfad")
    monkeypatch.setattr(api_recipes, "_recipe_root", lambda: recipe_root)

    response = client.post(f"/api/recipes/{recipe_id}/extract-frame")

    assert response.status_code == 404
    assert response.json()["detail"] == "Rezeptordner fehlt oder ist nicht zulässig"
