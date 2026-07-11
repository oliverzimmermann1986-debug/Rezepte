"""Regressionstests für die einheitliche Rezeptliebe-Oberfläche."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_only_one_application_stylesheet_exists():
    css_files = sorted(p.name for p in STATIC.glob("*.css"))
    assert css_files == ["rezeptliebe.css"]
    html = (STATIC / "index.html").read_text()
    assert '/static/rezeptliebe.css' in html
    assert 'style.css' not in html
    assert 'mobile-first.css' not in html
    assert 'recipe-focus.css' not in html
    assert 'butter-yellow.css' not in html


def test_recipe_library_is_default_and_has_primary_navigation():
    js = (STATIC / "app.js").read_text()
    html = (STATIC / "index.html").read_text()
    assert "page: 'recipes'" in js
    assert "favorites" in js
    assert "Einkaufsliste" in html
    assert "Rezepte" in html
    assert "recipes-searchbar" in html
    assert "recipes-grid" in html


def test_legacy_theme_switcher_is_gone():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    css = (STATIC / "rezeptliebe.css").read_text()
    combined = html + js
    assert "data-theme" not in combined
    assert "themePicker" not in combined
    assert "fonts.googleapis" not in combined
    assert "One coherent Butter Yellow design system" in css


def test_mobile_footer_reserves_content_space_and_is_opaque():
    css = (STATIC / "rezeptliebe.css").read_text()
    assert "--mobile-nav-height" in css
    assert "padding: 14px 12px calc(var(--mobile-nav-height) + env(safe-area-inset-bottom) + 30px)" in css
    assert "background: #fffdf9" in css
    assert "position: fixed" in css
    assert "inset: auto 0 0" in css


def test_manifest_uses_rezeptliebe_brand_and_butter_palette():
    manifest = json.loads((STATIC / "manifest.json").read_text())
    assert manifest["name"] == "Rezeptliebe"
    assert manifest["theme_color"] == "#f5c84f"
    assert manifest["background_color"] == "#fffaf0"
    assert any(shortcut["url"] == "/?tab=recipes" for shortcut in manifest["shortcuts"])
    assert any(shortcut["url"] == "/?tab=favorites" for shortcut in manifest["shortcuts"])


def test_no_removed_remote_sync_feature_remains_in_runtime():
    runtime_files = [
        ROOT / "app" / "static" / "app.js",
        ROOT / "app" / "static" / "index.html",
        ROOT / "app" / "routes" / "api_events.py",
        ROOT / "app" / "routes" / "api_stats.py",
        ROOT / "app" / "routes" / "api_metrics.py",
    ]
    forbidden = ("rclone", "bisync", "quicksync", "backup_progress", "/per-pair")
    for path in runtime_files:
        text = path.read_text().lower()
        for token in forbidden:
            assert token not in text, f"{token!r} remains in {path}"


def test_audit_state_is_safe_before_hidden_page_is_loaded():
    js = (STATIC / "app.js").read_text()
    assert "total_recipes: 0" in js
    assert "exact_duplicates: []" in js
    assert "data_gaps: { no_image: []" in js
    assert "summary: {" in js
