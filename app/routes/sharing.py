"""Print-View + Public-Sharing-Links für Rezepte.

Routes:
  GET  /recipe/{id}/pdf            — Auth-required, echte PDF-Datei
  POST /api/recipes/{id}/share     — Auth-required, generiert signiertes Token
                                     + URL. Default-Gültigkeit 7 Tage.
  GET  /share/{token}              — KEINE Auth, validiert Token, zeigt
                                     Print-View für den Empfänger
  GET  /share/{token}/thumb        — Public-Thumbnail-Endpoint (gleiche
                                     Token-Validation)

Token sind stateless (URLSafeTimedSerializer mit web.secret_key). Neue Links
sind standardmäßig 7 Tage gültig und enthalten keinen Benutzernamen.

DSGVO-Hinweise:
- Share-Link enthält nur Rezept-ID und Ablaufzeit
- Token ist HMAC-signiert, kein Bruteforce möglich
- Tokens werden NICHT in Logs ausgegeben (nur recipe_id)
"""
from __future__ import annotations

import html
import logging
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from ..auth import require_auth
from ..config_store import get_config
from ..core.safety import resolve_directory_under, resolve_regular_file_under
from ..db import get_db
from ..recipes.recipe_pdf import build_recipe_pdf

logger = logging.getLogger(__name__)

# Authenticated PDF-/Share-Routen (eingeloggte User)
print_router = APIRouter(tags=["sharing"])
# Public share-resolution (KEINE Auth)
public_router = APIRouter(tags=["sharing"])
# Share-Token-Generator (auth-required, an api_recipes angehängt)
share_api_router = APIRouter(prefix="/api/recipes", tags=["sharing"],
                              dependencies=[Depends(require_auth)])

SHARE_SALT = "recipe-share-v1"
SHARE_MAX_AGE_DAYS_DEFAULT = 7


def _serializer() -> URLSafeTimedSerializer:
    secret = get_config().get("web", "secret_key", default="") or ""
    if not secret or len(secret) < 32:
        raise RuntimeError("web.secret_key fehlt — Sharing nicht möglich")
    return URLSafeTimedSerializer(secret, salt=SHARE_SALT)


def _load_share_token(token: str) -> dict:
    """Lädt neue Tokens mit eigenem ``exp`` und alte Tokens mit 30-Tage-Frist."""
    data = _serializer().loads(token, max_age=365 * 86400)
    exp = data.get("exp")
    if exp is None:
        # Kompatibilität: alte Tokens hatten kein exp und waren fest 30 Tage gültig.
        return _serializer().loads(
            token,
            max_age=SHARE_MAX_AGE_DAYS_DEFAULT * 86400,
        )
    try:
        if float(exp) < time.time():
            raise SignatureExpired("Share-Link ist abgelaufen", payload=data)
    except (TypeError, ValueError) as exc:
        raise BadSignature("Ungültiges Ablaufdatum im Share-Token") from exc
    return data


def _format_ingredient(ing: dict) -> str:
    """Zutat als 'Menge Einheit Name'-String, optional aus raw_text."""
    raw = (ing.get("raw") or "").strip()
    if raw:
        return raw
    parts = []
    if ing.get("amount") is not None:
        amt = ing["amount"]
        parts.append(f"{amt:g}" if isinstance(amt, float) else str(amt))
    if ing.get("unit"):
        parts.append(str(ing["unit"]))
    if ing.get("name"):
        parts.append(str(ing["name"]))
    return " ".join(parts)


def _render_print_html(
    recipe: dict,
    image_url: Optional[str] = None,
    shared_by: Optional[str] = None,
    is_share: bool = False,
) -> str:
    """Rendert eine print-optimierte HTML-Seite für ein Rezept.
    Single-File mit Inline-CSS — keine Static-Dependencies, damit auch
    der public-/share/-Pfad ohne static-Files-Routing funktioniert.

    Browser-`@media print`-CSS verbirgt die Action-Buttons, sodass User
    via Cmd+P → 'Als PDF speichern' eine saubere A4-Karte bekommt."""
    name = html.escape(recipe.get("name") or "Unbenannt")
    description = (recipe.get("description") or "").strip()
    desc_safe = html.escape(description) if description else ""

    type_cat = " · ".join(
        html.escape(str(x)) for x in
        (recipe.get("type"), recipe.get("category")) if x
    )

    url = recipe.get("url") or ""
    url_safe = html.escape(url) if url else ""
    # nur https/http erlauben — javascript:-URLs würden Print-View XSSen
    url_safe = url_safe if url_safe.startswith(("http://", "https://")) else ""

    # Zutaten als Liste
    ings = recipe.get("ingredients") or []
    ing_html = "".join(
        f'<li>{html.escape(_format_ingredient(i))}</li>'
        for i in ings if _format_ingredient(i)
    )

    # Schritte als ol
    steps = recipe.get("steps") or []
    steps_html = "".join(
        f'<li>{html.escape((s.get("instruction") or "").strip())}</li>'
        for s in steps if (s.get("instruction") or "").strip()
    )

    # Nährwerte falls vorhanden
    nutrition_html = ""
    cal = recipe.get("calories_per_serving")
    if cal:
        nutrition_html = (
            '<div class="nutrition">'
            f'<strong>Pro Portion:</strong> '
            f'~{int(cal)} kcal · '
            f'{recipe.get("protein_g") or 0:g}g Eiweiß · '
            f'{recipe.get("carbs_g") or 0:g}g KH · '
            f'{recipe.get("fat_g") or 0:g}g Fett'
            '</div>'
        )

    # Servings
    srv = recipe.get("servings")
    srv_html = f'<span class="meta">{int(srv)} Portionen</span>' if srv else ""

    # Tags als Pills
    tags = recipe.get("tags") or []
    tag_names = [t.get("name") or t for t in tags if isinstance(t, dict)] if tags and isinstance(tags[0], dict) else tags
    tag_html = ""
    if tag_names:
        tag_html = '<div class="tags">' + "".join(
            f'<span class="tag">{html.escape(str(t))}</span>' for t in tag_names
        ) + "</div>"

    # Image
    img_html = ""
    if image_url:
        img_html = f'<img src="{html.escape(image_url)}" alt="" class="thumb">'

    # Footer
    footer_parts = []
    if shared_by:
        footer_parts.append(f'Geteilt von {html.escape(shared_by)}')
    if url_safe:
        footer_parts.append(f'Quelle: <a href="{url_safe}" target="_blank" rel="noopener noreferrer">{url_safe}</a>')
    footer_html = ""
    if footer_parts:
        footer_html = '<footer class="rfooter">' + " · ".join(footer_parts) + "</footer>"

    # Watermark wenn share
    share_banner = ""
    if is_share:
        share_banner = (
            '<div class="share-banner no-print">'
            '🔗 Geteiltes Rezept · '
            '<a href="javascript:window.print()">Als PDF speichern</a>'
            '</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
  :root {{
    --fg: #1a1a1a; --fg-dim: #666; --bg: #fff;
    --accent: #ff7849; --border: #e0e0e0;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    color: var(--fg); background: var(--bg);
    max-width: 760px; margin: 0 auto; padding: 24px 28px;
    line-height: 1.5; font-size: 15px;
  }}
  h1 {{ font-size: 28px; margin: 0 0 8px; line-height: 1.2; }}
  h2 {{
    font-size: 18px; margin: 28px 0 10px;
    border-bottom: 2px solid var(--accent); padding-bottom: 4px;
    page-break-after: avoid;
  }}
  .meta {{ color: var(--fg-dim); font-size: 13px; }}
  .meta + .meta::before {{ content: " · "; }}
  .thumb {{
    width: 100%; max-height: 280px; object-fit: cover;
    border-radius: 6px; margin: 12px 0 16px;
  }}
  .desc {{
    color: var(--fg-dim); font-style: italic;
    margin: 12px 0; padding: 10px 14px;
    background: #f7f7f7; border-left: 3px solid var(--accent);
    white-space: pre-wrap;
  }}
  .nutrition {{
    background: #fff5f0; border: 1px solid #ffd4c4;
    padding: 8px 14px; border-radius: 6px;
    font-size: 13px; margin: 14px 0;
  }}
  .tags {{ margin: 14px 0 0; }}
  .tag {{
    display: inline-block; font-size: 11px;
    background: #f0f0f0; padding: 2px 9px;
    border-radius: 10px; margin: 2px 4px 2px 0;
    color: var(--fg-dim);
  }}
  ul, ol {{ padding-left: 22px; }}
  li {{ margin-bottom: 6px; }}
  .rfooter {{
    margin-top: 32px; padding-top: 12px;
    border-top: 1px solid var(--border);
    color: var(--fg-dim); font-size: 12px;
  }}
  .rfooter a {{ color: var(--fg-dim); }}
  .share-banner {{
    position: sticky; top: 0; z-index: 10;
    background: var(--accent); color: white;
    padding: 8px 14px; margin: -24px -28px 16px; border-radius: 0;
    font-size: 13px; text-align: center;
  }}
  .share-banner a {{ color: white; font-weight: 600; }}

  @media print {{
    body {{ padding: 0; max-width: none; font-size: 11pt; }}
    .no-print {{ display: none !important; }}
    h1 {{ font-size: 22pt; }}
    h2 {{ font-size: 14pt; }}
    .thumb {{ max-height: 200px; }}
    ol, ul {{ page-break-inside: avoid; }}
  }}
  @page {{ margin: 1.5cm; }}
</style>
</head>
<body>
<div class="no-print" style="position:fixed; top:10px; right:10px; z-index:50; display:flex; gap:8px;">
  <button onclick="window.print()"
          style="min-height:42px; padding:8px 14px; border-radius:21px; border:1px solid #ddd; background:#fff; font-size:15px; box-shadow:0 2px 8px rgba(0,0,0,.18); cursor:pointer;">🖨 Drucken</button>
  <button onclick="if(history.length>1){{history.back()}}else{{window.close()}}"
          style="min-height:42px; min-width:42px; padding:8px 14px; border-radius:21px; border:1px solid #ddd; background:#fff; font-size:16px; font-weight:600; box-shadow:0 2px 8px rgba(0,0,0,.18); cursor:pointer;">✕ Schließen</button>
</div>
{share_banner}
<h1>{name}</h1>
<div>{type_cat}{srv_html}</div>
{img_html}
{nutrition_html}
{f'<p class="desc">{desc_safe}</p>' if desc_safe else ''}

<h2>Zutaten {f'({len(ings)})' if ings else ''}</h2>
{f'<ul>{ing_html}</ul>' if ing_html else '<p class="meta">— keine Zutaten erfasst —</p>'}

<h2>Zubereitung {f'({len(steps)})' if steps else ''}</h2>
{f'<ol>{steps_html}</ol>' if steps_html else '<p class="meta">— keine Schritte erfasst —</p>'}

{tag_html}
{footer_html}
</body>
</html>"""


def _load_recipe_full(recipe_id: int) -> dict:
    """Lädt Rezept + Zutaten + Schritte + Tags. Raised 404 wenn nicht da."""
    db = get_db()
    r = db.recipe_get(recipe_id)
    if not r or r.get("deleted_at") is not None:
        raise HTTPException(404, "Rezept nicht gefunden")
    r["ingredients"] = db.recipe_ingredients_get(recipe_id)
    r["steps"] = db.recipe_steps_get(recipe_id)
    r["tags"] = db.recipe_tags_get(recipe_id)
    return r


# ════ Authenticated PDF ════
@print_router.get(
    "/recipe/{recipe_id}/pdf",
    dependencies=[Depends(require_auth)],
)
def recipe_pdf(recipe_id: int):
    """Erzeugt eine echte PDF-Datei für Download und Web-Share."""
    recipe = _load_recipe_full(recipe_id)
    try:
        pdf = build_recipe_pdf(recipe)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    slug = re.sub(r"[^a-z0-9]+", "-", str(recipe.get("name") or "").lower())
    slug = slug.strip("-")[:60] or f"rezept-{recipe_id}"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class ShareRequest(BaseModel):
    expires_days: int = Field(SHARE_MAX_AGE_DAYS_DEFAULT, ge=1, le=30)


@share_api_router.post("/{recipe_id}/share")
def create_share_link(recipe_id: int, payload: ShareRequest, request: Request):
    """Generiert einen kurzlebigen öffentlichen Link ohne Benutzername."""
    db = get_db()
    recipe = db.recipe_get(recipe_id)
    if not recipe or recipe.get("deleted_at") is not None:
        raise HTTPException(404, "Rezept nicht gefunden")

    token = _serializer().dumps({
        "rid": int(recipe_id),
        "exp": time.time() + payload.expires_days * 86400,
    })

    # base_url respektiert X-Forwarded-Proto + Host bei Reverse-Proxy
    base = str(request.base_url).rstrip("/")
    url = f"{base}/share/{token}"
    logger.info(f"Share-Link für Rezept #{recipe_id} erstellt (gültig {payload.expires_days}d)")
    return {
        "ok": True,
        "url": url,
        "expires_days": payload.expires_days,
        "recipe_id": recipe_id,
    }


# ════ Public Share-Resolution (KEINE Auth) ════
@public_router.get("/share/{token}", response_class=HTMLResponse)
def share_recipe(token: str):
    """Validiert Token, zeigt Print-View für den Empfänger. Bei Bad/Expired
    Token: klare Fehlerseite (Status 410/403 mit kurzem HTML statt JSON)."""
    try:
        data = _load_share_token(token)
    except SignatureExpired:
        return HTMLResponse(
            "<h1>Link abgelaufen</h1><p>Dieser Share-Link ist nicht mehr gültig. "
            "Bitte den Besitzer um einen neuen.</p>",
            status_code=410,
        )
    except BadSignature:
        return HTMLResponse(
            "<h1>Ungültiger Link</h1><p>Der Link konnte nicht validiert werden.</p>",
            status_code=403,
        )

    rid = int(data.get("rid") or 0)
    if rid <= 0:
        raise HTTPException(400, "Ungültige Token-Daten")
    try:
        recipe = _load_recipe_full(rid)
    except HTTPException as e:
        if e.status_code == 404:
            return HTMLResponse(
                "<h1>Rezept nicht mehr verfügbar</h1>",
                status_code=404,
            )
        raise

    image_url = f"/share/{token}/thumb" if recipe.get("thumb_filename") else None
    return HTMLResponse(_render_print_html(
        recipe,
        image_url=image_url,
        shared_by=data.get("by"),
        is_share=True,
    ))


@public_router.get("/share/{token}/thumb")
def share_thumb(token: str):
    """Public-Thumbnail mit gleicher Token-Validation wie die Hauptview."""
    try:
        data = _load_share_token(token)
    except (BadSignature, SignatureExpired):
        raise HTTPException(403, "Ungültiger oder abgelaufener Link")

    rid = int(data.get("rid") or 0)
    db = get_db()
    r = db.recipe_get(rid)
    if (
        not r
        or r.get("deleted_at") is not None
        or not r.get("thumb_filename")
    ):
        raise HTTPException(404)
    try:
        root = Path(get_config().get("paths", "recipe_dir", default="/mnt/rezepte"))
        folder = resolve_directory_under(Path(r["folder_path"]), root)
        fp = resolve_regular_file_under(
            folder / str(r["thumb_filename"]),
            folder,
            root,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(404) from exc
    return FileResponse(str(fp), headers={"Cache-Control": "private, max-age=300"})
