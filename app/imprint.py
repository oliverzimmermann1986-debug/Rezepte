"""Public operator information; independent of login and the support portal."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter()
_TEMPLATE = Path(__file__).parent / "static" / "impressum.html"


@router.get("/impressum", response_class=HTMLResponse, include_in_schema=False)
def imprint_page():
    return HTMLResponse(
        _TEMPLATE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache"},
    )
