"""Pytest-Fixtures für API-Tests.

Strategy:
- migrate_security wird VOR dem app-Import gepatcht (sonst raised es weil
  Test-Umgebung keinen valid PW-Hash hat). Same für migrate_users_to_db.
- sync_filesystem + ensure_extraction_running werden NOOP gepatcht weil sie
  sonst Production-Recipes vom FS in die Test-DB laden würden (Lazy-Sync
  triggert bei recipe_count==0 → fängt sich alle echten Rezepte ein).
- Singleton get_db() wird auf eine Test-Datenbank umgebogen (pro Test fresh)
- require_auth/require_admin Dependency-Overrides → fachliche API-Tests
  brauchen keinen Login; dedizierte RBAC-Tests entfernen den Admin-Override.
- TestClient von FastAPI für synchrone HTTP-Calls
"""
from pathlib import Path
from tools.test_sandbox import install_test_environment

_TEST_SANDBOX = install_test_environment(Path(__file__).resolve().parents[1])

# WICHTIG: diese Funktionen werden während Import UND TestClient-Lifespan
# temporär ersetzt, weil:
# - app.main ruft die Migrationen beim Lifespan-Start auf
# - api_recipes ruft sync_filesystem() bei jedem list-Endpoint mit count==0 auf
import app.auth as _auth

import app.recipes.indexer as _indexer
REAL_SYNC_FILESYSTEM = _indexer.sync_filesystem
_indexer.sync_filesystem = lambda db=None: {"added": 0, "updated": 0}
_indexer.ensure_extraction_running = lambda **kwargs: False
_indexer.is_extraction_running = lambda: False

import pytest
from fastapi.testclient import TestClient

from app.db import Database
Database.__init__.__defaults__ = (_TEST_SANDBOX / "fallback.db",)
import app.db as db_module
from app.auth import require_admin, require_auth, require_import


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    """Frische Test-Database pro Test."""
    db = Database(tmp_path / "test.db")
    # Singleton-Slot überschreiben damit get_db() unsere Test-DB returnt
    db_module._db = db
    yield db
    db_module._db = None


@pytest.fixture
def client(test_db: Database, monkeypatch) -> TestClient:
    """FastAPI-TestClient mit Auth-Bypass.

    Wichtig: app wird HIER importiert (lazy), damit der Import nicht beim
    Test-Collect läuft — sonst hätten wir Issues mit migrate_security() das
    bei nicht-konfiguriertem Default-PW raised.
    """
    real_migrate_security = _auth.migrate_security
    real_migrate_users = _auth.migrate_users_to_db
    _auth.migrate_security = lambda: None
    _auth.migrate_users_to_db = lambda: None
    try:
        from app import main as app_main
    finally:
        _auth.migrate_security = real_migrate_security
        _auth.migrate_users_to_db = real_migrate_users

    app = app_main.app
    from app.routes import api_auth
    from app.security import LoginRateLimiter
    monkeypatch.setattr(api_auth, "guest_limiter", LoginRateLimiter(max_fails=30, window_sec=300, ban_sec=300))
    actor_limiter = LoginRateLimiter()
    ip_limiter = LoginRateLimiter(max_fails=100)
    for module in (app_main, api_auth):
        monkeypatch.setattr(module, 'login_limiter', actor_limiter)
        monkeypatch.setattr(module, 'login_ip_limiter', ip_limiter)
    real_main_migrate_security = app_main.migrate_security
    real_main_migrate_users = app_main.migrate_users_to_db
    real_pdf_migration = app_main.migrate_pdf_quality_defaults
    app_main.migrate_security = lambda: None
    app_main.migrate_users_to_db = lambda: None
    app_main.migrate_pdf_quality_defaults = lambda: False

    # Auth-Dependencies überschreiben: fachliche Endpoint-Tests laufen als
    # Administrator. Die Security-Suite testet die echten Dependencies separat.
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[require_admin] = lambda: {
        "username": "test-admin",
        "role": "admin",
        "full_access": True,
    }
    app.dependency_overrides[require_import] = lambda: {
        "username": "test-admin", "role": "admin", "full_access": True, "can_import": True,
    }
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        app_main.migrate_security = real_main_migrate_security
        app_main.migrate_users_to_db = real_main_migrate_users
        app_main.migrate_pdf_quality_defaults = real_pdf_migration


def _create_recipe(db: Database, *, name: str, folder_path: str,
                   url: str = None, type: str = "Hauptgericht",
                   category: str = "Test", description: str = None):
    """Helper: erstellt ein Recipe und gibt das Dict zurück."""
    import time
    if url is None:
        url = f"https://test.local/{folder_path.replace('/', '_')}"
    db.recipe_upsert(
        url=url, name=name, type=type, category=category,
        folder_path=folder_path, description=description,
        thumb_filename=None, video_filename=None, source_added_at=time.time(),
    )
    return db.recipe_get_by_folder(folder_path)
