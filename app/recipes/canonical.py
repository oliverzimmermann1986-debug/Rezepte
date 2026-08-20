"""Normalisierung von Zutaten-Namen → canonical_name.

Zweck: damit „Tomate", „Tomaten", „TOMATE" und „tomate" auf denselben
Token kollabieren, sodass:
  * der Filter „Zutat = Tomate" alle vier findet
  * der Einkaufskorb sie zu EINEM Eintrag mergen kann
  * die Ingredient-Übersicht im Frontend nicht 3x „Tomate" zeigt

Strategie (bewusst minimal, 80%-Lösung):
  1. lowercase, trim, Adjektive vorne ("frische", "große", "kleine") entfernen
  2. Synonyme über kleine Hardcoded-Map auflösen
     (Knoblauchzehe → knoblauch, Zwiebel → zwiebel, Eier → ei)
  3. Heuristik für Pluralformen — die häufigsten deutschen Endungen
     in dieser Reihenfolge abschneiden:
        -nnen → "Innen" als Plural von "Köchinnen" — kommt bei Zutaten nicht vor, ignoriert
        -innen → wie oben
        -etten → "Frikadetten" → "Frikade"? nein zu fragil — wir nehmen nur die sicheren
        -en  → "Tomaten" → "Tomate"  ✓
        -er  → "Eier"    → "Ei"      ✓
        -e   → "Kartoffe" — falsch! "Kartoffel" wird zu "Kartoff" → schlecht
  4. Daher: -e wird NUR abgeschnitten wenn das Wort danach noch ≥3 Buchstaben hat
     UND der Stamm in einem kleinen Whitelist-Set ist.

Lieber underdetect (zwei Eintrage mit ähnlichen Namen) als overcollapse
(„Kartoffel" wird zu „Kartoff" und matched mit nichts).

Wenn der User später bessere Map will: einfach `_SYNONYMS` erweitern oder
über die DB pflegen — der Algorithmus bleibt gleich.
"""
from __future__ import annotations

import re
from typing import Optional

TOMATO_CANONICAL = "tomate"
TOMATO_SHOPPING_NAME = "Tomaten"
TOMATO_CANONICAL_ALIASES = frozenset({
    "tomate",
    "tomaten",
    "cherrytomate",
    "cherrytomaten",
    "cherry tomate",
    "cherry tomaten",
    "cherry-tomate",
    "cherry-tomaten",
    "cocktailtomate",
    "cocktailtomaten",
    "cocktail tomate",
    "cocktail tomaten",
    "cocktail-tomate",
    "cocktail-tomaten",
    "kirschtomate",
    "kirschtomaten",
    "kirsch tomate",
    "kirsch tomaten",
    "kirsch-tomate",
    "kirsch-tomaten",
})

# Vorne dranhängende Adjektive / Qualifier, die wir entfernen.
_ADJECTIVE_PREFIXES = [
    "frische", "frischer", "frisches", "frisch",
    "große", "großer", "großes", "groß", "klein", "kleine", "kleiner", "kleines",
    "reife", "reifer", "reifes", "reif",
    "getrocknete", "getrockneter", "getrocknetes", "getrocknet",
    "gefrorene", "gefrorener", "gefrorenes", "gefroren", "tk",
    "geriebene", "geriebener", "geriebenes", "gerieben",
    "geschälte", "geschälter", "geschältes", "geschält",
    "gewürfelte", "gewürfelter", "gewürfeltes", "gewürfelt",
    "gehackte", "gehackter", "gehacktes", "gehackt",
    "rote", "roter", "rotes", "rot",
    "grüne", "grüner", "grünes", "grün",
    "gelbe", "gelber", "gelbes", "gelb",
    "weiße", "weißer", "weißes", "weiß",
    "bio",
]

# Hand-kuratierte Synonyme. Key = bereits adjektiv-bereinigter lowercase-Form.
_SYNONYMS = {
    # Verschiedene Schreibweisen
    **{alias: TOMATO_CANONICAL for alias in TOMATO_CANONICAL_ALIASES},
    "ei": "ei",
    "eier": "ei",
    "zwiebel": "zwiebel",
    "zwiebeln": "zwiebel",
    "kartoffel": "kartoffel",
    "kartoffeln": "kartoffel",
    "knoblauch": "knoblauch",
    "knoblauchzehe": "knoblauch",
    "knoblauchzehen": "knoblauch",
    "knobi": "knoblauch",
    "möhre": "karotte",
    "möhren": "karotte",
    "karotte": "karotte",
    "karotten": "karotte",
    "paprika": "paprika",
    "paprikas": "paprika",
    "gurke": "gurke",
    "gurken": "gurke",
    "zucchini": "zucchini",
    "aubergine": "aubergine",
    "auberginen": "aubergine",
    "champignon": "champignon",
    "champignons": "champignon",
    "pilze": "pilz",
    "pilz": "pilz",
    # Käse / Milchprodukte
    "milch": "milch",
    "butter": "butter",
    "sahne": "sahne",
    "schlagsahne": "sahne",
    "schmand": "schmand",
    "joghurt": "joghurt",
    "jogurt": "joghurt",
    "frischkäse": "frischkäse",
    "feta": "feta",
    "mozzarella": "mozzarella",
    "parmesan": "parmesan",
    "käse": "käse",
    # Fleisch
    "hackfleisch": "hackfleisch",
    "hack": "hackfleisch",
    "hähnchen": "hähnchen",
    "hähnchenbrust": "hähnchen",
    "hühnerbrust": "hähnchen",
    "rindfleisch": "rindfleisch",
    "rind": "rindfleisch",
    "schweinefleisch": "schweinefleisch",
    "schwein": "schweinefleisch",
    "speck": "speck",
    "bacon": "speck",
    "schinken": "schinken",
    # Kohlenhydrate
    "nudeln": "nudeln",
    "pasta": "nudeln",
    "spaghetti": "nudeln",
    "penne": "nudeln",
    "reis": "reis",
    "brot": "brot",
    "mehl": "mehl",
    # Gewürze + Basics
    "salz": "salz",
    "pfeffer": "pfeffer",
    "zucker": "zucker",
    "öl": "öl",
    "olivenöl": "olivenöl",
    "sonnenblumenöl": "öl",
    "rapsöl": "öl",
    "essig": "essig",
    "balsamico": "essig",
    "honig": "honig",
    "senf": "senf",
    "ketchup": "ketchup",
    "tomatenmark": "tomatenmark",
    "passierte tomaten": "passierte tomaten",
    # Kräuter
    "basilikum": "basilikum",
    "petersilie": "petersilie",
    "schnittlauch": "schnittlauch",
    "thymian": "thymian",
    "rosmarin": "rosmarin",
    "oregano": "oregano",
    "dill": "dill",
    # Sonstiges
    "wasser": "wasser",
    "brühe": "brühe",
    "gemüsebrühe": "brühe",
    "fleischbrühe": "brühe",
    "hühnerbrühe": "brühe",
    "wein": "wein",
    "weißwein": "wein",
    "rotwein": "wein",
}

# Whitelist für die unsichere -e/-en/-er-Endungs-Heuristik. Worte deren
# Singular zwingend in einer dieser Endungen aufgeht.
_PLURAL_OK_STEMS = {
    "tomate", "zwiebel", "kartoffel", "karotte", "möhre", "paprika",
    "gurke", "aubergine", "champignon", "ei", "nudel", "frikadelle",
    "kichererbse", "linse", "bohne", "erbse", "minze", "olive",
    "scheibe", "zehe", "tasse", "dose",
}


def _strip_adjectives(text: str) -> str:
    """„frische große Tomaten" → „Tomaten"."""
    parts = text.split()
    while parts and parts[0].lower() in _ADJECTIVE_PREFIXES:
        parts.pop(0)
    return " ".join(parts)


def _strip_plural(stem: str) -> str:
    """Heuristik. Operiert auf bereits lowercase-Eingabe."""
    # -nen: "Tomatinnen" gibt's nicht — skip
    # -en: "Tomaten" → "Tomate" wenn Stamm ≥3 chars und whitelist ok
    if stem.endswith("en") and len(stem) >= 5:
        candidate = stem[:-1]  # "Tomaten" → "Tomate"
        if candidate in _PLURAL_OK_STEMS:
            return candidate
        candidate2 = stem[:-2]  # "Pilzen" → "Pilz"
        if candidate2 in _PLURAL_OK_STEMS:
            return candidate2
    if stem.endswith("er") and len(stem) >= 4:
        candidate = stem[:-2]  # "Eier" → "Ei"
        if candidate in _PLURAL_OK_STEMS:
            return candidate
    if stem.endswith("n") and len(stem) >= 4:
        candidate = stem[:-1]  # "Kartoffeln" → "Kartoffel"
        if candidate in _PLURAL_OK_STEMS:
            return candidate
    return stem


def canonical_name(name: Optional[str]) -> Optional[str]:
    """Hauptfunktion. None-tolerant.
    Bei leerer Eingabe oder reinen Sonderzeichen → None."""
    if not name:
        return None
    text = re.sub(r"[^\w\säöüÄÖÜß\-]", " ", str(name)).strip()
    if not text:
        return None
    text = _strip_adjectives(text).lower()
    # Synonym-Direkthit
    if text in _SYNONYMS:
        return _SYNONYMS[text]
    # Plural-Heuristik
    text = _strip_plural(text)
    if text in _SYNONYMS:
        return _SYNONYMS[text]
    return text or None
