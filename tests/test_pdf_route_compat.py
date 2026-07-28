"""Regressionen gegen gemischte Frontend-/Backend-Versionen."""
from pathlib import Path


def test_pdf_admin_routes_are_registered():
    from app.main import app
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/admin/pdf/preflight" in paths
    assert "/api/admin/pdf/process" in paths
    assert "/api/admin/pdf/jobs/active" in paths
    assert "/api/admin/pdf/jobs/{run_id}" in paths
    assert "/api/system/info" in paths


def test_health_and_system_info_report_build_version(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["version"] == "1.2.7"
    assert "pdf-preflight" in health.json()["capabilities"]
    assert "recurring-shopping" in health.json()["capabilities"]

    info = client.get("/api/system/info")
    assert info.status_code == 200
    assert info.json()["version"] == "1.2.7"
    assert "pdf-background-jobs" in info.json()["capabilities"]
    assert "einkauf-proxy" in info.json()["capabilities"]


def test_logout_clears_browser_state_and_redirects_to_login(client):
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["clear-site-data"] == '"cache", "storage"'
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_frontend_has_legacy_pdf_fallback():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "legacyMode" in app_js
    assert "backend_restart_required" in app_js
    assert "PDF-Backend fehlt" in app_js
    assert "error.status = r.status" in app_js


def test_local_updater_does_not_git_pull():
    updater = Path("proxmox/update-local.sh").read_text(encoding="utf-8")
    assert "\ngit pull" not in updater
    assert "\n  git pull" not in updater
    assert "rsync -a --delete" in updater
    assert "/api/admin/pdf/preflight" in updater
    assert "EXPECTED_VERSION=" in updater
    assert 'HEALTH_VERSION=' in updater
    assert '"$HEALTH_VERSION" != "$EXPECTED_VERSION"' in updater
