"""Deterministische Stammdaten für Supermarktbereiche und Einkaufs-Icons."""
from __future__ import annotations

import re
from typing import Dict, Optional


SHOPPING_CATEGORY_ICONS: Dict[str, str] = {
    "Obst & Gemüse": "🍎",
    "Bäckerei": "🥖",
    "Fleisch & Fisch": "🥩",
    "Kühlregal": "🥛",
    "Vorrat & Konserven": "🥫",
    "Getränke": "🥤",
    "Tiefkühl": "❄️",
    "Drogerie & Haushalt": "🧴",
    "Sonstiges": "🛒",
}
SHOPPING_CATEGORIES = tuple(SHOPPING_CATEGORY_ICONS)


_CATEGORY_TERMS = {
    "Obst & Gemüse": {
        "apfel", "birne", "banane", "beere", "brokkoli", "champignon",
        "gurke", "ingwer", "kartoffel", "knoblauch", "kohl", "kräuter",
        "lauch", "limette", "möhre", "orange", "paprika", "pilz", "salat",
        "sellerie", "spargel", "spinat", "tomate", "zitrone", "zwiebel",
    },
    "Bäckerei": {
        "baguette", "brötchen", "brot", "croissant", "toast", "tortilla",
    },
    "Fleisch & Fisch": {
        "fleisch", "fisch", "garnelen", "hackfleisch", "hähnchen", "lachs",
        "pute", "rind", "salami", "schinken", "schwein", "speck", "wurst",
    },
    "Kühlregal": {
        "butter", "creme fraiche", "ei", "eier", "feta", "frischkäse",
        "joghurt", "käse", "mascarpone", "milch", "mozzarella", "quark",
        "sahne", "schmand",
    },
    "Vorrat & Konserven": {
        "backpulver", "brühe", "essig", "gewürz", "haferflocken", "honig",
        "kakao", "kichererbse", "konserve", "linsen", "mehl", "nudel", "öl",
        "pasta", "pfeffer", "reis", "salz", "senf", "soße", "tomatenmark",
        "zucker",
    },
    "Getränke": {
        "bier", "cola", "getränk", "kaffee", "saft", "sekt", "tee", "wein",
    },
    "Tiefkühl": {
        "eis", "tiefkühl", "tk",
    },
    "Drogerie & Haushalt": {
        "alufolie", "backpapier", "küchenrolle", "müllbeutel", "reiniger",
        "seife", "spülmittel", "toilettenpapier", "waschmittel",
    },
}


def normalize_shopping_category(value: Optional[str]) -> str:
    candidate = " ".join(str(value or "").split())
    return candidate if candidate in SHOPPING_CATEGORY_ICONS else "Sonstiges"


def infer_shopping_category(name: str, canonical_name: Optional[str] = None) -> str:
    haystack = f" {canonical_name or ''} {name or ''} ".casefold()
    for category, terms in _CATEGORY_TERMS.items():
        if any(
            re.search(
                rf"(?<!\w){re.escape(term.casefold())}{'(?!\\w)' if len(term) <= 2 else '\\w*'}",
                haystack,
            )
            for term in terms
        ):
            return category
    return "Sonstiges"


def category_icon(category: Optional[str]) -> str:
    return SHOPPING_CATEGORY_ICONS[normalize_shopping_category(category)]


def product_defaults(
    name: str,
    canonical_name: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, str]:
    resolved_category = (
        normalize_shopping_category(category)
        if category
        else infer_shopping_category(name, canonical_name)
    )
    return {
        "category": resolved_category,
        "icon": category_icon(resolved_category),
    }
