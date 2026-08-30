from types import SimpleNamespace

from app.core import recipe_web


def test_recipe_url_normalization_supports_new_sources_and_blocks_lookalikes():
    assert recipe_web.normalize_recipe_url("https://youtu.be/dQw4w9WgXcQ?si=secret") == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert recipe_web.normalize_recipe_url("https://www.pinterest.com/pin/123456789/") == (
        "https://www.pinterest.com/pin/123456789/"
    )
    assert recipe_web.normalize_recipe_url(
        "https://rezepte.example/pasta?utm_source=test&portionen=4#zutaten"
    ) == "https://rezepte.example/pasta?portionen=4"
    assert recipe_web.normalize_recipe_url("https://instagram.com.evil.example/reel/123") is None
    assert recipe_web.normalize_recipe_url("https://127.0.0.1/rezept") is None
    assert recipe_web.normalize_recipe_url("http://rezepte.example/pasta") is None


def test_json_ld_recipe_page_is_extracted(monkeypatch):
    html = """
    <html><head>
      <link rel="canonical" href="https://koch.example/rezepte/suppe?utm_source=x">
      <script type="application/ld+json">{
        "@context":"https://schema.org", "@type":"Recipe",
        "name":"Kartoffelsuppe", "recipeYield":"4 Portionen",
        "recipeIngredient":["500 g Kartoffeln", "1 l Brühe"],
        "recipeInstructions":[{"@type":"HowToStep","text":"20 Minuten kochen."}]
      }</script>
    </head></html>
    """

    def fake_request(url, *, max_bytes, accept):
        assert max_bytes == recipe_web._MAX_HTML_BYTES
        return (
            SimpleNamespace(
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=html,
                content=html.encode(),
            ),
            url,
        )

    monkeypatch.setattr(recipe_web, "_request_following_public_redirects", fake_request)
    result = recipe_web.extract_recipe_web_metadata("https://koch.example/rezepte/suppe")
    assert result["canonical_url"] == "https://koch.example/rezepte/suppe"
    assert result["page_title"] == "Kartoffelsuppe"
    assert "500 g Kartoffeln" in result["description_text"]
    assert "20 Minuten kochen" in result["description_text"]
    assert result["description_source"] == "recipe-json-ld"


def test_source_check_can_skip_thumbnail_download(monkeypatch):
    html = """
    <html><head>
      <meta property="og:title" content="Pasta">
      <meta property="og:description" content="Ein vollständiger Rezepttext">
      <meta property="og:image" content="https://koch.example/cover.jpg">
    </head></html>
    """

    def fake_request(url, *, max_bytes, accept):
        assert max_bytes == recipe_web._MAX_HTML_BYTES
        return (
            SimpleNamespace(
                status_code=200,
                headers={"content-type": "text/html"},
                text=html,
                content=html.encode(),
            ),
            url,
        )

    monkeypatch.setattr(recipe_web, "_request_following_public_redirects", fake_request)
    result = recipe_web.extract_recipe_web_metadata(
        "https://koch.example/pasta", include_thumbnail=False
    )

    assert result["page_title"] == "Pasta"
    assert result["thumbnail_bytes"] is None
    assert result["thumbnail_suffix"] is None
