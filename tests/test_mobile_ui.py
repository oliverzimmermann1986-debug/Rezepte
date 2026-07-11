from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


class SectionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages = set()
        self.nav_labels = []
        self._nav = False
        self._button = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "nav" and "nav-list" in attr.get("class", ""):
            self._nav = True
        if tag == "button" and self._nav:
            self._button = True
        if tag == "section":
            expression = attr.get("x-show", "")
            for page in ("recipes", "import", "pending", "history", "config"):
                if f"page==='{page}'" in expression:
                    self.pages.add(page)

    def handle_endtag(self, tag):
        if tag == "button": self._button = False
        if tag == "nav": self._nav = False

    def handle_data(self, data):
        if self._button and data.strip(): self.nav_labels.append(data.strip())


def test_recipe_search_is_the_primary_mobile_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    parser = SectionParser(); parser.feed(html)
    assert 'x-data="recipeApp()"' in html
    assert "Was möchtest du kochen?" in html
    assert 'class="recipe-search-main"' in html
    assert 'class="recipe-grid"' in html
    assert parser.pages == {"recipes", "import", "pending", "history", "config"}
    assert parser.nav_labels[0] == "Rezepte"
    assert "Rclone" not in html and "rclone" not in html
    assert "scraperProgress ? formatDuration(scraperProgress.elapsed_sec) : '—'" in html
    assert "in (maintenance && maintenance.tiers ? maintenance.tiers : {})" in html


def test_frontend_uses_recipe_api_and_defaults_to_recipes():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function recipeApp()" in js
    assert "page: 'recipes'" in js
    assert "'/api/recipes" in js or "`/api/recipes" in js
    assert "loadRecipes(reset = true)" in js
    assert "cell.dataset.label" in js
    assert "window.addEventListener('offline'" in js
    assert "rclone" not in js.lower()


def test_mobile_css_covers_recipe_cards_touch_and_safe_areas():
    css = (STATIC / "rezeptliebe.css").read_text(encoding="utf-8")
    assert "env(safe-area-inset-bottom)" in css
    assert "min-height: 44px" in css
    assert ".recipe-grid" in css
    assert ".recipe-card" in css
    assert "@media (max-width: 470px)" in css
    assert "prefers-reduced-motion" in css
    assert "--mobile-nav-clearance" in css
    assert "background: #fffdf8" in css
    assert "recipeFiltersOpen" in (STATIC / "app.js").read_text(encoding="utf-8")
    assert ".recipe-filter-row.is-open" in css
    assert css.count("{") == css.count("}")


def test_manifest_opens_recipe_search():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/#recipes"
    assert manifest["short_name"] == "Rezeptliebe"
    assert not (STATIC / "service-worker.js").exists()


def test_login_uses_recipe_brand_and_mobile_meta():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in source
    assert "Login · Rezeptliebe" in source
    assert 'aria-labelledby="login-title"' in source


def test_butter_yellow_theme_is_loaded_and_old_design_is_removed():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "rezeptliebe.css").read_text(encoding="utf-8")
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert 'content="#f7cf63"' in html
    assert 'content="light"' in html
    assert '/static/rezeptliebe.css' in html
    assert html.count('rel="stylesheet"') == 1
    assert '--accent: #f5c84f' in css
    assert '.recipe-search-submit' in css
    assert '.login-brand' in css
    assert manifest["theme_color"] == "#f7cf63"
    for legacy in ("style.css", "mobile-first.css", "recipe-focus.css", "butter-yellow.css"):
        assert legacy not in html
        assert not (STATIC / legacy).exists()
    assert css.count("{") == css.count("}")
