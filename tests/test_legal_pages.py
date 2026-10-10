"""Public legal pages must not weaken authentication or publish incomplete data."""
import json

import pytest

from app import legal


@pytest.fixture(autouse=True)
def no_real_operator_configuration(monkeypatch):
    monkeypatch.delenv(legal.CONFIG_ENV, raising=False)


@pytest.fixture
def public_client(client):
    from app.main import app
    app.dependency_overrides.clear()
    client.cookies.clear()
    return client


@pytest.fixture
def operator_data():
    # Synthetic values for tests only; never installed as production defaults.
    return {"name": "Testbetrieb & Küche", "address": "Beispielweg 12\n12345 Teststadt",
            "email": "service@example.test", "additional_information": "Zusätzliche Testangaben"}


def configure(tmp_path, monkeypatch, value):
    path = tmp_path / "operator-private-filename.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(legal.CONFIG_ENV, str(path))
    return path


@pytest.mark.parametrize("route", ["/impressum", "/support", "/privacy"])
def test_complete_legal_pages_are_available_without_authentication(public_client, operator_data, tmp_path, monkeypatch, route):
    configure(tmp_path, monkeypatch, operator_data)
    response = public_client.get(route, follow_redirects=False)
    assert response.status_code == 200
    assert "Testbetrieb &amp; Küche" in response.text
    assert "Beispielweg 12<br>12345 Teststadt" in response.text
    assert 'href="mailto:service@example.test"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "text/html" in response.headers["content-type"]
    for path in ("/impressum", "/privacy", "/support"):
        assert f'href="{path}"' in response.text


@pytest.mark.parametrize("route", ["/impressum", "/support"])
def test_unconfigured_pages_are_explicit_503_and_contain_no_fake_identity(public_client, route):
    response = public_client.get(route)
    assert response.status_code == 503
    assert "Betreiberangaben" in response.text
    assert "mailto:" not in response.text
    assert "SCRAPPER_LEGAL_CONFIG_FILE" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("missing", ["name", "address", "email"])
def test_incomplete_configuration_never_publishes_partial_identity(public_client, operator_data, tmp_path, monkeypatch, missing):
    operator_data.pop(missing)
    path = configure(tmp_path, monkeypatch, operator_data)
    for route in ("/impressum", "/support"):
        response = public_client.get(route)
        assert response.status_code == 503
        assert "Testbetrieb" not in response.text
        assert "example.test" not in response.text
        assert str(path) not in response.text
    privacy = public_client.get("/privacy")
    assert privacy.status_code == 200
    assert "noch keine vollständigen Betreiberangaben" in privacy.text


@pytest.mark.parametrize("field,value", [
    ("name", 123), ("name", "  "), ("name", "line1\nline2"),
    ("address", []), ("address", True), ("address", "x\x00y"),
    ("email", "https://example.test"), ("email", "a@example.test\r\nBcc: victim@example.test"),
    ("email", 'a@example.test" onclick="x'), ("email", "a@localhost"),
    ("email", ".a@example.test"), ("email", "a..b@example.test"),
    ("email", "a@-example.test"), ("email", "a@example..test"),
    ("additional_information", {"html": "unsafe"}),
])
def test_invalid_field_types_and_values_fail_closed(operator_data, tmp_path, monkeypatch, field, value):
    operator_data[field] = value
    configure(tmp_path, monkeypatch, operator_data)
    assert legal.configured_operator() is None


@pytest.mark.parametrize(
    "content",
    [b"not JSON", b"\xff", b"[]", b"null", b'{}', b'{"name":"first","name":"second"}', b"x" * 32769],
    ids=["malformed", "invalid-utf8", "array", "null", "empty-object", "duplicate-key", "too-large"],
)
def test_invalid_or_oversized_file_returns_generic_html(public_client, tmp_path, monkeypatch, content):
    path = tmp_path / "secret-operator-path.json"
    path.write_bytes(content)
    monkeypatch.setenv(legal.CONFIG_ENV, str(path))
    response = public_client.get("/impressum")
    assert response.status_code == 503
    assert "secret-operator-path" not in response.text
    assert "Traceback" not in response.text
    assert "JSONDecodeError" not in response.text


def test_missing_relative_directory_and_unknown_fields_are_not_used(public_client, operator_data, tmp_path, monkeypatch):
    for value in (str(tmp_path / "missing.json"), "relative.json", str(tmp_path)):
        monkeypatch.setenv(legal.CONFIG_ENV, value)
        assert public_client.get("/support").status_code == 503
    operator_data["redirect_url"] = "https://untrusted.example.test"
    configure(tmp_path, monkeypatch, operator_data)
    assert public_client.get("/impressum").status_code == 503


def test_plain_text_is_html_escaped_and_mailto_delimiters_are_percent_encoded(public_client, operator_data, tmp_path, monkeypatch):
    operator_data.update(name='<script>alert("name")</script>', address='<img src=x onerror="alert(1)">\n& Adresse',
                         email="help?subject=other&bcc=hidden@example.test",
                         additional_information='<iframe src="https://other.test"></iframe>\nWeitere & Angaben')
    configure(tmp_path, monkeypatch, operator_data)
    for route in ("/impressum", "/support", "/privacy"):
        response = public_client.get(route)
        assert response.status_code == 200
        assert "<script>" not in response.text and "<img " not in response.text and "<iframe " not in response.text
        assert "&lt;script&gt;" in response.text
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;<br>&amp; Adresse" in response.text
        assert 'href="mailto:help%3Fsubject%3Dother%26bcc%3Dhidden@example.test"' in response.text
    assert "&lt;iframe" in public_client.get("/impressum").text


def test_query_parameters_cannot_override_identity_or_redirect(public_client, operator_data, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, operator_data)
    response = public_client.get("/impressum", params={"next": "https://other.test", "url": "https://other.test", "name": "injected"}, follow_redirects=False)
    assert response.status_code == 200
    assert "location" not in response.headers
    assert "injected" not in response.text


def test_replaced_or_removed_config_is_reflected_without_restart(public_client, operator_data, tmp_path, monkeypatch):
    path = configure(tmp_path, monkeypatch, operator_data)
    assert public_client.get("/support").status_code == 200
    operator_data["name"] = "Geänderter Testbetrieb"
    path.write_text(json.dumps(operator_data), encoding="utf-8")
    assert "Geänderter Testbetrieb" in public_client.get("/support").text
    path.unlink()
    response = public_client.get("/support")
    assert response.status_code == 503
    assert "Geänderter Testbetrieb" not in response.text


def test_legal_routes_do_not_relax_api_spa_or_write_authentication(public_client, operator_data, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, operator_data)
    for route in ("/api/account/profile", "/api/recipes", "/api/config", "/api/admin/overview"):
        assert public_client.get(route).status_code == 401, route
    for route in ("/", "/account", "/admin"):
        response = public_client.get(route, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")
    for route in ("/impressum", "/support"):
        assert public_client.post(route, headers={"Origin": "http://testserver"}).status_code == 405
        assert public_client.delete(route, headers={"Origin": "http://testserver"}).status_code == 405
    assert public_client.delete("/api/account/profile", headers={"Origin": "http://testserver"}).status_code == 401


def test_login_registration_and_main_page_link_to_public_legal_pages(public_client, monkeypatch):
    for route in ("/login", "/register"):
        response = public_client.get(route)
        assert response.status_code == 200
        for target in ("/impressum", "/privacy", "/support"):
            assert f'href="{target}"' in response.text
    import app.main as main
    from app.auth import SESSION_COOKIE
    monkeypatch.setattr(main, "verify_session", lambda value: value == "synthetic-session")
    public_client.cookies.set(SESSION_COOKIE, "synthetic-session")
    response = public_client.get("/")
    assert response.status_code == 200
    for target in ("/impressum", "/privacy", "/support"):
        assert f'href="{target}"' in response.text


def test_privacy_and_support_describe_existing_deletion_flow(public_client, operator_data, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, operator_data)
    for route in ("/privacy", "/support"):
        response = public_client.get(route)
        assert "Einstellungen → Mein Konto → Konto löschen" in response.text
        assert 'href="/account"' in response.text
        assert "Haushaltsdaten" in response.text
    assert "voreingestellten Rezeptregal-Dienst" in public_client.get("/privacy").text


def test_optional_information_can_be_omitted(operator_data, tmp_path, monkeypatch):
    operator_data.pop("additional_information")
    configure(tmp_path, monkeypatch, operator_data)
    assert legal.configured_operator().additional_information == ""
