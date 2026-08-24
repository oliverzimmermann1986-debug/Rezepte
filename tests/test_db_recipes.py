"""Minimal pytest-Suite für DB-Layer. Fokus auf kritische Pfade:
- Recipe-CRUD (Insert, Get, Update, Delete)
- Soft-Delete + Restore
- FTS-Search (Name + Description + Ingredients)
- Favorite-Toggle + Rating
- Filter-Logik (verified, favorite_only, min_rating)

Tests laufen mit einer Temp-DB pro Test (kein Cross-Test-State).
"""
import os
import tempfile
from pathlib import Path

import pytest

from app.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Frische Database in tempdir pro Test."""
    return Database(tmp_path / "test.db")


_upsert_counter = [0]

def _upsert(db, *, name, folder_path, url=None, type="t",
            category="c", description=None):
    """Helper: recipe_upsert mit allen required kwargs.
    Generiert eindeutige URL falls keine angegeben — sonst UNIQUE-Konflikt
    weil recipes.url ein UNIQUE-Index hat."""
    if url is None:
        _upsert_counter[0] += 1
        url = f"https://test.local/r{_upsert_counter[0]}"
    return db.recipe_upsert(
        url=url, name=name, type=type, category=category,
        folder_path=folder_path, description=description,
        thumb_filename=None, video_filename=None, source_added_at=None,
    )


# ─── Recipe-CRUD ────────────────────────────────────────────────────────────

def test_recipe_upsert_and_get(db):
    _upsert(db, name="Test Spargel", folder_path="/tmp/test/spargel-1",
            type="Hauptgericht", category="Spargel",
            description="Mit grünem Spargel und Kartoffeln")
    rec = db.recipe_get_by_folder("/tmp/test/spargel-1")
    assert rec is not None
    assert rec["name"] == "Test Spargel"
    assert rec["type"] == "Hauptgericht"
    assert rec["deleted_at"] is None


def test_recipe_list_excludes_soft_deleted_by_default(db):
    _upsert(db, name="Active", folder_path="/tmp/active")
    _upsert(db, name="Deleted", folder_path="/tmp/deleted")
    rec_deleted = db.recipe_get_by_folder("/tmp/deleted")
    db.recipe_soft_delete(rec_deleted["id"])

    active = db.recipe_list()
    assert len(active) == 1
    assert active[0]["name"] == "Active"

    trash = db.recipe_list(only_deleted=True)
    assert len(trash) == 1
    assert trash[0]["name"] == "Deleted"


def test_recipe_restore_clears_deleted_at(db):
    _upsert(db, name="R", folder_path="/tmp/r")
    rec = db.recipe_get_by_folder("/tmp/r")
    db.recipe_soft_delete(rec["id"])
    assert db.recipe_get(rec["id"])["deleted_at"] is not None

    res = db.recipe_restore(rec["id"])
    assert res["ok"] is True
    assert db.recipe_get(rec["id"])["deleted_at"] is None


# ─── FTS-Search ─────────────────────────────────────────────────────────────

def test_fts_search_by_name(db):
    _upsert(db, name="Spargelsalat", folder_path="/tmp/x1")
    _upsert(db, name="Tomatenpasta", folder_path="/tmp/x2")
    results = db.recipe_list(search="Spargel")
    names = [r["name"] for r in results]
    assert "Spargelsalat" in names
    assert "Tomatenpasta" not in names


def test_fts_search_by_description(db):
    _upsert(db, name="Geheimrezept", folder_path="/tmp/g",
            description="mit viel Knoblauch")
    results = db.recipe_list(search="Knoblauch")
    assert any(r["name"] == "Geheimrezept" for r in results)


# ─── Favorit + Rating ───────────────────────────────────────────────────────

def test_favorite_filter(db):
    _upsert(db, name="A", folder_path="/tmp/a")
    _upsert(db, name="B", folder_path="/tmp/b")
    rec_a = db.recipe_get_by_folder("/tmp/a")
    with db.conn() as c:
        c.execute("UPDATE recipes SET is_favorite=1 WHERE id=?", (rec_a["id"],))

    favs = db.recipe_list(favorite_only=True)
    assert len(favs) == 1
    assert favs[0]["name"] == "A"


def test_min_rating_filter(db):
    _upsert(db, name="1Star", folder_path="/tmp/1")
    _upsert(db, name="3Star", folder_path="/tmp/3")
    _upsert(db, name="5Star", folder_path="/tmp/5")
    with db.conn() as c:
        for path, rating in [("/tmp/1", 1), ("/tmp/3", 3), ("/tmp/5", 5)]:
            c.execute("UPDATE recipes SET rating=? WHERE folder_path=?", (rating, path))

    high = db.recipe_list(min_rating=3)
    names = sorted(r["name"] for r in high)
    assert names == ["3Star", "5Star"]


def test_multi_category_and_exact_rating_filters(db):
    _upsert(db, name="Pasta5", folder_path="/tmp/pasta5", category="Pasta")
    _upsert(db, name="Suppe1", folder_path="/tmp/suppe1", category="Suppe")
    _upsert(db, name="Salat3", folder_path="/tmp/salat3", category="Salat")
    _upsert(db, name="Pasta0", folder_path="/tmp/pasta0", category="Pasta")
    with db.conn() as c:
        for path, rating in [
            ("/tmp/pasta5", 5),
            ("/tmp/suppe1", 1),
            ("/tmp/salat3", 3),
        ]:
            c.execute("UPDATE recipes SET rating=? WHERE folder_path=?", (rating, path))

    selected = db.recipe_list(categories=["Pasta", "Suppe"], ratings=[1, 5])
    assert {recipe["name"] for recipe in selected} == {"Pasta5", "Suppe1"}
    assert db.recipe_count(categories=["Pasta"], ratings=[0, 5]) == 2


# ─── Count konsistent mit List ──────────────────────────────────────────────

def test_recipe_count_matches_list_length(db):
    for i in range(5):
        _upsert(db, name=f"R{i}", folder_path=f"/tmp/r{i}")
    assert db.recipe_count() == 5
    assert len(db.recipe_list()) == 5
    db.recipe_soft_delete(db.recipe_get_by_folder("/tmp/r0")["id"])
    assert db.recipe_count() == 4
    assert db.recipe_count(only_deleted=True) == 1
