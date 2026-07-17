"""Regressionstests für die einheitliche Rezepte-Oberfläche."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_only_one_application_stylesheet_exists():
    css_files = sorted(p.name for p in STATIC.glob("*.css"))
    assert css_files == ["rezepte.css"]
    html = (STATIC / "index.html").read_text()
    assert '/static/rezepte.css' in html
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
    css = (STATIC / "rezepte.css").read_text()
    combined = html + js
    assert "data-theme" not in combined
    assert "themePicker" not in combined
    assert "fonts.googleapis" not in combined
    assert "One coherent Butter Yellow design system" in css


def test_mobile_footer_reserves_content_space_and_is_opaque():
    css = (STATIC / "rezepte.css").read_text()
    assert "--mobile-nav-height" in css
    assert "padding: 14px 12px calc(var(--mobile-nav-height) + env(safe-area-inset-bottom) + 30px)" in css
    assert "background: #fffdf9" in css
    assert "position: fixed" in css
    assert "inset: auto 0 0" in css


def test_manifest_uses_rezepte_brand_and_butter_palette():
    manifest = json.loads((STATIC / "manifest.json").read_text())
    assert manifest["name"] == "Rezepte"
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


def test_pdf_auto_rotation_settings_are_exposed_with_safe_defaults():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert "PDF-Verarbeitung" in html
    assert 'x-model="config.pdf.auto_rotate"' in html
    assert 'x-model="config.pdf.use_tesseract_osd"' in html
    assert "cfg.pdf ||= {}" in js
    assert "cfg.pdf.auto_rotate = true" in js


def test_admin_center_replaces_fragmented_navigation():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    assert "Administration" in html
    for label in ("Importzentrale", "Versionen", "PDF &amp; Scan", "Suche", "Wartung"):
        assert label in html
    assert "page==='admin'" in html
    assert "['recipes','favorites','cart','admin']" in js.replace(" ", "") or "'recipes','favorites','cart','admin'" in js.replace(" ", "")
    assert ".admin-tabs" in css
    assert ".maintenance-grid" in css


def test_mobile_admin_and_footer_have_reserved_space():
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    assert "@media (max-width: 900px)" in css
    assert ".admin-two-col { grid-template-columns: 1fr; }" in css
    assert "var(--mobile-nav-height)" in css


def test_admin_center_has_direct_mobile_entry_and_pdf_quality_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'aria-label="Admin Center öffnen"' in html
    assert 'Direktaufruf: <code>/admin</code>' in html
    assert 'x-model="admin.pdf.sharpen_scans"' in html
    assert 'x-model.number="admin.pdf.scan_dpi"' in html
    assert "scan_dpi: 300" in js
    assert "limit: 500" in js


def test_admin_uses_real_routes_and_pwa_shell_is_network_first():
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '@app.get("/admin", response_class=HTMLResponse)' in main_py
    assert 'initial_page="admin"' in main_py
    assert 'initial_admin_tab="pdf"' in main_py
    assert "document.body?.dataset?.initialPage" in js
    assert "window.location.pathname.startsWith('/admin')" in js
    assert "rezepte-v1.2.5" in sw
    assert "fetch(event.request, { cache: 'no-store' })" in sw


def test_admin_is_visible_for_every_authenticated_user():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    users_api = (ROOT / "app" / "routes" / "api_users.py").read_text(encoding="utf-8")
    assert 'x-show="session.is_admin"' not in html
    assert "Admin-Rechte erforderlich" not in js
    assert "setUserRole" not in js
    assert "<th>Rolle</th>" not in html
    assert "role: Optional[str]" not in users_api
    assert "Alle angemeldeten Benutzer haben Vollzugriff" in html


def test_pdf_admin_uses_background_jobs_and_preflight():
    from pathlib import Path
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    css = Path("app/static/rezepte.css").read_text(encoding="utf-8")

    assert "/api/admin/pdf/preflight" in js
    assert "/api/admin/pdf/jobs/active" in js
    assert "background: !this.admin.pdf.legacyMode" in js
    assert "PDF-Lauf gestartet" in js
    assert "pdf-progress-track" in html
    assert "Systemprüfung" in html
    assert ".pdf-progress-value" in css
