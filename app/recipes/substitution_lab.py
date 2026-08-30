"""Kuratierte, nachvollziehbare Zutaten-Ersetzungen mit Review-Hinweisen."""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .canonical import canonical_name
from .units import normalize_unit


_CATALOG: tuple[Dict[str, Any], ...] = (
    {
        "id": "milk-oat-drink",
        "sources": ("milch",),
        "replacement_name": "Haferdrink",
        "ratio": 1.0,
        "confidence": "high",
        "functional_effect": "Ähnliche Flüssigkeitsmenge; Geschmack und Bräunung können sich leicht ändern.",
        "allergen_notes": ["Hafer kann Gluten enthalten; Kennzeichnung des Produkts prüfen."],
        "nutrition_notes": ["Eiweiß- und Fettgehalt können niedriger sein als bei Kuhmilch."],
        "blocked_auto_tags": ("glutenfrei",),
    },
    {
        "id": "milk-coconut-milk",
        "sources": ("milch", "sahne", "schlagsahne"),
        "replacement_name": "Kokosmilch",
        "ratio": 1.0,
        "confidence": "medium",
        "functional_effect": "Cremig, aber mit erkennbarem Kokosgeschmack; für herzhafte Saucen prüfen.",
        "allergen_notes": ["Milch kann entfallen; weitere Zutaten und Produktetikett trotzdem prüfen."],
        "nutrition_notes": ["Fettart und Kalorien können deutlich vom Original abweichen."],
    },
    {
        "id": "butter-plant-margarine",
        "sources": ("butter",),
        "replacement_name": "Pflanzenmargarine",
        "ratio": 1.0,
        "confidence": "high",
        "functional_effect": "Meist 1:1 einsetzbar; Wassergehalt beeinflusst Backergebnis und Bräunung.",
        "allergen_notes": ["Nur eine ausdrücklich milchfreie Margarine verwenden; Etikett prüfen."],
        "nutrition_notes": ["Fettsäureprofil hängt stark vom gewählten Produkt ab."],
        "blocked_auto_tags": ("laktosefrei",),
    },
    {
        "id": "egg-applesauce",
        "sources": ("ei", "eier"),
        "replacement_name": "Apfelmus",
        "ratio": 60.0,
        "unit_override": "g",
        "compatible_source_units": (None, "Stück"),
        "source_amount_required": True,
        "confidence": "medium",
        "functional_effect": "Für saftige Backwaren; bindet, lockert aber weniger als Ei.",
        "allergen_notes": ["Ei entfällt nur an dieser Stelle; das gesamte Rezept separat prüfen."],
        "nutrition_notes": ["Weniger Eiweiß und Fett, dafür mehr Kohlenhydrate möglich."],
    },
    {
        "id": "flour-gluten-free-mix",
        "sources": ("mehl", "weizenmehl", "dinkelmehl"),
        "replacement_name": "Glutenfreie Mehlmischung",
        "ratio": 1.0,
        "confidence": "medium",
        "functional_effect": "Nur für eine geeignete Backmischung; Bindung und Flüssigkeitsbedarf können abweichen.",
        "allergen_notes": ["Nur zertifiziert glutenfreie Mischung verwenden und Kreuzkontamination beachten."],
        "nutrition_notes": ["Ballaststoff- und Eiweissgehalt variieren je nach Mischung."],
        "blocked_auto_tags": ("glutenfrei",),
    },
    {
        "id": "yogurt-plant-yogurt",
        "sources": ("joghurt", "skyr", "quark"),
        "replacement_name": "Pflanzenjoghurt natur",
        "ratio": 1.0,
        "confidence": "high",
        "functional_effect": "Konsistenz und Säuregrad können variieren; ungesüßtes Produkt verwenden.",
        "allergen_notes": ["Basisprodukt wie Soja, Hafer oder Nuss sowie dessen Etikett prüfen."],
        "nutrition_notes": ["Eiweißgehalt ist je nach Pflanzenbasis oft niedriger."],
        "blocked_auto_tags": ("glutenfrei", "nussfrei"),
    },
    {
        "id": "parmesan-nutritional-yeast",
        "sources": ("parmesan",),
        "replacement_name": "Hefeflocken",
        "ratio": 0.5,
        "confidence": "medium",
        "functional_effect": "Würzig, schmilzt aber nicht wie Käse; eher zum Abschmecken geeignet.",
        "allergen_notes": ["Milch kann entfallen; weitere Zutaten des Rezepts prüfen."],
        "nutrition_notes": ["Weniger Fett; Salz- und Vitaminwerte sind produktabhaengig."],
    },
    {
        "id": "sugar-honey",
        "sources": ("zucker",),
        "replacement_name": "Honig",
        "ratio": 0.75,
        "confidence": "medium",
        "functional_effect": "Fügt Flüssigkeit hinzu; andere Flüssigkeit im Rezept gegebenenfalls reduzieren.",
        "allergen_notes": ["Honig ist nicht vegan und für Säuglinge ungeeignet."],
        "nutrition_notes": ["Weiterhin eine Zuckerquelle; Kalorienersparnis ist nicht garantiert."],
    },
    {
        "id": "breadcrumbs-oats",
        "sources": ("semmelbrösel", "semmelbroesel", "paniermehl"),
        "replacement_name": "Feine Haferflocken",
        "ratio": 1.0,
        "confidence": "medium",
        "functional_effect": "Bindet anders und bleibt gröber; bei Bedarf vorher mahlen.",
        "allergen_notes": ["Bei Glutenverzicht nur zertifiziert glutenfreie Haferflocken verwenden."],
        "nutrition_notes": ["Ballaststoffgehalt kann steigen; Werte neu berechnen."],
        "blocked_auto_tags": ("glutenfrei",),
    },
)


def _canonical(value: Optional[str]) -> str:
    return str(canonical_name(value) or value or "").casefold().strip()


def _candidate_applicability_error(
    ingredient: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Optional[str]:
    """Liefert den Grund, warum eine mengenabhängige Ersetzung nicht passt."""
    amount = ingredient.get("amount")
    if candidate.get("source_amount_required"):
        if amount is None:
            return "Für diese Ersetzung muss eine Ausgangsmenge angegeben sein"
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError):
            return "Die Ausgangsmenge ist für diese Ersetzung ungültig"
        if not math.isfinite(numeric_amount) or numeric_amount <= 0:
            return "Die Ausgangsmenge muss größer als null sein"

    compatible_units = candidate.get("compatible_source_units")
    if compatible_units:
        source_unit = normalize_unit(ingredient.get("unit"))
        allowed = {normalize_unit(unit) for unit in compatible_units}
        if source_unit not in allowed:
            return "Diese Ersetzung ist nur für Stück-/Anzahlmengen geeignet"
    return None


def _candidate_public(
    candidate: Mapping[str, Any], ingredient: Mapping[str, Any]
) -> Dict[str, Any]:
    replacement_name = str(candidate["replacement_name"])
    public = {
        "id": candidate["id"],
        "replacement_name": replacement_name,
        "replacement_canonical": _canonical(replacement_name),
        "ratio": float(candidate["ratio"]),
        "unit_override": candidate.get("unit_override"),
        "confidence": candidate["confidence"],
        "functional_effect": candidate["functional_effect"],
        "allergen_notes": list(candidate["allergen_notes"]),
        "nutrition_notes": list(candidate["nutrition_notes"]),
        "blocked_auto_tags": list(candidate.get("blocked_auto_tags") or []),
        "requires_review": True,
    }
    public["result_ingredient"] = substituted_ingredient(ingredient, candidate)
    return public


def substitution_candidates(ingredient: Mapping[str, Any]) -> List[Dict[str, Any]]:
    source = _canonical(
        str(ingredient.get("canonical_name") or ingredient.get("name") or "")
    )
    return [
        _candidate_public(candidate, ingredient)
        for candidate in _CATALOG
        if source in {_canonical(item) for item in candidate["sources"]}
        and _candidate_applicability_error(ingredient, candidate) is None
    ]


def substitution_lab_payload(
    recipe: Mapping[str, Any],
    ingredients: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for ingredient in ingredients:
        candidates = substitution_candidates(ingredient)
        if not candidates:
            continue
        items.append({
            "ingredient_id": int(ingredient["id"]),
            "name": ingredient.get("name"),
            "canonical_name": ingredient.get("canonical_name"),
            "amount": ingredient.get("amount"),
            "unit": ingredient.get("unit"),
            "candidates": candidates,
        })
    return {
        "recipe_id": int(recipe["id"]),
        "recipe_name": recipe.get("name"),
        "items": items,
        "automatic_apply": False,
        "medical_safety_claim": False,
    }


def resolve_candidate(
    ingredient: Mapping[str, Any], candidate_id: str
) -> Dict[str, Any]:
    source = _canonical(
        str(ingredient.get("canonical_name") or ingredient.get("name") or "")
    )
    for candidate in _CATALOG:
        if candidate["id"] != candidate_id:
            continue
        if source not in {_canonical(item) for item in candidate["sources"]}:
            break
        error = _candidate_applicability_error(ingredient, candidate)
        if error:
            raise ValueError(error)
        return _candidate_public(candidate, ingredient)
    raise ValueError("Diese Ersetzung ist für die gewählte Zutat nicht freigegeben")


def substituted_ingredient(
    ingredient: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Dict[str, Any]:
    error = _candidate_applicability_error(ingredient, candidate)
    if error:
        raise ValueError(error)
    amount = ingredient.get("amount")
    if amount is not None:
        amount = round(float(amount) * float(candidate["ratio"]), 4)
    unit = normalize_unit(candidate.get("unit_override") or ingredient.get("unit"))
    replacement_name = str(candidate["replacement_name"])
    quantity = "" if amount is None else f"{amount:g} "
    unit_text = "" if not unit else f"{unit} "
    return {
        "name": replacement_name,
        "canonical_name": candidate.get("replacement_canonical") or _canonical(replacement_name),
        "amount": amount,
        "unit": unit,
        "raw": f"{quantity}{unit_text}{replacement_name}".strip(),
    }
