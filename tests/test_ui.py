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
    assert "new Set(['recipes','cart','admin'])" in js
    assert '<span class="nav-label">Favoriten</span>' not in html
    assert html.count('class="nav-item nav-primary"') == 3
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
    assert any(shortcut["url"] == "/?tab=cart" for shortcut in manifest["shortcuts"])
    assert not any(shortcut["url"] == "/?tab=favorites" for shortcut in manifest["shortcuts"])


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


def test_admin_center_uses_private_tile_navigation():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    assert "Administration" in html
    for label in ("Importzentrale", "Versionen", "PDF &amp; Scan", "Suche", "Wartung"):
        assert label in html
    assert "page==='admin'" in html
    assert "['recipes','cart','admin']" in js.replace(" ", "")
    assert "admin-home-grid" in html
    assert ".admin-home-tile" in css
    assert "Privater Admin-Bereich" in html
    assert "Multi-User-Login" not in html
    assert ".maintenance-grid" in css


def test_mobile_admin_and_footer_have_reserved_space():
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    assert "@media (max-width: 900px)" in css
    assert ".admin-two-col { grid-template-columns: 1fr; }" in css
    assert "var(--mobile-nav-height)" in css


def test_admin_center_has_three_item_mobile_entry_and_pdf_quality_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert html.count('class="nav-item nav-primary"') == 3
    assert '<span class="nav-label">Admin</span>' in html
    assert "mobile-admin-button" not in html
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
    assert 'initial_admin_tab="home"' in main_py
    assert 'initial_admin_tab="pdf"' in main_py
    assert "document.body?.dataset?.initialPage" in js
    assert "window.location.pathname.startsWith('/admin')" in js
    assert "params.get('tab') || routePage" in js
    assert "params.get('section')" in js
    assert "rezepte-static-v1.2.9-logout-control" in sw
    assert "caches.delete" in sw
    assert "request.mode === 'navigate'" in sw
    assert "fetch(event.request, {cache: 'no-store'})" in sw


def test_logout_controls_work_without_javascript_on_desktop_and_mobile():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    assert html.count('method="get" action="/logout"') == 2
    assert 'class="mobile-logout-button"' in html
    assert 'class="sidebar-logout-button"' in html
    assert 'href="/logout"' not in html
    assert ".mobile-logout-button" in css
    assert ".sidebar-logout-button" in css


def test_admin_ui_is_private_and_has_no_user_management():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    users_api = (ROOT / "app" / "routes" / "api_users.py").read_text(encoding="utf-8")
    assert "Privater Admin-Bereich" in html
    assert "Benutzer-Verwaltung" not in html
    assert "loadUsers" not in js
    assert "createUser" not in js
    assert "role: Optional[str]" not in users_api


def test_refined_recipe_filters_and_shopping_list_match_mockup():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "recipes-quick-filters" in html
    assert 'aria-label="Kategorie filtern"' not in html
    assert 'aria-label="Typ filtern"' not in html
    assert "Mehrere Zutaten möglich" in html
    assert "Nur Favoriten anzeigen" in html
    assert "📦 Senden" not in html
    assert "📋 Export" not in html
    assert "pushToEinkauf" not in js
    assert "exportCart" not in js
    for unit in ('value="g"', 'value="kg"', 'value="ml"', 'value="l"'):
        assert unit in html
    assert ">Bestätigen</span>" in html


def test_failed_imports_can_be_discarded_from_import_center():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    pending_api = (ROOT / "app" / "routes" / "api_pending.py").read_text(encoding="utf-8")
    assert "Verwerfen" in html
    assert "discardFailedDownload(f.url)" in html
    assert "discardingUrl === f.url" in html
    assert "async discardFailedDownload(url)" in js
    assert "/discard" in pending_api
    assert "download_failure_clear(url)" in pending_api


def test_external_shopping_and_recurring_ui_are_available():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "rezepte.css").read_text(encoding="utf-8")
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    config_example = (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8")

    assert "api_einkauf" in main_py
    assert "Wiederkehrend" in html
    assert "Fällige jetzt eintragen" in html
    assert 'x-model="config.einkauf.api_url"' in html
    assert 'x-model="config.einkauf.app_token"' in html
    assert "/api/einkauf/recurring" in js
    assert "async recSave()" in js
    assert "async recRunNow()" in js
    assert ".recurring-form" in css
    assert "app_token:" in config_example


def test_ingredients_can_be_excluded_from_shopping():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    updater = (ROOT / "proxmox" / "update-local.sh").read_text(encoding="utf-8")

    assert "Nicht einkaufen" in html
    assert "setShoppingExclusion(c, $event.target.checked)" in html
    assert "async setShoppingExclusion(can, excluded)" in js
    assert "/shopping-exclusion" in js
    assert 'chmod 0755 "$APP_DIR"' in updater


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
