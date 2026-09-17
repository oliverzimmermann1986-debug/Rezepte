"""Public, data-free support page for Rezeptregal and App Store metadata."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter()
_TEMPLATE = Path(__file__).parent / "static" / "support.html"
PUBLIC_SUPPORT_URL = "https://support.zimlab.org/?module=rezeptregal"


@router.get("/support", response_class=HTMLResponse, include_in_schema=False)
def support_page():
    """Never require a recipe account or expose server/user data for support.

    Only the approved, module-specific portal URL can be enabled. The explicit
    configuration gate stays closed until the public form is verified. Neither
    query parameters nor arbitrary environment URLs can redirect visitors.
    """
    destination = os.environ.get("REZEPTREGAL_SUPPORT_PORTAL_URL", "").strip()
    if destination == PUBLIC_SUPPORT_URL:
        return RedirectResponse(PUBLIC_SUPPORT_URL, status_code=307, headers={"Cache-Control": "no-store"})
    contact = (
        '<p role="status">Das Supportformular wird gerade eingerichtet. '
        'Bitte wende dich bis dahin an den Betreiber deines Rezepteservers.<br>'
        '<span lang="en">The support form is being set up. '
        'Please contact your recipe server administrator in the meantime.</span></p>'
    )
    return HTMLResponse(
        _TEMPLATE.read_text(encoding="utf-8").replace("<!-- SUPPORT_CONTACT -->", contact),
        status_code=503,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )
