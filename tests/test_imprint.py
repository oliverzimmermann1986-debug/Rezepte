"""Public legal notice and offline native entry points stay available without login."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DETAILS = (
    "Oliver Zimmermann", "c/o COCENTER", "Koppoldstr. 1",
    "86551 Aichach", "impressum@zimlab.org",
)


@pytest.fixture
def public_client(client, monkeypatch):
    import app.auth as auth
    import app.main as main
    main.app.dependency_overrides.clear()
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(main, "auth_disabled", lambda: False)
    monkeypatch.delenv("REZEPTREGAL_SUPPORT_PORTAL_URL", raising=False)
    return client


def test_imprint_is_public_and_does_not_open_private_data(public_client):
    response = public_client.get("/impressum")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "set-cookie" not in response.headers
    for detail in PUBLIC_DETAILS:
        assert detail in response.text
    assert 'href="mailto:impressum@zimlab.org"' in response.text
    assert set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", response.text)) == {"impressum@zimlab.org"}
    assert "tel:" not in response.text
    assert public_client.get("/api/recipes").status_code == 401


@pytest.mark.parametrize("path", ["/login", "/privacy", "/support", "/share"])
def test_public_pages_link_to_imprint(public_client, path):
    response = public_client.get(path)
    assert response.status_code == (503 if path == "/support" else 200)
    assert 'href="/impressum"' in response.text


def test_recipe_share_view_links_to_imprint():
    from app.routes.sharing import _render_print_html
    assert 'href="/impressum"' in _render_print_html({"name": "Test"}, is_share=True)


def test_web_app_and_public_review_source_have_imprint_links():
    for relative in ("app/static/index.html", "app/static/review-source-zitronen-ricotta-pasta.html"):
        assert 'href="/impressum"' in (ROOT / relative).read_text(encoding="utf-8")
    app_html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert app_html.count('href="/impressum"') == 2  # mobile header and desktop sidebar


@pytest.mark.parametrize("relative", [
    "ios-swift/Rezepte/Views/Settings/ImpressumView.swift",
    "native-ios/src/components/impressum.tsx",
])
def test_native_notices_bundle_the_same_public_details(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    for detail in PUBLIC_DETAILS:
        assert detail in source
    assert "mailto:impressum@zimlab.org" in source
    assert set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", source)) == {"impressum@zimlab.org"}
    assert "tel:" not in source
    assert "fetch(" not in source
    assert "useAuth" not in source
    assert "SessionStore" not in source


def test_native_notices_are_reachable_before_and_after_login():
    for relative in ("ios-swift/Rezepte/Views/LoginView.swift", "ios-swift/Rezepte/Views/Settings/SettingsView.swift"):
        assert "ImpressumView()" in (ROOT / relative).read_text(encoding="utf-8")
    for relative in ("native-ios/src/app/login.tsx", "native-ios/src/app/(tabs)/index.tsx"):
        assert "<ImpressumButton />" in (ROOT / relative).read_text(encoding="utf-8")
