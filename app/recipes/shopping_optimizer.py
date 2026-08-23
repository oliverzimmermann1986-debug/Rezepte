"""Sichere Aufbereitung von KI-Vorschlägen für die Einkaufsliste.

Die KI darf nur Anzeigenamen und Einkaufsbereiche vorschlagen. Mengen,
Einheiten, Häkchen und Rezeptquellen kommen ausschließlich aus der Datenbank.
Zusammenführen und Sortieren passieren anschließend deterministisch.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

from .cart_logic import display_amount, prepare_for_cart


SHOPPING_CATEGORIES = (
    "Obst & Gemüse",
    "Bäckerei",
    "Fleisch & Fisch",
    "Kühlregal",
    "Vorrat & Konserven",
    "Getränke",
    "Tiefkühl",
    "Drogerie & Haushalt",
    "Sonstiges",
)
_CATEGORY_ORDER = {name: index for index, name in enumerate(SHOPPING_CATEGORIES)}


def _source_ids(raw: Any) -> List[int]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
    if not isinstance(raw, list):
        return []
    result: List[int] = []
    for value in raw:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in result:
            result.append(item)
    return result


def cart_fingerprint(items: Iterable[Dict[str, Any]]) -> str:
    """Stabiler Fingerprint für den Vorschau-gegen-Aktuell-Check."""
    comparable = []
    for item in sorted(items, key=lambda value: int(value.get("id") or 0)):
        comparable.append({
            "id": int(item.get("id") or 0),
            "name": str(item.get("name") or ""),
            "canonical_name": str(item.get("canonical_name") or ""),
            "amount": item.get("amount"),
            "unit": item.get("unit"),
            "checked": bool(item.get("checked")),
            "added_at": item.get("added_at"),
            "source_recipe_ids": _source_ids(item.get("source_recipe_ids")),
            "category": item.get("category"),
            "sort_order": item.get("sort_order"),
        })
    payload = json.dumps(
        comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_optimized_cart(
    items: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validiert KI-Vorschläge und baut eine verlustfreie neue Liste."""
    by_id: Dict[int, Dict[str, Any]] = {}
    valid_ids = {int(item["id"]) for item in items}
    for suggestion in suggestions[:1000]:
        if not isinstance(suggestion, dict):
            continue
        try:
            item_id = int(suggestion.get("id"))
        except (TypeError, ValueError):
            continue
        if item_id not in valid_ids or item_id in by_id:
            continue
        by_id[item_id] = suggestion

    grouped: Dict[tuple, Dict[str, Any]] = {}
    renamed = 0
    categorized = 0
    for item in items:
        item_id = int(item["id"])
        suggestion = by_id.get(item_id) or {}
        current_name = str(item.get("name") or "?").strip() or "?"
        proposed_name = " ".join(
            str(suggestion.get("name") or current_name).replace("\n", " ").split()
        )[:200] or current_name
        category = str(
            suggestion.get("category") or item.get("category") or "Sonstiges"
        ).strip()
        if category not in _CATEGORY_ORDER:
            category = "Sonstiges"
        if proposed_name.casefold() != current_name.casefold():
            renamed += 1
        if category != (item.get("category") or None):
            categorized += 1

        prepared = prepare_for_cart(proposed_name, item.get("amount"), item.get("unit"))
        key = (
            str(prepared.get("canonical_name") or proposed_name.casefold()),
            prepared.get("unit"),
            bool(item.get("checked")),
            prepared.get("amount") is None,
        )
        source_ids = _source_ids(item.get("source_recipe_ids"))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                **prepared,
                "checked": bool(item.get("checked")),
                "added_at": float(item.get("added_at") or 0),
                "source_recipe_ids": source_ids,
                "source_item_ids": [item_id],
                "category": category,
            }
            continue

        amount = prepared.get("amount")
        if amount is not None:
            existing["amount"] = float(existing.get("amount") or 0) + float(amount)
        existing["added_at"] = max(
            float(existing.get("added_at") or 0), float(item.get("added_at") or 0)
        )
        existing["source_item_ids"].append(item_id)
        for recipe_id in source_ids:
            if recipe_id not in existing["source_recipe_ids"]:
                existing["source_recipe_ids"].append(recipe_id)
        if _CATEGORY_ORDER[category] < _CATEGORY_ORDER[existing["category"]]:
            existing["category"] = category

    for item in grouped.values():
        item["source_recipe_ids"] = sorted(item["source_recipe_ids"])
        item["source_item_ids"] = sorted(item["source_item_ids"])

    optimized = sorted(
        grouped.values(),
        key=lambda item: (
            bool(item.get("checked")),
            _CATEGORY_ORDER.get(str(item.get("category")), len(_CATEGORY_ORDER)),
            str(item.get("name") or "").casefold(),
        ),
    )
    for index, item in enumerate(optimized):
        item["sort_order"] = index

    preview_items = []
    for item in optimized:
        display_value, display_unit = display_amount(item.get("amount"), item.get("unit"))
        preview_items.append({
            **item,
            "amount": display_value,
            "unit": display_unit,
        })

    return {
        "items": optimized,
        "preview_items": preview_items,
        "matched_suggestions": len(by_id),
        "summary": {
            "original_count": len(items),
            "optimized_count": len(optimized),
            "merged_count": max(0, len(items) - len(optimized)),
            "renamed_count": renamed,
            "categorized_count": categorized,
        },
    }
