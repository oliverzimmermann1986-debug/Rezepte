"""Der externe FTS5-Index wird bei Inhaltsabweichungen selbst repariert."""

from app.db import Database
from tests.conftest import _create_recipe


def test_startup_rebuilds_external_fts_when_index_is_empty(test_db):
    recipe = _create_recipe(
        test_db,
        name="Zitronenpasta",
        folder_path="/tmp/fts-recovery",
        description="Pasta mit Zitronenschale und Parmesan",
    )
    with test_db.conn() as connection:
        connection.execute(
            "INSERT INTO recipes_fts(recipes_fts, rowid, name, description, type, category) "
            "VALUES ('delete', ?, ?, ?, ?, ?)",
            (
                recipe["id"], recipe["name"], recipe["description"],
                recipe["type"], recipe["category"],
            ),
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM recipes_fts_docsize"
        ).fetchone()[0] == 0
        # External-content SELECT spiegelt trotzdem recipes und hätte den alten
        # COUNT(*)-Backfill fälschlich übersprungen.
        assert connection.execute("SELECT COUNT(*) FROM recipes_fts").fetchone()[0] == 1

    reopened = Database(test_db.path)
    with reopened.conn() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM recipes_fts_docsize"
        ).fetchone()[0] == 1
    assert [item["id"] for item in reopened.recipe_list(search="Zitronenpasta")] == [recipe["id"]]


def test_fts_update_trigger_only_tracks_searchable_columns(test_db):
    with test_db.conn() as connection:
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='recipes_fts_au'"
        ).fetchone()[0]
    normalized = " ".join(definition.split()).lower()
    assert "after update of name, description, type, category on recipes" in normalized

