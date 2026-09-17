import pytest
from tools.check_support_url import PUBLIC_SUPPORT_URL, NoRedirects, check_configuration, check_document, check_url


def document():
    return '<title>Zimlab · Support</title><form id="support-form"><input name="email"><input name="subject"><textarea name="message"></textarea></form><a href="/privacy">Datenschutz</a><a href="/impressum">Impressum</a>'


def test_support_release_check_accepts_form_without_exposing_email():
    check_document(document())
    check_configuration({"ready": True, "siteKey": "configured", "modules": [{"slug": "rezeptregal"}]})


@pytest.mark.parametrize("body", ["<title>Login</title>", document().replace('id="support-form"', ''), document().replace('name="email"', ''), document().replace('href="/privacy"', 'href="/login"'), document().replace('href="/impressum"', 'href="/login"'), document() + '<a href="mailto:owner@example.com">Kontakt</a>'])
def test_support_release_check_rejects_incomplete_or_email_page(body):
    with pytest.raises(ValueError):
        check_document(body)


@pytest.mark.parametrize("configuration", [{}, {"ready": False}, {"ready": True, "siteKey": "configured", "modules": []}, {"ready": True, "siteKey": "", "modules": [{"slug": "rezeptregal"}]}, {"ready": True, "siteKey": True, "modules": [{"slug": "rezeptregal"}]}, {"ready": True, "siteKey": "configured", "modules": [{"slug": "rezeptregal", "enabled": False}]}])
def test_support_release_check_rejects_unready_or_disabled_module(configuration):
    with pytest.raises(ValueError):
        check_configuration(configuration)


@pytest.mark.parametrize("url", ["", "http://example.com/support", "https://user:password@example.com/support", "https://support.zimlab.org/", "https://support.zimlab.org/?module=aeon-seals", PUBLIC_SUPPORT_URL + "&next=https://attacker.example/"])
def test_support_release_check_rejects_bad_url_before_network(url):
    with pytest.raises(ValueError):
        check_url(url)


def test_release_url_matches_backend_approved_destination():
    from app.support import PUBLIC_SUPPORT_URL as backend_url
    assert PUBLIC_SUPPORT_URL == backend_url


def test_release_check_rejects_redirects_to_auth_or_other_hosts():
    assert NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://login.example/") is None


def test_release_check_reads_only_canonical_public_endpoints(monkeypatch):
    import tools.check_support_url as checker
    calls = []

    def fetch(url, content_type):
        calls.append((url, content_type))
        body = document() if content_type == "text/html" else '{"ready":true,"siteKey":"configured","modules":[{"slug":"rezeptregal"}]}'
        return url, body

    monkeypatch.setattr(checker, "fetch_public", fetch)
    check_url(PUBLIC_SUPPORT_URL)
    assert calls == [(PUBLIC_SUPPORT_URL, "text/html"), ("https://support.zimlab.org/api/public/config", "application/json")]
