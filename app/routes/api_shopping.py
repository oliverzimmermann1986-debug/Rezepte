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

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..auth import require_auth
from ..db import get_db
from ..recipes.cart_logic import add_recipe_to_cart, cart_for_display, prepare_for_cart

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

@router.post("/cook/{recipe_id}")
def cook_recipe(recipe_id: int):
    """Lädt alle Zutaten des Rezepts in den Einkaufskorb (mit Smart-Merge)."""
    db = get_db()
    if not db.recipe_get(recipe_id):
        raise HTTPException(404, "Rezept nicht gefunden")
    counters = add_recipe_to_cart(db, recipe_id)
    return {"ok": True, **counters}


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
