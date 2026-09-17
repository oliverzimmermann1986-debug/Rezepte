"""Support form routing is public; recipe data and private contacts are not."""
import pytest
from app.support import PUBLIC_SUPPORT_URL


@pytest.fixture
def public_client(client, monkeypatch):
    import app.auth as auth
    import app.main as main
    main.app.dependency_overrides.clear()
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(main, "auth_disabled", lambda: False)
    monkeypatch.setenv("REZEPTREGAL_SUPPORT_PORTAL_URL", PUBLIC_SUPPORT_URL)
    return client


def test_support_redirects_publicly_to_form_without_opening_recipes(public_client):
    response = public_client.get("/support", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == PUBLIC_SUPPORT_URL
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert public_client.get("/api/recipes").status_code == 401


@pytest.mark.parametrize("destination", [
    "", "http://support.example.com", "https://user:pass@example.com", "javascript:alert(1)",
    "https://example.com\r\nX: y", "https://[bad", "https://attacker.example/",
    "https://support.zimlab.org.attacker.example/?module=rezeptregal",
    "https://support.zimlab.org/admin", "https://support.zimlab.org/?module=another-app",
    PUBLIC_SUPPORT_URL + "&next=https://attacker.example/", PUBLIC_SUPPORT_URL + "#fragment",
    "https://support.zimlab.org:444/?module=rezeptregal",
])
def test_support_fails_closed_for_missing_or_unsafe_portal(public_client, monkeypatch, destination):
    monkeypatch.setenv("REZEPTREGAL_SUPPORT_PORTAL_URL", destination)
    monkeypatch.setenv("REZEPTREGAL_SUPPORT_EMAIL", "private@operator.example")
    response = public_client.get("/support", follow_redirects=False)
    assert response.status_code == 503
    assert "mailto:" not in response.text
    assert "private@operator.example" not in response.text
    assert "Supportformular" in response.text


def test_support_ignores_untrusted_destination_query(public_client):
    response = public_client.get("/support?next=https://attacker.example/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == PUBLIC_SUPPORT_URL


def test_login_and_privacy_link_to_support(public_client):
    for path in ("/login", "/privacy"):
        response = public_client.get(path)
        assert response.status_code == 200
        assert 'href="/support"' in response.text
        assert "Rezeptregal" in response.text
