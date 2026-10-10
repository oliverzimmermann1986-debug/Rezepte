"""Public legal/contact pages, backed only by explicitly configured operator data."""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


CONFIG_ENV = "SCRAPPER_LEGAL_CONFIG_FILE"
MAX_CONFIG_BYTES = 32_768
router = APIRouter()
LEGAL_NAV_HTML = (
    '<nav aria-label="Rechtliches und Hilfe">'
    '<a class="btn btn-ghost" href="/impressum">Impressum</a> '
    '<a class="btn btn-ghost" href="/privacy">Datenschutz</a> '
    '<a class="btn btn-ghost" href="/support">Support</a></nav>'
)


@dataclass(frozen=True)
class Operator:
    name: str
    address: str
    email: str
    additional_information: str = ""


def _plain_text(value, *, maximum: int, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid operator field")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError("Invalid operator field")
    for char in value:
        if ord(char) < 32 and not (multiline and char in "\r\n") or 127 <= ord(char) <= 159:
            raise ValueError("Invalid operator field")
    if not multiline and ("\n" in value or "\r" in value):
        raise ValueError("Invalid operator field")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _email(value) -> str:
    value = _plain_text(value, maximum=254)
    if value.count("@") != 1 or not value.isascii():
        raise ValueError("Invalid operator email")
    local, domain = value.rsplit("@", 1)
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local):
        raise ValueError("Invalid operator email")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("Invalid operator email")
    labels = domain.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
        raise ValueError("Invalid operator email")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate operator field")
        result[key] = value
    return result


def configured_operator() -> Operator | None:
    """No defaults, config fallback, network requests, caching or raw-error output."""
    filename = os.environ.get(CONFIG_ENV, "").strip()
    if not filename:
        return None
    try:
        path = Path(filename)
        if not path.is_absolute() or not path.is_file():
            return None
        with path.open("rb") as handle:
            data = handle.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            return None
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) - {"name", "address", "email", "additional_information"}:
            return None
        extra = value.get("additional_information", "")
        if extra != "":
            extra = _plain_text(extra, maximum=8_000, multiline=True)
        return Operator(
            name=_plain_text(value.get("name"), maximum=300),
            address=_plain_text(value.get("address"), maximum=2_000, multiline=True),
            email=_email(value.get("email")),
            additional_information=extra,
        )
    except (OSError, ValueError, TypeError, RecursionError):
        # Neither filesystem paths nor configured values appear in public errors.
        return None


def _paragraph(value: str) -> str:
    return html.escape(value, quote=True).replace("\n", "<br>")


def _contact(operator: Operator) -> str:
    # Percent-encode every local-part delimiter before HTML attribute escaping.
    mailto = html.escape("mailto:" + quote(operator.email, safe="@"), quote=True)
    return f'<a href="{mailto}">{html.escape(operator.email, quote=True)}</a>'


def _operator_html(operator: Operator) -> str:
    return (
        '<address style="font-style:normal">'
        f'<strong>{_paragraph(operator.name)}</strong><br>{_paragraph(operator.address)}'
        f'<br>E-Mail: {_contact(operator)}</address>'
    )


def privacy_operator_html() -> str:
    operator = configured_operator()
    if operator is None:
        return (
            '<h2>Verantwortlicher und Kontakt</h2>'
            '<p>Für diesen Server sind noch keine vollständigen Betreiberangaben hinterlegt. '
            'Eine konkrete Kontaktstelle kann deshalb hier noch nicht genannt werden. '
            'Diese Datenschutzhinweise enthalten allgemeine Informationen zur Datenverarbeitung; '
            'die Betreiberangaben müssen ergänzt werden.</p>'
        )
    return '<h2>Verantwortlicher und Kontakt</h2>' + _operator_html(operator)


def _page(title: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    escaped_title = html.escape(title)
    content = f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_title} – Rezeptregal</title>
<style>
body{{margin:0;background:#fffaf0;color:#433427;font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:720px;margin:auto;padding:32px 20px 64px;overflow-wrap:anywhere}}
h1{{font-size:2rem;line-height:1.2}}h2{{margin-top:28px;font-size:1.25rem}}
a{{color:inherit}}nav{{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:32px}}
nav a{{display:inline-flex;align-items:center;min-height:44px}}
</style></head><body><main><h1>{escaped_title}</h1>{body}
{LEGAL_NAV_HTML}<p><a href="/login">Zur Anmeldung</a></p></main></body></html>'''
    return HTMLResponse(content, status_code=status_code, headers={"Cache-Control": "no-store"})


def _unavailable(title: str) -> HTMLResponse:
    return _page(title, '<p>Die Betreiberangaben für diesen Server sind noch nicht vollständig verfügbar. '
                 'Diese Seite kann deshalb derzeit keine vollständigen Anbieter- oder Kontaktdaten anzeigen.</p>'
                 '<p>Bitte versuche es später erneut. Allgemeine Hinweise zur Datenverarbeitung stehen unter Datenschutz.</p>',
                 status_code=503)


@router.get("/impressum", response_class=HTMLResponse, include_in_schema=False)
def imprint_page():
    operator = configured_operator()
    if operator is None:
        return _unavailable("Impressum derzeit nicht verfügbar")
    body = '<h2>Anbieter dieses Rezeptservers</h2>' + _operator_html(operator)
    if operator.additional_information:
        body += '<h2>Weitere Anbieterangaben</h2><p>' + _paragraph(operator.additional_information) + '</p>'
    return _page("Impressum", body)


@router.get("/support", response_class=HTMLResponse, include_in_schema=False)
def support_page():
    operator = configured_operator()
    if operator is None:
        return _unavailable("Support derzeit nicht verfügbar")
    body = (
        '<p>Bei Fragen zu diesem Rezeptserver oder Problemen mit deinem Konto erreichst du den Betreiber per E-Mail: '
        + _contact(operator) + '</p>'
        '<p>Beschreibe den betroffenen Schritt und die angezeigte Fehlermeldung. '
        'Nenne möglichst App-Version und iOS-Version. Sende keine Passwörter, Sitzungstoken oder Einladungslinks.</p>'
        '<h2>Konto und Daten löschen</h2>'
        '<p>In der iPhone-App: Einstellungen → Mein Konto → Konto löschen. '
        'Im Browser: <a href="/account">Mein Konto</a> → Konto löschen. '
        'Die Löschung erfordert eine erneute Bestätigung deiner Anmeldung. '
        'Gemeinsame Haushaltsdaten bleiben für weitere Mitglieder erhalten. '
        'Als einziges Mitglied kannst du mit der zusätzlichen Bestätigung „HAUSHALT LÖSCHEN“ '
        'auch deinen Haushalt samt privaten Rezepten, Importen, Listen und Dateien entfernen. '
        'Laufende Verarbeitungen müssen vorher beendet sein. Eine ausstehende Datei-Bereinigung '
        'wird serverseitig wiederholt. Das letzte Administratorkonto erfordert zunächst die Übergabe der Serververwaltung. '
        'Falls die Löschung gesperrt ist oder du keinen Kontozugriff mehr hast, wende dich an den Betreiber.</p>'
        '<h2>Betreiber und Kontakt</h2>' + _operator_html(operator)
    )
    return _page("Support", body)
