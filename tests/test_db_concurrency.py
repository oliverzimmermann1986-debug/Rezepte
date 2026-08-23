import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db import CURRENT_SCHEMA_VERSION, Database


def test_overlapping_initializers_create_one_verified_migration_backup(tmp_path):
    path = tmp_path / "recipes.db"
    initial = Database(path)
    with initial.conn() as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            (CURRENT_SCHEMA_VERSION,),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        databases = list(executor.map(lambda _index: Database(path), range(4)))

    assert len(databases) == 4
    backups = list((tmp_path / "backups").glob(
        f"pre-migration-v*-to-v{CURRENT_SCHEMA_VERSION}-*.db"
    ))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION - 10
    with sqlite3.connect(path) as current:
        assert current.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_newer_database_schema_refuses_application_downgrade(tmp_path):
    path = tmp_path / "future.db"
    database = Database(path)
    with database.conn() as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (999, 'future', 0)"
        )

    with pytest.raises(RuntimeError, match="neuer als diese Anwendung"):
        Database(path)


def test_cooking_completion_dedupe_table_is_recreated_on_upgrade(tmp_path):
    path = tmp_path / "legacy-cooking.db"
    database = Database(path)
    with database.conn() as connection:
        connection.execute("DROP TABLE recipe_cooking_completion_requests")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            (CURRENT_SCHEMA_VERSION,),
        )

    Database(path)

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(recipe_cooking_completion_requests)"
            ).fetchall()
        }
        assert columns == {
            "recipe_id",
            "username",
            "idempotency_key",
            "servings",
            "history_id",
            "created_at",
        }
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_concurrent_recipe_upserts_converge_on_one_row(tmp_path):
    database = Database(tmp_path / "upsert.db")

    def upsert(index: int) -> int:
        return database.recipe_upsert(
            url="https://example.test/same",
            name=f"Rezept {index}",
            type="Hauptgericht",
            category="Test",
            folder_path="/tmp/concurrent-upsert",
            description="Beschreibung",
            thumb_filename=None,
            video_filename=None,
            source_added_at=float(index),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(upsert, range(20)))

    assert len(set(ids)) == 1
    with database.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1


def test_concurrent_recipe_versions_get_unique_monotonic_numbers(tmp_path):
    database = Database(tmp_path / "versions.db")
    recipe_id = database.recipe_upsert(
        url="https://example.test/versioned",
        name="Versioniert",
        type="Hauptgericht",
        category="Test",
        folder_path="/tmp/versioned",
        description="Beschreibung",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        version_ids = list(executor.map(
            lambda index: database.recipe_version_create(
                recipe_id,
                reason=f"Änderung {index}",
            ),
            range(20),
        ))

    assert all(version_ids)
    versions = database.recipe_versions_list(recipe_id, limit=100)
    assert sorted(version["version_no"] for version in versions) == list(range(1, 21))
    snapshots = [database.recipe_version_get(version_id)["snapshot"] for version_id in version_ids]
    assert all(json.loads(json.dumps(snapshot))["recipe"]["id"] == recipe_id for snapshot in snapshots)
