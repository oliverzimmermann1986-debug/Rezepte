from pathlib import Path


def _empty_recipe(test_db, folder: Path, *, url):
    folder.mkdir()
    return test_db.recipe_upsert(
        url=url,
        name=folder.name,
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description="Eine ausreichend lange Beschreibung ohne extrahierte Zutaten.",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )


def test_audit_exposes_all_empty_recipes_but_only_url_candidates(
    client, test_db, tmp_path: Path
):
    with_url = _empty_recipe(
        test_db,
        tmp_path / "Mit URL",
        url="https://www.tiktok.com/@cook/video/123",
    )
    without_url = _empty_recipe(test_db, tmp_path / "Ohne URL", url=None)

    response = client.get("/api/audit")

    assert response.status_code == 200
    body = response.json()
    listed_ids = {recipe["id"] for recipe in body["empty_recipes"]}
    assert {with_url, without_url} <= listed_ids
    assert body["empty_rescrape_ids"] == [with_url]
    assert body["summary"]["empty_recipe_count"] == 2
    assert body["summary"]["empty_rescrape_count"] == 1
