from pathlib import Path

from app.core.tiktok_caption import (
    _caption_from_player_payload,
    _fetch_tiktok_player_caption,
    _is_allowed_tiktok_cdn_url,
    _looks_like_tiktok_challenge,
    _metadata_from_player_payload,
    _target_article,
    _tiktok_post_id,
    caption_from_article_text,
    clean_expanded_caption,
    fetch_tiktok_player_metadata,
    is_tiktok_url,
    parse_netscape_cookies,
)
from app.jobs.scraper import ScraperJob


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


def test_tiktok_post_id_supports_photo_video_and_player_urls():
    assert _tiktok_post_id("https://www.tiktok.com/@cook/photo/7675767326981016864") == (
        "7675767326981016864"
    )
    assert _tiktok_post_id("https://www.tiktok.com/@cook/video/7650432038700404000") == (
        "7650432038700404000"
    )
    assert _tiktok_post_id("https://www.tiktok.com/player/v1/123456789") == "123456789"
    assert _tiktok_post_id("https://vm.tiktok.com/ZGdx79trY/") is None
    assert _tiktok_post_id("https://example.org/@cook/photo/123456789") is None


def test_caption_from_player_payload_keeps_complete_photo_recipe():
    caption = _caption_from_player_payload(
        {
            "status_code": 0,
            "item_list": [
                {
                    "desc": (
                        "Cremiger Halloumi-Nudelsalat 🥗\n\n"
                        "Zubereitung:\n1. 200g Joghurt in eine Schüssel geben\n"
                        "2. 2 EL Mayonnaise hinzufügen\n"
                        "12. Alles vermengen und genießen 😋"
                    ),
                    "image_post_info": {"images": [{}, {}]},
                }
            ],
        }
    )

    assert caption.startswith("Cremiger Halloumi-Nudelsalat")
    assert "200g Joghurt" in caption
    assert "12. Alles vermengen" in caption


def test_metadata_from_player_payload_extracts_first_photo():
    metadata = _metadata_from_player_payload(
        {
            "items": [
                {
                    "desc": "Orzo mit Pesto und Cherrytomaten",
                    "image_post_info": {
                        "images": [
                            {
                                "display_image": {
                                    "url_list": [
                                        "https://p16-sign-va.tiktokcdn-eu.com/example.jpeg"
                                    ]
                                }
                            }
                        ]
                    },
                }
            ]
        }
    )

    assert metadata == {
        "description_text": "Orzo mit Pesto und Cherrytomaten",
        "thumbnail_url": "https://p16-sign-va.tiktokcdn-eu.com/example.jpeg",
    }


def test_tiktok_cdn_validation_rejects_lookalikes_and_non_https():
    assert _is_allowed_tiktok_cdn_url("https://p16.tiktokcdn-eu.com/image.jpeg")
    assert not _is_allowed_tiktok_cdn_url("http://p16.tiktokcdn-eu.com/image.jpeg")
    assert not _is_allowed_tiktok_cdn_url("https://tiktokcdn-eu.com.example.org/image.jpeg")


def test_fetch_tiktok_player_metadata_resolves_short_url_and_downloads_photo(monkeypatch):
    import requests

    post_id = "7675767326981016864"
    canonical = f"https://www.tiktok.com/@koch/photo/{post_id}"
    image_url = "https://p16-sign-va.tiktokcdn-eu.com/cover.jpeg"
    calls = []

    class Response:
        def __init__(self, *, url, payload=None, body=b"", headers=None):
            self.url = url
            self._payload = payload
            self.content = body
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        def iter_content(self, _chunk_size):
            yield self.content

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.startswith("https://vm.tiktok.com/"):
            return Response(url=canonical)
        if url.endswith("/player/api/v1/items"):
            return Response(
                url=url,
                payload={
                    "items": [
                        {
                            "desc": "Nudelsalat mit Halloumi",
                            "image_post_info": {
                                "images": [
                                    {"display_image": {"url_list": [image_url]}}
                                ]
                            },
                        }
                    ]
                },
                body=b"{}",
            )
        assert url == image_url
        return Response(
            url=image_url,
            body=b"jpeg-data",
            headers={"content-type": "image/jpeg", "content-length": "9"},
        )

    monkeypatch.setattr(requests, "get", fake_get)

    metadata = fetch_tiktok_player_metadata("https://vm.tiktok.com/ZGdxVLPy2/")

    assert metadata["canonical_url"] == canonical
    assert metadata["description_text"] == "Nudelsalat mit Halloumi"
    assert metadata["thumbnail_bytes"] == b"jpeg-data"
    assert metadata["thumbnail_suffix"] == ".jpg"
    assert len(calls) == 3


def test_fetch_tiktok_player_caption_captures_items_response():
    post_id = "7675767326981016864"
    events = {}
    visited = []

    class Response:
        url = "https://www.tiktok.com/player/api/v1/items?item_ids=" + post_id

        @staticmethod
        def json():
            return {"item_list": [{"desc": "Cremiger Halloumi-Nudelsalat mit Rezept"}]}

    class Page:
        @staticmethod
        def on(name, callback):
            events[name] = callback

        @staticmethod
        def goto(url, **_kwargs):
            visited.append(url)
            events["response"](Response())

        @staticmethod
        def wait_for_timeout(_milliseconds):
            pass

        @staticmethod
        def remove_listener(name, callback):
            assert events[name] is callback

    caption = _fetch_tiktok_player_caption(Page(), post_id, 5_000)

    assert caption == "Cremiger Halloumi-Nudelsalat mit Rezept"
    assert visited == [
        "https://www.tiktok.com/player/v1/7675767326981016864?description=1"
    ]


def test_fetch_tiktok_player_caption_fails_closed_and_removes_listener():
    events = {}
    removed = []

    class Page:
        @staticmethod
        def on(name, callback):
            events[name] = callback

        @staticmethod
        def goto(_url, **_kwargs):
            raise RuntimeError("blocked")

        @staticmethod
        def remove_listener(name, callback):
            removed.append((name, callback))

    assert _fetch_tiktok_player_caption(Page(), "123456789", 5_000) == ""
    assert removed == [("response", events["response"])]


def test_tiktok_challenge_detection_covers_slider_and_ignores_recipe():
    assert _looks_like_tiktok_challenge("Bewege den Schieberegler, um das Puzzle einzupassen")
    assert _looks_like_tiktok_challenge("Slide to fit the puzzle")
    assert not _looks_like_tiktok_challenge("Zutaten und Zubereitung für Nudelsalat")


def test_target_article_matches_direct_tiktok_photo_post():
    selectors = []
    expected = object()

    class Candidate:
        first = expected

        @staticmethod
        def count():
            return 1

    class Page:
        @staticmethod
        def locator(selector):
            selectors.append(selector)
            return Candidate()

    article = _target_article(
        Page(),
        "https://www.tiktok.com/@koch/photo/7650432038700404000",
    )

    assert article is expected
    assert selectors == [
        'article:has(a[href*="/photo/7650432038700404000"])',
    ]


def test_first_import_prefers_expanded_caption_for_tiktok_photo(
    tmp_path: Path, monkeypatch
):
    import app.core.tiktok_caption as tiktok_caption

    url = "https://www.tiktok.com/@koch/photo/7650432038700404000"
    short_caption = "Kurze Caption"
    long_caption = (
        "Kartoffelauflauf für vier Personen.\n\n"
        "Zutaten: 1 kg Kartoffeln, 250 ml Sahne und 150 g Käse.\n"
        "Zubereitung: Kartoffeln schneiden, schichten und 45 Minuten backen."
    )
    calls = []

    class Downloader:
        cookies_file = str(tmp_path / "cookies.txt")

        @staticmethod
        def refresh_metadata(_url):
            return {"description_text": short_caption}

    class Config:
        @staticmethod
        def get(*keys, default=None):
            if keys == ("ytdlp",):
                return {
                    "expanded_tiktok_caption": True,
                    "browser_timeout_seconds": 12,
                }
            return default

    monkeypatch.setattr(
        tiktok_caption,
        "fetch_tiktok_player_metadata",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_expanded_tiktok_caption",
        lambda fetched_url, **kwargs: calls.append((fetched_url, kwargs)) or long_caption,
    )
    job = object.__new__(ScraperJob)
    job.cfg = Config()
    job.downloader = Downloader()
    job._fetch_description_via_ytdlp = lambda _url: None

    metadata = job._fetch_external_link_metadata(url)

    assert metadata["description_text"] == long_caption
    assert metadata["description_source"] == "tiktok-browser"
    assert calls == [
        (
            url,
            {
                "fallback_text": short_caption,
                "cookies_file": Downloader.cookies_file,
                "timeout_seconds": 12,
                "executable_path": None,
            },
        )
    ]


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
        "fetch_tiktok_player_metadata",
        lambda *_args, **_kwargs: {},
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


def test_rescrape_restores_tiktok_photo_thumbnail(
    client, test_db, tmp_path: Path, monkeypatch
):
    from io import BytesIO

    from PIL import Image

    import app.core.downloader as downloader
    import app.core.tiktok_caption as tiktok_caption
    import app.routes.api_recipes as api_recipes

    folder = tmp_path / "photo-recipe"
    folder.mkdir()
    url = "https://vm.tiktok.com/ZGdxVLPy2/"
    recipe_id = test_db.recipe_upsert(
        url=url,
        name="Nudelsalat mit Halloumi",
        type="Hauptgericht",
        category="Vegetarisch",
        folder_path=str(folder),
        description="Nudelsalat mit Halloumi",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    image_buffer = BytesIO()
    Image.new("RGB", (120, 160), (90, 140, 70)).save(image_buffer, format="JPEG")

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
        lambda self, _url: {},
    )
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_tiktok_player_metadata",
        lambda *_args, **_kwargs: {
            "description_text": "Nudelsalat mit Halloumi",
            "thumbnail_bytes": image_buffer.getvalue(),
            "thumbnail_suffix": ".jpg",
        },
    )
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_expanded_tiktok_caption",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Browser-Fallback darf für vollständigen Fotopost nicht laufen")
        ),
    )

    response = client.post(f"/api/recipes/{recipe_id}/rescrape")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["thumbnail_updated"] is True
    assert body["description_source"] == "tiktok-player"
    assert test_db.recipe_get(recipe_id)["thumb_filename"] == "thumb.jpg"
    with Image.open(folder / "thumb.jpg") as restored:
        assert restored.format == "JPEG"
        assert restored.size == (120, 160)


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
    import app.core.tiktok_caption as tiktok_caption
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
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_tiktok_player_metadata",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        tiktok_caption,
        "fetch_expanded_tiktok_caption",
        lambda _url, **_kwargs: description,
    )
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", lambda: True)

    response = client.post(f"/api/recipes/{recipe_id}/rescrape?reanalyze=true")

    assert response.status_code == 200
    body = response.json()
    assert body["description_updated"] is False
    assert body["ingredients_queued"] is True
    assert body["worker_started"] is True
    assert test_db.recipe_get(recipe_id)["ingredients_status"] == "pending"
