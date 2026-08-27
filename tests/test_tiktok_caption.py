from pathlib import Path

from app.core.tiktok_caption import (
    caption_from_article_text,
    clean_expanded_caption,
    is_tiktok_url,
    parse_netscape_cookies,
)


def test_caption_from_article_text_uses_page_title_as_ui_boundary():
    article = """Lohmar · Rhein-Sieg-Kreis
bbqdad1985
Hackfleisch Käse Lauch Pasta
#bbqdad
mehr
00:03 / 01:48
Hackfleisch-Käse-Lauch-Pasta Rezept – einfach & schnell
Lead
Ein schnelles Familiengericht.

Zutaten (als Orientierung)
- 400 g Hackfleisch
- 300 g Pasta

Zubereitung
1. Pasta kochen.
Dies ist eine KI-generierte Zusammenfassung des Inhalts. Feedback und Hilfe – TikTok
weniger
108.5K
"""

    caption = caption_from_article_text(
        article,
        "Hackfleisch-Käse-Lauch-Pasta Rezept – einfach & schnell | TikTok",
    )

    assert caption.startswith("Hackfleisch-Käse-Lauch-Pasta Rezept")
    assert "400 g Hackfleisch" in caption
    assert "Lohmar" not in caption
    assert "108.5K" not in caption
    assert "KI-generierte Zusammenfassung" not in caption


def test_clean_expanded_caption_keeps_recipe_and_removes_ui_notice():
    raw = """Hackfleisch Käse Lauch Pasta #bbqdad

Hackfleisch-Käse-Lauch-Pasta Rezept – einfach & schnell
Zutaten (als Orientierung)
- 400 g Hackfleisch
- 1–2 Stangen Lauch

Zubereitung
1. Pasta kochen.

ⓘ Dies ist eine KI-generierte Zusammenfassung des Inhalts. Sie soll keinen faktischen Kontext bereitstellen.
Feedback und Hilfe
weniger
"""

    cleaned = clean_expanded_caption(raw)

    assert "Zutaten (als Orientierung)" in cleaned
    assert "1. Pasta kochen." in cleaned
    assert "KI-generierte Zusammenfassung" not in cleaned
    assert "Feedback und Hilfe" not in cleaned
    assert not cleaned.endswith("weniger")


def test_parse_netscape_cookies_supports_http_only(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".tiktok.com\tTRUE\t/\tTRUE\t1893456000\tttwid\tplain\n"
        "#HttpOnly_.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n",
        encoding="utf-8",
    )

    cookies = parse_netscape_cookies(str(cookie_file))

    assert cookies[0]["name"] == "ttwid"
    assert cookies[0]["expires"] == 1893456000
    assert cookies[1]["name"] == "sessionid"
    assert cookies[1]["httpOnly"] is True
    assert "expires" not in cookies[1]


def test_tiktok_url_detection_rejects_lookalike_hosts():
    assert is_tiktok_url("https://www.tiktok.com/@cook/video/123")
    assert is_tiktok_url("https://m.tiktok.com/v/123")
    assert not is_tiktok_url("https://tiktok.com.example.org/@cook/video/123")
    assert not is_tiktok_url("https://example.org/video/123")


def test_rescrape_prefers_expanded_caption_and_queues_extraction(
    client, test_db, tmp_path: Path, monkeypatch
):
    folder = tmp_path / "recipe"
    folder.mkdir()
    url = "https://www.tiktok.com/@bbqdad1985/video/7650432038700404000"
    recipe_id = test_db.recipe_upsert(
        url=url,
        name="Hackfleisch Käse Lauch Pasta",
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description="",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    test_db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [{"name": "Alt", "raw": "Alt"}],
    )

    import app.core.downloader as downloader
    import app.core.tiktok_caption as tiktok_caption
    import app.routes.api_recipes as api_recipes

    class FakeConfig:
        def get(self, *keys, default=None):
            values = {
                ("ytdlp",): {
                    "binary": "yt-dlp",
                    "expanded_tiktok_caption": True,
                    "browser_timeout_seconds": 12,
                },
                    ("paths", "temp_dir"): str(tmp_path / "temp"),
                    ("paths", "recipe_dir"): str(tmp_path),
            }
            return values.get(keys, default)

    long_caption = (
        "Hackfleisch-Käse-Lauch-Pasta Rezept\n\n"
        "Zutaten\n- 400 g Hackfleisch\n- 300 g Pasta\n- 2 Stangen Lauch\n\n"
        "Zubereitung\n1. Pasta kochen.\n2. Hackfleisch anbraten und alles vermengen.\n"
        "Das ist die vollständig aufgeklappte Caption aus der TikTok-Seite."
    )
    browser_calls = []

    monkeypatch.setattr(api_recipes, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(
        downloader.VideoDownloader,
        "refresh_metadata",
        lambda self, scraped_url: {"description_text": "Kurze Caption"},
    )
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_expanded_tiktok_caption",
        lambda scraped_url, **kwargs: browser_calls.append((scraped_url, kwargs)) or long_caption,
    )
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", lambda: True)

    response = client.post(f"/api/recipes/{recipe_id}/rescrape")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["description_updated"] is True
    assert body["description_source"] == "tiktok-browser"
    assert body["ingredients_queued"] is True
    assert body["worker_started"] is True
    assert browser_calls[0][0] == url
    assert browser_calls[0][1]["fallback_text"] == "Kurze Caption"

    updated = test_db.recipe_get(recipe_id)
    assert updated["description"] == long_caption
    assert updated["ingredients_status"] == "pending"
    assert (folder / "description.txt").read_text(encoding="utf-8") == long_caption
    # Existing data stays visible until the successful worker result replaces it.
    assert test_db.recipe_ingredients_get(recipe_id)[0]["name"] == "Alt"


def test_rescrape_reanalyze_queues_unchanged_description(
    client, test_db, tmp_path: Path, monkeypatch
):
    folder = tmp_path / "unchanged-recipe"
    folder.mkdir()
    description = "Zutaten: 400 g Hackfleisch und 300 g Pasta. Zubereitung: Alles kochen."
    recipe_id = test_db.recipe_upsert(
        url="https://www.tiktok.com/@koch/video/123456789",
        name="Unverändert",
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description=description,
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    test_db.recipe_set_extraction_result(recipe_id, "ok", [])

    import app.core.downloader as downloader
    import app.routes.api_recipes as api_recipes

    class FakeConfig:
        def get(self, *keys, default=None):
            values = {
                ("ytdlp",): {"binary": "yt-dlp", "expanded_tiktok_caption": True},
                    ("paths", "temp_dir"): str(tmp_path / "temp"),
                    ("paths", "recipe_dir"): str(tmp_path),
            }
            return values.get(keys, default)

    monkeypatch.setattr(api_recipes, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(
        downloader.VideoDownloader,
        "refresh_metadata",
        lambda self, scraped_url: {"description_text": description},
    )
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", lambda: True)

    response = client.post(f"/api/recipes/{recipe_id}/rescrape?reanalyze=true")

    assert response.status_code == 200
    body = response.json()
    assert body["description_updated"] is False
    assert body["ingredients_queued"] is True
    assert body["worker_started"] is True
    assert test_db.recipe_get(recipe_id)["ingredients_status"] == "pending"
