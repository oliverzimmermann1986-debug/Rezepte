import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

import tools.refresh_app_review_demo as refresh_module
from app.core import recipe_web
from app.db import Database
from app.recipes.meal_conductor import build_conductor_plan
from app.recipes.source_integrity import normalize_source_text, source_fingerprint
from app.recipes.substitution_lab import substitution_lab_payload
from tools.refresh_app_review_demo import LEGACY_SOURCE_URL, refresh_app_review_demo
from tools.setup_app_review_demo import RECIPES, seed_review_demo
from tools.setup_app_review_demo import (
    REVIEW_PLAN_RECIPES,
    REVIEW_PLAN_WEEKS,
    REVIEW_SOURCE_CURRENT_TEXT,
    review_source_url,
)


ROOT = Path(__file__).resolve().parents[1]


def _review_inputs(tmp_path: Path) -> dict:
    assets = tmp_path / "assets"
    assets.mkdir()
    source_image = ROOT / "native-ios" / "assets" / "images" / "icon.png"
    for recipe in RECIPES:
        shutil.copy2(source_image, assets / f"{recipe['slug']}.png")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({
            "web": {"auth_disabled": True, "public_url": ""},
            "mail": {
                "recipe": {"enabled": True, "username": "private", "password": "private"},
                "wedding": {"enabled": True, "username": "private", "password": "private"},
            },
            "ai": {
                "openai": {"api_key": "private", "base_url": "https://private.invalid"},
                "auto_translate": True,
                "video_fallback": {"enabled": True},
            },
            "ytdlp": {"cookies_file": "/private/cookies", "expanded_tiktok_caption": True},
            "webhooks": [{"url": "https://private.invalid"}],
            "external_hdd": {"enabled": True},
            "einkauf": {"api_url": "https://private.invalid", "app_token": "private"},
        }, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "db_path": tmp_path / "review.db",
        "recipe_root": tmp_path / "recipes",
        "asset_root": assets,
        "config_path": config,
        "credential_output": tmp_path / "credentials.txt",
        "public_url": "https://rezepte-review.mausbaeren.me",
        "trusted_proxy_cidr": "192.168.1.141/32",
        "hostname": "rezepte-review",
    }


def test_review_demo_is_artificial_complete_and_sanitized(tmp_path: Path, monkeypatch):
    inputs = _review_inputs(tmp_path)
    result = seed_review_demo(**inputs)
    db = Database(inputs["db_path"])
    config = yaml.safe_load(inputs["config_path"].read_text(encoding="utf-8"))

    assert result["recipes"] == 6
    assert result["complete_recipes"] == 5
    assert result["manual_care_recipes"] == 1
    assert db.recipe_count() == 6
    meal_entries = db.meal_plan_entries("2000-01-01", "2100-01-01")
    assert len(meal_entries) == REVIEW_PLAN_WEEKS * len(REVIEW_PLAN_RECIPES)
    planned_dates = sorted({entry["planned_for"] for entry in meal_entries})
    assert len(planned_dates) == REVIEW_PLAN_WEEKS
    conductor_entries = [
        entry for entry in meal_entries if entry["planned_for"] == planned_dates[0]
    ]
    assert len(conductor_entries) == 3
    plan = build_conductor_plan(
        conductor_entries,
        {
            int(entry["recipe_id"]): db.recipe_steps_get(int(entry["recipe_id"]))
            for entry in conductor_entries
        },
        planned_for=date.fromisoformat(planned_dates[0]),
        serve_hour=19,
        serve_minute=0,
        burners=1,
        oven_slots=1,
        active_cooks=1,
    )
    assert plan["summary"]["recipes"] == 3
    assert plan["summary"]["steps"] == 9

    pancakes = db.recipe_get_by_url("review-demo://beeren-pancakes")
    substitution_lab = substitution_lab_payload(
        pancakes,
        db.recipe_ingredients_get(pancakes["id"]),
    )
    candidate_ids = {
        candidate["id"]
        for item in substitution_lab["items"]
        for candidate in item["candidates"]
    }
    assert {"egg-applesauce", "milk-oat-drink"} <= candidate_ids
    assert len(db.cart_list()) == 3
    assert result["recurring_items"] == 1
    assert len(db.recurring_list()) == 1
    hafermilch = next(item for item in db.cart_list() if item["canonical_name"] == "hafermilch")
    assert hafermilch["amount"] == 2
    source_url = review_source_url()
    source_recipe = db.recipe_get_by_url(source_url)
    assert source_recipe is not None
    source_state = db.recipe_source_snapshot_state(source_recipe["id"], source_url)
    assert source_state["baseline"]["content_sha256"] != source_state["latest"]["content_sha256"]
    source_html = (
        ROOT / "app" / "static" / "review-source-zitronen-ricotta-pasta.html"
    ).read_text(encoding="utf-8")

    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = source_html
        content = source_html.encode("utf-8")

    monkeypatch.setattr(
        recipe_web,
        "_request_following_public_redirects",
        lambda url, **_kwargs: (Response(), url),
    )
    extracted = recipe_web.extract_recipe_web_metadata(
        source_url,
        include_thumbnail=False,
    )
    assert extracted["description_source"] == "recipe-json-ld"
    assert normalize_source_text(extracted["description_text"]) == normalize_source_text(
        REVIEW_SOURCE_CURRENT_TEXT
    )
    assert source_fingerprint(extracted["description_text"]) == source_state["latest"][
        "content_sha256"
    ]
    assert db.user_get_by_name("app-review")["role"] == "admin"
    assert len(list(inputs["recipe_root"].rglob("*.png"))) == 6
    if os.name != "nt":
        assert inputs["credential_output"].stat().st_mode & 0o777 == 0o600
    assert "Password:" in inputs["credential_output"].read_text(encoding="utf-8")
    assert config["web"]["auth_disabled"] is False
    assert config["web"]["public_url"] == inputs["public_url"]
    assert config["web"]["trusted_proxies"] == [
        "127.0.0.1/32",
        "::1/128",
        inputs["trusted_proxy_cidr"],
    ]
    assert config["mail"]["recipe"]["enabled"] is False
    assert config["mail"]["wedding"]["enabled"] is False
    assert config["ai"]["openai"]["api_key"] == ""
    assert config["ai"]["video_fallback"]["enabled"] is False
    assert config["webhooks"] == []
    assert config["external_hdd"]["enabled"] is False
    assert config["einkauf"]["app_token"] == ""
    assert (ROOT / "app" / "static" / "review-source-zitronen-ricotta-pasta.html").is_file()


def test_review_demo_refuses_wrong_host_and_nonempty_instance(tmp_path: Path):
    inputs = _review_inputs(tmp_path)
    with pytest.raises(RuntimeError, match="nur auf Hostname"):
        seed_review_demo(**{**inputs, "hostname": "rezepte"})

    seed_review_demo(**inputs)
    inputs["credential_output"].unlink()
    with pytest.raises(RuntimeError, match="nicht leer"):
        seed_review_demo(**inputs)


def test_artificial_review_source_is_served_as_html(client):
    response = client.get("/static/review-source-zitronen-ricotta-pasta.html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ausschließlich künstliche Daten" in response.text


def _add_artificial_variant(inputs: dict, db: Database) -> int:
    source = next(inputs["recipe_root"].rglob("lachs-kraeuterkruste"))
    target = source.parent / "lachs-kraeuterkruste-variante"
    shutil.copytree(source, target)
    info_path = target / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["name"] = "Lachs mit Kräuterkruste – Variante"
    info_path.write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return db.recipe_upsert(
        url="review-demo://lachs-kraeuterkruste-variante",
        name=info["name"],
        type=info["type"],
        category=info["category"],
        folder_path=str(target),
        description="Rein künstliche Variante für den Migrationsnachweis.",
        thumb_filename="lachs-kraeuterkruste.png",
        video_filename=None,
        source_added_at=1_700_000_000.0,
    )


def _prepare_legacy_review_state(inputs: dict) -> tuple[Database, int, int]:
    seed_review_demo(**inputs)
    db = Database(inputs["db_path"])
    source_recipe = db.recipe_get_by_url(review_source_url())
    variant_id = _add_artificial_variant(inputs, db)
    legacy_plan = (
        ("2026-08-24", int(source_recipe["id"]), 2),
        (
            "2026-08-26",
            int(db.recipe_get_by_url("review-demo://ofengemuese-feta")["id"]),
            4,
        ),
        (
            "2026-08-27",
            variant_id,
            2,
        ),
        (
            "2026-08-28",
            int(db.recipe_get_by_url("review-demo://lachs-kraeuterkruste")["id"]),
            2,
        ),
    )
    now = 1_777_777_777.0
    with db.conn() as connection:
        connection.execute(
            "UPDATE recipes SET url=? WHERE id=?",
            (LEGACY_SOURCE_URL, int(source_recipe["id"])),
        )
        connection.execute(
            "DELETE FROM recipe_source_snapshots WHERE recipe_id=?",
            (int(source_recipe["id"]),),
        )
        connection.execute("DELETE FROM meal_plan_entries")
        connection.executemany(
            "INSERT INTO meal_plan_entries (planned_for, recipe_id, "
            "planned_servings, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            [(*entry, now, now) for entry in legacy_plan],
        )
    return db, int(source_recipe["id"]), variant_id


def _user_security_state(db: Database) -> tuple:
    with db.conn() as connection:
        row = connection.execute(
            "SELECT password_hash, role, disabled, session_version "
            "FROM users WHERE username='app-review'"
        ).fetchone()
    return tuple(row)


def test_review_refresh_upgrades_existing_instance_and_is_idempotent(tmp_path: Path):
    inputs = _review_inputs(tmp_path)
    db, source_recipe_id, variant_id = _prepare_legacy_review_state(inputs)
    backup_dir = tmp_path / "refresh-backups"
    security_before = _user_security_state(db)
    recipe_ids_before = {
        int(recipe["id"])
        for recipe in db.recipe_list(include_deleted=False, limit=100)
    }

    first = refresh_app_review_demo(
        db_path=inputs["db_path"],
        recipe_root=inputs["recipe_root"],
        config_path=inputs["config_path"],
        backup_dir=backup_dir,
        public_url=inputs["public_url"],
        hostname=inputs["hostname"],
        today=date(2026, 8, 30),
    )
    second = refresh_app_review_demo(
        db_path=inputs["db_path"],
        recipe_root=inputs["recipe_root"],
        config_path=inputs["config_path"],
        backup_dir=backup_dir,
        public_url=inputs["public_url"],
        hostname=inputs["hostname"],
        today=date(2026, 8, 30),
    )

    assert first["changed"] is True
    assert first["url_changed"] is True
    assert first["snapshots_changed"] is True
    assert first["meal_plan_changed"] is True
    assert second["changed"] is False
    assert first["backup"]["verified"] is True
    assert second["backup"]["verified"] is True
    backups = sorted(backup_dir.glob("app-review-refresh-*.db"))
    assert len(backups) == 2
    if os.name != "nt":
        assert backup_dir.stat().st_mode & 0o777 == 0o700
        assert all(backup.stat().st_mode & 0o777 == 0o600 for backup in backups)
    for backup in backups:
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchone() is None

    with sqlite3.connect(first["backup"]["dest"]) as legacy_backup:
        assert legacy_backup.execute(
            "SELECT url FROM recipes WHERE id=?", (source_recipe_id,)
        ).fetchone()[0] == LEGACY_SOURCE_URL
        assert legacy_backup.execute(
            "SELECT COUNT(*) FROM recipe_source_snapshots WHERE recipe_id=?",
            (source_recipe_id,),
        ).fetchone()[0] == 0
        assert legacy_backup.execute(
            "SELECT COUNT(*) FROM meal_plan_entries"
        ).fetchone()[0] == 4

    assert _user_security_state(db) == security_before
    recipe_ids_after = {
        int(recipe["id"])
        for recipe in db.recipe_list(include_deleted=False, limit=100)
    }
    assert recipe_ids_after == recipe_ids_before
    assert variant_id in recipe_ids_after
    assert db.recipe_get_by_url(review_source_url())["id"] == source_recipe_id
    source_state = db.recipe_source_snapshot_state(source_recipe_id, review_source_url())
    assert source_state["baseline"]["description_source"] == "recipe-json-ld"
    assert source_state["latest"]["description_source"] == "recipe-json-ld"
    meal_entries = db.meal_plan_entries("2000-01-01", "2100-01-01")
    assert len(meal_entries) == REVIEW_PLAN_WEEKS * len(REVIEW_PLAN_RECIPES)
    planned_dates = sorted({entry["planned_for"] for entry in meal_entries})
    assert len(planned_dates) == REVIEW_PLAN_WEEKS
    assert planned_dates[:2] == ["2026-08-24", "2026-08-31"]
    assert all(
        sum(entry["planned_for"] == planned_date for entry in meal_entries)
        == len(REVIEW_PLAN_RECIPES)
        for planned_date in planned_dates
    )


def test_review_refresh_rolls_back_mutation_and_keeps_verified_backup(
    tmp_path: Path, monkeypatch
):
    inputs = _review_inputs(tmp_path)
    db, source_recipe_id, _variant_id = _prepare_legacy_review_state(inputs)
    original_name = db.recipe_get(source_recipe_id)["name"]
    backup_dir = tmp_path / "refresh-backups"

    def fail_after_write(connection, **_kwargs):
        connection.execute(
            "UPDATE recipes SET name='DARF NICHT BLEIBEN' WHERE id=?",
            (source_recipe_id,),
        )
        raise RuntimeError("erwarteter Testabbruch")

    monkeypatch.setattr(refresh_module, "_apply_transactional_refresh", fail_after_write)
    with pytest.raises(RuntimeError, match="erwarteter Testabbruch"):
        refresh_app_review_demo(
            db_path=inputs["db_path"],
            recipe_root=inputs["recipe_root"],
            config_path=inputs["config_path"],
            backup_dir=backup_dir,
            public_url=inputs["public_url"],
            hostname=inputs["hostname"],
            today=date(2026, 8, 30),
        )

    assert db.recipe_get(source_recipe_id)["name"] == original_name
    backups = list(backup_dir.glob("app-review-refresh-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_review_refresh_refuses_unsafe_config_before_backup(tmp_path: Path):
    inputs = _review_inputs(tmp_path)
    seed_review_demo(**inputs)
    config = yaml.safe_load(inputs["config_path"].read_text(encoding="utf-8"))
    config["ai"]["openai"]["api_key"] = "production-secret"
    inputs["config_path"].write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    backup_dir = tmp_path / "refresh-backups"

    with pytest.raises(RuntimeError, match="nicht bereinigt"):
        refresh_app_review_demo(
            db_path=inputs["db_path"],
            recipe_root=inputs["recipe_root"],
            config_path=inputs["config_path"],
            backup_dir=backup_dir,
            public_url=inputs["public_url"],
            hostname=inputs["hostname"],
        )

    assert not backup_dir.exists()
