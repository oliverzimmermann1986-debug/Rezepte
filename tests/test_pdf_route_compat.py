"""Regressionen gegen gemischte Frontend-/Backend-Versionen."""
from pathlib import Path

from app import __version__


def test_pdf_admin_routes_are_registered():
    from app.main import app
    # FastAPI >=0.141 hält include_router intern verschachtelt. Die öffentliche
    # OpenAPI-Sicht ist die stabile Aussage darüber, welche Routen registriert
    # und dokumentiert sind.
    paths = set(app.openapi()["paths"])
    assert "/api/admin/pdf/preflight" in paths
    assert "/api/admin/pdf/process" in paths
    assert "/api/admin/pdf/jobs/active" in paths
    assert "/api/admin/pdf/jobs/{run_id}" in paths
    assert "/api/system/info" in paths


def test_health_and_system_info_report_build_version(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["version"] == __version__
    assert "ai-shopping-optimization" in health.json()["capabilities"]
    assert "shopping-categories" in health.json()["capabilities"]
    assert "native-admin-roles" in health.json()["capabilities"]
    assert "native-admin-config-v1" in health.json()["capabilities"]
    assert "pdf-preflight" in health.json()["capabilities"]
    assert "recurring-shopping" in health.json()["capabilities"]
    assert "weekly-meal-plan" in health.json()["capabilities"]
    assert "weekly-meal-plan-pdf" in health.json()["capabilities"]
    assert "recipe-pdf-export" in health.json()["capabilities"]
    assert "meal-conductor-v1" in health.json()["capabilities"]
    assert "source-integrity-v2" in health.json()["capabilities"]
    assert "substitution-lab-v1" in health.json()["capabilities"]

    info = client.get("/api/system/info")
    assert info.status_code == 200
    assert info.json()["version"] == __version__
    assert "pdf-background-jobs" in info.json()["capabilities"]
    assert "einkauf-proxy" in info.json()["capabilities"]


def test_logout_clears_browser_state_and_redirects_to_login(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "auth_disabled", lambda: False)
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["clear-site-data"] == '"cache", "storage"'
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_browser_logout_revokes_server_sessions(client, monkeypatch):
    import app.main as main

    revoked = []

    class FakeDb:
        def user_revoke_sessions(self, username):
            revoked.append(username)
            return True

    monkeypatch.setattr(main, "auth_disabled", lambda: False)
    monkeypatch.setattr(main, "request_user", lambda _request: "anna")
    monkeypatch.setattr(main, "get_db", lambda: FakeDb())

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert revoked == ["anna"]


def test_logout_delegates_to_cloudflare_when_internal_auth_is_disabled(
    client, monkeypatch
):
    import app.main as main

    monkeypatch.setattr(main, "auth_disabled", lambda: True)
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/cdn-cgi/access/logout"
    assert response.headers["clear-site-data"] == '"cache", "storage"'


def test_deep_health_route_requires_authentication():
    from app.auth import require_auth
    from app.main import app

    route = next(route for route in app.routes if getattr(route, "path", "") == "/healthz/deep")
    assert any(dependency.call is require_auth for dependency in route.dependant.dependencies)


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
    assert "/api/cart/optimize/preview" in updater
    assert "ai-shopping-optimization" in updater
    assert "shopping-categories" in updater
    assert "native-admin-roles" in updater
    assert "EXPECTED_VERSION=" in updater
    assert 'app/__init__.py' in updater
    assert 'HEALTH_VERSION=' in updater
    assert '"$HEALTH_VERSION" != "$EXPECTED_VERSION"' in updater
    assert 'ln -s "$APP_DIR/venv" "$APP_DIR/venv.next"' in updater
    assert '"$APP_DIR/venv/bin/yt-dlp" --version' in updater
    assert '"$APP_DIR/venv/bin/yt-dlp" --list-impersonate-targets' in updater
    assert "grep -vq 'unavailable'" in updater


def test_tiktok_downloader_installs_browser_impersonation_support():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "yt-dlp[default,curl-cffi]==2026.8.19" in requirements.splitlines()
