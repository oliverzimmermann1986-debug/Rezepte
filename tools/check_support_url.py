"""Check the public support form and active Rezeptregal module before upload."""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PUBLIC_SUPPORT_URL = "https://support.zimlab.org/?module=rezeptregal"
PUBLIC_CONFIG_URL = "https://support.zimlab.org/api/public/config"


class NoRedirects(HTTPRedirectHandler):
    """The canonical public endpoints must not forward to login or another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SupportHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.fields = set()
        self.form = False
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.links.append(attributes.get("href", ""))
        if tag == "form" and attributes.get("id") == "support-form":
            self.form = True
        if tag in {"input", "textarea"}:
            self.fields.add(attributes.get("name", ""))
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def check_document(document):
    page = SupportHTML()
    page.feed(document)
    if "Zimlab" not in page.title or "Support" not in page.title or not page.form:
        raise ValueError("Support URL does not show the Zimlab support form (possibly a login page).")
    if not {"email", "subject", "message"}.issubset(page.fields):
        raise ValueError("Support form lacks required contact fields.")
    if "/privacy" not in page.links:
        raise ValueError("Support page lacks its privacy link.")
    if "/impressum" not in page.links:
        raise ValueError("Support page lacks its legal notice link.")
    if any(urlsplit(link).scheme == "mailto" for link in page.links):
        raise ValueError("Public support must use the form, not expose an email contact.")


def check_configuration(data):
    if (not isinstance(data, dict) or data.get("ready") is not True
            or not isinstance(data.get("siteKey"), str) or not data["siteKey"].strip()):
        raise ValueError("Support form is not configured and ready.")
    modules = data.get("modules")
    if not isinstance(modules, list) or not any(
        isinstance(module, dict) and module.get("slug") == "rezeptregal"
        and module.get("enabled") is not False for module in modules
    ):
        raise ValueError("Rezeptregal support module is not enabled.")


def fetch_public(url, content_type):
    request = Request(url, headers={"User-Agent": "Rezeptregal-Support-Check/2.0"})
    with build_opener(NoRedirects).open(request, timeout=20) as response:
        target = urlsplit(response.url)
        if response.status != 200 or response.url != url or target.scheme != "https" or target.username or target.password:
            raise ValueError("Support URL must return HTTP 200 at the canonical HTTPS URL without redirects or credentials.")
        if response.headers.get_content_type() != content_type:
            raise ValueError(f"Support URL must return {content_type}.")
        body = response.read(1_048_577)
        if len(body) > 1_048_576:
            raise ValueError("Unexpectedly large support response.")
        return response.url, body.decode("utf-8")


def check_url(url):
    if url != PUBLIC_SUPPORT_URL:
        raise ValueError(f"Configure the approved module-specific Support URL: {PUBLIC_SUPPORT_URL}")
    # Anonymous GETs only; never create fake tickets in production.
    _, document = fetch_public(url, "text/html")
    check_document(document)
    _, config = fetch_public(PUBLIC_CONFIG_URL, "application/json")
    check_configuration(json.loads(config))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    try:
        check_url(args.url)
    except Exception as exc:
        parser.exit(1, f"Support check failed: {exc}\n")
    print("Public support form and active Rezeptregal module: OK (anonymous HTTPS checks).")


if __name__ == "__main__":
    main()
