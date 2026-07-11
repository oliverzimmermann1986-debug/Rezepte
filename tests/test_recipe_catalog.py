from __future__ import annotations

import json
from pathlib import Path

from app.db import Database
from app.routes import api_recipes


class FakeConfig:
    def __init__(self, recipe_root: Path):
        self.recipe_root = recipe_root

    def get(self, *keys, default=None):
        if keys == ("paths", "recipe_dir"):
            return str(self.recipe_root)
        return default


def test_recipe_catalog_indexes_searches_and_serves_media(tmp_path: Path, monkeypatch):
    root = tmp_path / "rezepte"
    directory = root / "Hauptgericht" / "Pasta" / "Tomatenpasta"
    directory.mkdir(parents=True)
    (directory / "info.json").write_text(json.dumps({
        "name": "Schnelle Tomatenpasta",
        "type": "Hauptgericht",
        "category": "Pasta",
        "description": "Cremige Pasta mit Tomaten und Basilikum",
        "source": "social",
    }), encoding="utf-8")
    (directory / "rezept.mp4").write_bytes(b"video")

    db = Database(tmp_path / "catalog.db")
    url = "https://www.tiktok.com/@cook/video/123"
    db.history_add(url, content_type="recipe", name="Tomatenpasta", target_dir=str(directory))

    monkeypatch.setattr(api_recipes, "get_db", lambda: db)
    monkeypatch.setattr(api_recipes, "get_config", lambda: FakeConfig(root))
    monkeypatch.setattr(api_recipes, "_INDEX_READY", False)

    result = api_recipes.search_recipes(q="basilikum", type="", category="", sort="name", limit=20, offset=0)
    assert result["total"] == 1
    assert result["types"] == ["Hauptgericht"]
    assert result["categories"] == ["Pasta"]
    item = result["items"][0]
    assert item["name"] == "Schnelle Tomatenpasta"
    assert item["media_kind"] == "video"
    assert item["media_url"].endswith("/media")

    detail = api_recipes.recipe_detail(item["id"])
    assert "Cremige Pasta" in detail["description"]
    response = api_recipes.recipe_media(item["id"])
    assert Path(response.path) == directory / "rezept.mp4"


def test_recipe_search_filters_and_stable_ids(tmp_path: Path):
    db = Database(tmp_path / "search.db")
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    db.history_add("mail:one", content_type="recipe", name="Apfelkuchen", target_dir=str(a), recipe_type="Nachspeise", category="Kuchen", description="Apfel Zimt")
    db.history_add("mail:two", content_type="recipe", name="Kartoffelsuppe", target_dir=str(b), recipe_type="Hauptgericht", category="Suppe", description="Kartoffeln Möhren")
    db.history_add("mail:three", content_type="recipe", name="Ölkuchen", target_dir=str(b), recipe_type="Nachspeise", category="Kuchen", description="Mit feinem Öl")

    first = db.recipe_search(query="Kartoffeln", sort="name")
    assert first["total"] == 1 and first["items"][0]["name"] == "Kartoffelsuppe"
    unicode_match = db.recipe_search(query="öl", sort="name")
    assert unicode_match["total"] == 1 and unicode_match["items"][0]["name"] == "Ölkuchen"
    filtered = db.recipe_search(recipe_type="Nachspeise", category="Kuchen")
    assert filtered["total"] == 2 and filtered["items"][0]["item_id"]
    assert db.history_get("mail:one")["item_id"] in {item["item_id"] for item in filtered["items"]}


def test_recipe_media_rejects_symlink_escape(tmp_path: Path, monkeypatch):
    root = tmp_path / "recipes"; directory = root / "Typ" / "Kategorie" / "Name"
    directory.mkdir(parents=True)
    outside = tmp_path / "secret.pdf"; outside.write_bytes(b"secret")
    (directory / "leak.pdf").symlink_to(outside)
    db = Database(tmp_path / "symlink.db")
    db.history_add("mail:symlink", content_type="recipe", name="Name", target_dir=str(directory), recipe_type="Typ", category="Kategorie")
    monkeypatch.setattr(api_recipes, "get_db", lambda: db)
    monkeypatch.setattr(api_recipes, "get_config", lambda: FakeConfig(root))
    entry = db.history_get("mail:symlink")
    assert api_recipes._media_file(entry) is None
