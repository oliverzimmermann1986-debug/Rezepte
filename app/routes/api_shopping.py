"""Shopping-Cart-API.

Endpoints:
  GET    /api/cart                — Cart-Inhalt (display-formatiert)
  POST   /api/cart/cook/{recipe_id} — "Kochen"-Button: alle Zutaten des Rezepts
                                       in den Cart (mit Smart-Merge)
  POST   /api/cart/add            — Einzeln eine Zutat hinzufügen (Manuell)
  PATCH  /api/cart/{item_id}      — Menge / Häkchen / Name ändern
  DELETE /api/cart/{item_id}      — Eintrag löschen
  POST   /api/cart/clear          — Alles oder nur abgehakte löschen
  GET    /api/cart/export.txt     — Plain-Text-Liste für Mail/WhatsApp

Smart-Merge passiert in `cart_logic.add_recipe_to_cart` — gleiche
canonical_name + kompatible Einheit summiert Mengen (in Basis-Einheit
gespeichert, in Display-Einheit zurückgegeben).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..recipes.cart_logic import add_recipe_to_cart, cart_for_display, prepare_for_cart

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cart", tags=["cart"], dependencies=[Depends(require_auth)])


# ── Reading ─────────────────────────────────────────────────────────────

@router.get("")
def get_cart():
    return {"items": cart_for_display(get_db())}


@router.get("/export.txt", response_class=PlainTextResponse)
def export_text():
    items = cart_for_display(get_db())
    if not items:
        return "Einkaufskorb ist leer."
    lines = ["Einkaufsliste:", ""]
    open_items = [i for i in items if not i["checked"]]
    done_items = [i for i in items if i["checked"]]
    for it in open_items:
        lines.append(f"[ ] {_format_line(it)}")
    if done_items:
        lines.append("")
        lines.append("Erledigt:")
        for it in done_items:
            lines.append(f"[x] {_format_line(it)}")
    return "\n".join(lines) + "\n"


def _format_line(item: dict) -> str:
    parts = []
    amt = item.get("amount")
    unit = item.get("unit")
    if amt is not None:
        # Ganze Zahlen ohne Nachkomma, sonst max 2 Stellen
        if float(amt).is_integer():
            parts.append(str(int(amt)))
        else:
            parts.append(f"{amt:.2f}".rstrip("0").rstrip(".").replace(".", ","))
    if unit:
        parts.append(str(unit))
    parts.append(item["name"])
    return " ".join(parts)


# ── Kochen-Button ───────────────────────────────────────────────────────

class CookPayload(BaseModel):
    """Optional. Wenn nichts geschickt wird, ist multiplier=1.0 (original).
    Frontend schickt z.B. {multiplier: 2.0} um die Mengen zu verdoppeln."""
    multiplier: float = 1.0


@router.post("/cook/{recipe_id}")
def cook_recipe(recipe_id: int, payload: Optional[CookPayload] = None):
    """Lädt alle Zutaten des Rezepts in den Einkaufskorb (mit Smart-Merge).
    Optional mit multiplier zur Portionen-Skalierung."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    multiplier = payload.multiplier if payload else 1.0
    counters = add_recipe_to_cart(db, recipe_id, multiplier=multiplier)
    return {"ok": True, "multiplier": multiplier, **counters}


# ── Manuelles Hinzufügen ────────────────────────────────────────────────

class AddItem(BaseModel):
    name: str
    amount: Optional[float] = None
    unit: Optional[str] = None


@router.post("/add")
def add_item(payload: AddItem):
    if not payload.name.strip():
        raise HTTPException(400, "name fehlt")
    db = get_db()
    p = prepare_for_cart(payload.name, payload.amount, payload.unit)
    item_id = db.cart_add_or_merge(
        name=p["name"],
        canonical_name=p["canonical_name"],
        amount=p["amount"],
        unit=p["unit"],
        source_recipe_id=None,
    )
    return {"ok": True, "id": item_id}


# ── Update / Delete ─────────────────────────────────────────────────────

class CartUpdate(BaseModel):
    amount: Optional[float] = None
    checked: Optional[bool] = None
    name: Optional[str] = None


@router.patch("/{item_id}")
def update_item(item_id: int, payload: CartUpdate):
    db = get_db()
    db.cart_update(item_id, amount=payload.amount, checked=payload.checked, name=payload.name)
    return {"ok": True}


@router.delete("/{item_id}")
def delete_item(item_id: int):
    get_db().cart_delete(item_id)
    return {"ok": True}


class ClearPayload(BaseModel):
    only_checked: bool = False


@router.post("/clear")
def clear_cart(payload: ClearPayload):
    n = get_db().cart_clear(only_checked=payload.only_checked)
    return {"ok": True, "deleted": n}


# ─── Push zur externen Einkauf-App (einkaufen.mausbaeren.me-API) ────────
class PushPayload(BaseModel):
    consolidate: bool = True       # nach Push /consolidate aufrufen?
    only_unchecked: bool = True    # abgehakte Items skippen (haben wir ja schon)
    clear_after: bool = False      # bei Erfolg Cart leeren?


def _format_raw_text(item: dict) -> str:
    """Cart-Item → 'raw_text'-String für die einkauf-API.
    Beispiel: amount=200, unit='g', name='Pasta (trocken)' → '200 g Pasta (trocken)'
    Beispiel: amount=None, unit=None, name='Salz' → 'Salz'"""
    parts = []
    amount = item.get("amount")
    if amount is not None:
        # Saubere Darstellung: 1.0 → '1', 0.5 → '0.5'
        if float(amount).is_integer():
            parts.append(str(int(amount)))
        else:
            parts.append(f"{amount:.2f}".rstrip("0").rstrip("."))
    unit = (item.get("unit") or "").strip()
    if unit:
        parts.append(unit)
    name = (item.get("name") or "").strip()
    if name:
        parts.append(name)
    return " ".join(parts) or "(unbenannt)"


@router.post("/push-to-einkauf")
def push_to_einkauf(payload: PushPayload):
    """Schickt alle Cart-Items als POST /items an die externe Einkauf-API
    (einkaufen.mausbaeren.me oder selbst konfigurierte URL).

    Schema-Quelle: https://einkaufen.mausbaeren.me/openapi.json
      POST /items   { raw_text: str }
      POST /consolidate (kein Body) → dedupliziert/normalisiert auf der
                                       Empfänger-Seite
    Auth: aktuell keine im Schema. Falls 401/403 → wir loggen den Body
    und reichen den HTTP-Code durch."""
    import requests
    cfg = get_config()
    base_url = (cfg.get("einkauf", "api_url", default="") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            400,
            "Einkauf-API-URL nicht konfiguriert. In Einstellungen unter "
            "'Einkauf-App-Integration' eintragen (z.B. https://einkaufen.mausbaeren.me).",
        )

    db = get_db()
    items = db.cart_list()
    if payload.only_unchecked:
        items = [i for i in items if not i.get("checked")]
    if not items:
        raise HTTPException(400, "Keine Items zu pushen (Cart leer oder alle abgehakt)")

    pushed_ids: list = []
    failed: list = []
    session = requests.Session()
    for it in items:
        raw_text = _format_raw_text(it)
        try:
            r = session.post(
                f"{base_url}/items",
                json={"raw_text": raw_text},
                timeout=(5, 10),  # (connect, read)
                allow_redirects=True,
            )
            if r.status_code >= 400:
                failed.append({
                    "id": it["id"], "raw_text": raw_text,
                    "status": r.status_code, "error": r.text[:200],
                })
            else:
                pushed_ids.append(it["id"])
        except Exception as e:
            failed.append({
                "id": it["id"], "raw_text": raw_text, "error": str(e)[:200],
            })

    # Konsolidieren wenn gewünscht und mindestens ein Push erfolgreich war
    consolidated = False
    if payload.consolidate and pushed_ids:
        try:
            cr = session.post(f"{base_url}/consolidate", timeout=(5, 15))
            consolidated = cr.status_code < 400
            if not consolidated:
                logger.warning(f"consolidate returned {cr.status_code}: {cr.text[:200]}")
        except Exception as e:
            logger.warning(f"consolidate-Call failed: {e}")

    # Optional: Cart leeren bei vollem Erfolg
    cleared = 0
    if payload.clear_after and pushed_ids and not failed:
        cleared = db.cart_clear(only_checked=False)

    logger.info(
        f"push-to-einkauf: {len(pushed_ids)}/{len(items)} Items zu {base_url} "
        f"(failed={len(failed)}, consolidated={consolidated}, cleared={cleared})"
    )
    return {
        "ok": True,
        "pushed": len(pushed_ids),
        "total": len(items),
        "failed": failed,
        "consolidated": consolidated,
        "cleared": cleared,
        "target": base_url,
    }
