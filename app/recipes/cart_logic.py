"""Smart-Merge-Logik für den Einkaufskorb.

Hier liegt die Geschäftslogik, die VOR dem DB-Insert läuft:
  - Zutaten-Namen normalisieren (canonical_name)
  - Einheit normalisieren (normalize_unit)
  - In Basis-Einheit konvertieren (z.B. kg → g)
  - DB-Layer suchen lassen, ob es einen mergeable-Eintrag gibt
  - Wenn ja, Mengen addieren; wenn nein, neu einfügen

Der DB-Layer (db.cart_add_or_merge) macht nur den finalen INSERT/UPDATE,
hier wird die Klassifizierung gemacht — so kann die Logik unabhängig
vom DB-State getestet werden.

Display: in der UI rechnet `display_amount()` die gespeicherte Basis-
Menge wieder in eine sinnvolle Anzeige-Einheit zurück (1500 g → "1,5 kg").
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from .canonical import (
    TOMATO_CANONICAL,
    TOMATO_SHOPPING_NAME,
    canonical_name as _canonical,
)
from .units import normalize_unit, to_base, from_base_display, unit_class

logger = logging.getLogger(__name__)


def prepare_for_cart(name: str, amount: Optional[float], unit: Optional[str]) -> Dict[str, Optional[object]]:
    """Bereitet einen Zutaten-Eintrag fürs Speichern im Cart vor:
       - canonical_name berechnen
       - unit normalisieren
       - amount in Basis-Einheit konvertieren (sodass spätere Merges
         konsistent summieren können)

    Rückgabe ist ein Dict zum direkten Weiterreichen an cart_add_or_merge."""
    canon = _canonical(name)
    norm_unit = normalize_unit(unit)
    base_unit, base_amount = to_base(norm_unit, amount)
    return {
        "name": (
            TOMATO_SHOPPING_NAME
            if canon == TOMATO_CANONICAL
            else (name or "").strip() or "?"
        ),
        "canonical_name": canon,
        "amount": base_amount,
        "unit": base_unit,
    }


def display_amount(amount: Optional[float], unit: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """Konvertiert eine gespeicherte (Basis-Einheit, Menge) in eine sinnvolle
       Display-Form. Aufruf vor dem Rendering in der UI.

       Beispiele:
         display_amount(1500, "g") → (1.5, "kg")
         display_amount(250, "g")  → (250, "g")
         display_amount(3, "Stück") → (3, "Stück")
         display_amount(None, "Prise") → (None, "Prise")"""
    if amount is None or unit is None:
        return amount, unit
    cls = unit_class(unit)
    if cls in ("mass", "volume"):
        new_unit, new_amount = from_base_display(cls, amount)
        # Runden auf 2 Nachkommastellen damit "1.4999999999"-Floats nicht durchkommen
        return round(new_amount, 2), new_unit
    return amount, unit


def add_recipe_to_cart(db, recipe_id: int, multiplier: float = 1.0) -> Dict[str, int]:
    """Fügt ALLE Zutaten eines Rezepts dem Einkaufskorb hinzu (mit Merge).

    multiplier: Skalierungs-Faktor für die Mengen. Default 1.0 = original.
      Beispiele: 0.5 = halbieren, 2.0 = verdoppeln, 3.0 = verdreifachen.
      Wird auf jede Zutat-Menge VOR der Base-Unit-Konvertierung angewendet.
      Bei amount=None (z.B. "Prise Salz") bleibt es None — Skalierung von
      Nichts ist immer noch Nichts.

    Rückgabe: Counters {"added": n_neu, "merged": n_summiert, "skipped": n_uebersprungen}.
    """
    # Sanity: negativer oder 0er multiplier macht keinen Sinn, fallback auf 1
    try:
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        multiplier = 1.0
    if multiplier <= 0 or multiplier > 100:
        multiplier = 1.0

    ingredients = db.recipe_ingredients_get(recipe_id)
    excluded = db.shopping_excluded_canonicals()
    counters = {"added": 0, "merged": 0, "skipped": 0}
    for ing in ingredients:
        name = ing.get("name") or ""
        canon = ing.get("canonical_name") or _canonical(name)
        if not canon:
            # Zutat ohne erkennbaren Namen — überspringen (passiert nicht,
            # weil canonical_name in der DB seit Migration 1 immer gesetzt
            # wird, aber defensiv)
            counters["skipped"] += 1
            continue
        if canon.strip().lower() in excluded:
            counters["skipped"] += 1
            continue

        amount = ing.get("amount")
        if amount is not None and multiplier != 1.0:
            amount = amount * multiplier
        unit = normalize_unit(ing.get("unit"))
        base_unit, base_amount = to_base(unit, amount)

        # War schon was im Cart mit selber canonical+unit?
        existed = db.cart_find_mergeable(canon, base_unit)
        db.cart_add_or_merge(
            name=TOMATO_SHOPPING_NAME if canon == TOMATO_CANONICAL else name or canon,
            canonical_name=canon,
            amount=base_amount,
            unit=base_unit,
            source_recipe_id=recipe_id,
        )
        if existed:
            counters["merged"] += 1
        else:
            counters["added"] += 1
    return counters


def aggregate_recipes_for_cart(
    db,
    selections: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Aggregiert mehrere Rezepte ohne den Warenkorb zu verändern.

    ``selections`` enthält ``recipe_id`` und einen bereits berechneten
    ``multiplier``. Das Ergebnis verwendet dieselben Basis-Einheiten und
    Canonicals wie der echte Warenkorb und kann daher als Vorschau oder für
    einen atomaren ``cart_replace`` verwendet werden.
    """
    excluded = db.shopping_excluded_canonicals()
    aggregated: Dict[Tuple[str, Optional[str]], Dict[str, object]] = {}

    for selection in selections:
        try:
            recipe_id = int(selection.get("recipe_id"))
            multiplier = float(selection.get("multiplier", 1.0))
        except (TypeError, ValueError):
            continue
        if multiplier <= 0 or multiplier > 100:
            multiplier = 1.0

        for ingredient in db.recipe_ingredients_get(recipe_id):
            name = ingredient.get("name") or ""
            canonical = ingredient.get("canonical_name") or _canonical(name)
            if not canonical or canonical.strip().lower() in excluded:
                continue
            amount = ingredient.get("amount")
            if amount is not None:
                amount = float(amount) * multiplier
            prepared = prepare_for_cart(name, amount, ingredient.get("unit"))
            key = (str(prepared["canonical_name"]), prepared["unit"])
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = {
                    **prepared,
                    "source_recipe_ids": [recipe_id],
                }
                continue
            if prepared["amount"] is not None:
                existing["amount"] = (
                    float(existing["amount"] or 0) + float(prepared["amount"])
                )
            sources = existing["source_recipe_ids"]
            if recipe_id not in sources:
                sources.append(recipe_id)

    return sorted(
        aggregated.values(),
        key=lambda item: str(item.get("name") or "").casefold(),
    )


def aggregated_cart_for_display(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for item in items:
        display_value, display_unit = display_amount(item.get("amount"), item.get("unit"))
        out.append({
            **item,
            "amount_base": item.get("amount"),
            "unit_base": item.get("unit"),
            "amount": display_value,
            "unit": display_unit,
        })
    return out


def cart_for_display(db) -> List[Dict[str, object]]:
    """Holt den Cart-Inhalt aus der DB und konvertiert Basis-Mengen in
       Anzeige-Mengen. Frontend rendert direkt dieses Resultat."""
    out = []
    for row in db.cart_list():
        d_amount, d_unit = display_amount(row.get("amount"), row.get("unit"))
        out.append({
            "id": row["id"],
            "name": row["name"],
            "canonical_name": row.get("canonical_name"),
            "amount": d_amount,
            "amount_base": row.get("amount"),       # für Resync
            "unit": d_unit,
            "unit_base": row.get("unit"),           # für Resync
            "checked": bool(row.get("checked")),
            "added_at": row.get("added_at"),
            "source_recipe_ids": _parse_json_array(row.get("source_recipe_ids")),
            "category": row.get("category"),
            "sort_order": row.get("sort_order"),
        })
    return out


def _parse_json_array(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    import json
    try:
        v = json.loads(raw)
        return [int(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []
