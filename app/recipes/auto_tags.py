"""Regel-basierte Auto-Tags aus der canonical-Zutaten-Liste.

Hybrid-Strategie: stilistische Tags (italienisch, schnell, kinderfreundlich)
kommen von der KI im `analyze_recipe_content`-Call. Diät/Allergie-Tags
hier — deterministisch, sicherer als KI:

  - Vegan/vegetarisch-Falschlabels bringen Vertrauensverlust. Wenn die
    KI ein Käse-Rezept als "vegan" markiert, glaubt der User uns nicht
    mehr.
  - Allergie-Tags sind potentiell sicherheitsrelevant (z.B. Nuss-Allergie).
    Mit hand-kuratierter Forbidden-Liste haben wir volle Kontrolle.

Schema: nimmt eine Liste von canonical_names rein (aus recipe_ingredients),
gibt eine Liste der zutreffenden Tags raus.

ENTSCHEIDUNG: bei Ungewissheit KEIN Tag setzen. „vegan" wird nur gesetzt
wenn ALLE Zutaten als pflanzlich klassifiziert sind UND mindestens eine
typisch-vegane Zutat dabei ist (vermeidet false positives bei Rezepten
ohne erkennbare Zutaten).
"""
from __future__ import annotations

from typing import List, Optional, Set

# Forbidden-Listen pro Tag — wenn EINE dieser Zutaten in der Liste ist,
# kann der Tag NICHT gesetzt werden. canonical_names sind lowercase.

# Fleisch + Fisch
_NON_VEGETARIAN = {
    "hackfleisch", "rindfleisch", "schweinefleisch", "hähnchen", "huhn",
    "pute", "puter", "ente", "lamm", "kalb", "wurst", "salami", "schinken",
    "speck", "bacon", "leberwurst", "bratwurst",
    "fisch", "lachs", "thunfisch", "kabeljau", "garnele", "scampi",
    "shrimp", "tintenfisch", "krabbe",
}

# Tierische Produkte (vegan-Forbidden = vegetarian-Forbidden ∪ diese)
_ANIMAL_PRODUCTS = {
    "milch", "käse", "frischkäse", "feta", "mozzarella", "parmesan",
    "gouda", "cheddar", "ricotta", "burrata", "halloumi", "camembert",
    "butter", "sahne", "schlagsahne", "schmand", "creme fraiche",
    "crème fraîche", "saure sahne", "kefir", "buttermilch",
    "joghurt", "quark", "skyr",
    "ei", "honig", "gelatine",
}

# Laktose (Untermenge von _ANIMAL_PRODUCTS, ohne Eier/Honig)
_LACTOSE = {
    "milch", "käse", "frischkäse", "feta", "mozzarella", "parmesan",
    "gouda", "cheddar", "ricotta", "burrata", "halloumi", "camembert",
    "butter", "sahne", "schlagsahne", "schmand", "creme fraiche",
    "crème fraîche", "saure sahne", "kefir", "buttermilch",
    "joghurt", "quark", "skyr",
}

# Gluten — vereinfachte Liste der häufigsten Quellen
_GLUTEN = {
    "mehl", "weizenmehl", "roggenmehl", "weizen", "roggen", "gerste", "dinkel",
    "nudeln", "spaghetti", "penne", "tagliatelle", "ravioli", "tortellini",
    "brot", "brötchen", "toast", "baguette", "fladenbrot",
    "couscous", "bulgur", "grieß", "semmelbrösel", "paniermehl",
}

# Eier
_EGGS = {"ei", "eigelb", "eiweiß"}

# Nüsse
_NUTS = {
    "mandeln", "haselnüsse", "walnüsse", "cashew", "cashews",
    "pistazien", "macadamia", "pinienkerne", "erdnüsse", "erdnussbutter",
}


# Positive Marker: typisch-vegane Zutaten — wir setzen "vegan" nur wenn
# wenigstens eine dieser drin ist (sonst false-positive bei "leeres Rezept"
# oder "nur Salz und Wasser").
_VEGAN_INDICATORS = {
    "tomate", "zwiebel", "karotte", "kartoffel", "paprika", "gurke",
    "zucchini", "aubergine", "spinat", "salat", "brokkoli", "blumenkohl",
    "knoblauch", "ingwer", "linse", "linsen", "kichererbse", "kichererbsen",
    "bohne", "bohnen", "tofu", "tempeh", "seitan", "haferflocken",
    "reis", "quinoa", "olivenöl", "rapsöl", "sonnenblumenöl",
    "pasta", "nudeln", "brot", "mehl", "hafermilch", "sojamilch",
    "mandelmilch", "kokosmilch",
}


def compute_diet_tags(canonical_ingredients: List[str]) -> List[str]:
    """Bestimmt deterministisch welche Diät/Allergie-Tags zutreffen.

    Args:
        canonical_ingredients: Liste von canonical_name-Werten aus
            recipe_ingredients. NULL/None-Werte werden ignoriert.

    Returns: Liste von Tag-Namen die zugewiesen werden sollen, z.B.
        ["vegetarisch", "laktosefrei"] oder [].

    Heuristik:
      - "vegan"/"vegetarisch": immer wenn Bedingungen passen UND mindestens
        ein vegan-Indikator drin ist. Diese Tags sind positiv-aussagekräftig.
      - "laktosefrei"/"glutenfrei"/"eifrei"/"nussfrei": nur ab >=5 erkannten
        Zutaten. Sonst Tag-Spam (jedes Salat-Rezept wäre sonst „eifrei +
        nussfrei + glutenfrei", was wertlos ist beim Filtern).
    """
    ings: Set[str] = {
        (c or "").lower().strip()
        for c in canonical_ingredients
        if c
    }
    ings.discard("")

    if not ings:
        return []

    tags: List[str] = []

    is_vegetarian = ings.isdisjoint(_NON_VEGETARIAN)
    is_animal_free = is_vegetarian and ings.isdisjoint(_ANIMAL_PRODUCTS)
    has_vegan_indicator = bool(ings & _VEGAN_INDICATORS)

    if is_animal_free and has_vegan_indicator:
        tags.append("vegan")
    elif is_vegetarian and has_vegan_indicator:
        tags.append("vegetarisch")

    # Free-of-Tags nur ab ausreichend Zutaten — sonst werden sie zu Spam
    if len(ings) >= 5:
        if ings.isdisjoint(_LACTOSE):
            tags.append("laktosefrei")
        if ings.isdisjoint(_GLUTEN):
            tags.append("glutenfrei")
        if ings.isdisjoint(_EGGS):
            tags.append("eifrei")
        if ings.isdisjoint(_NUTS):
            tags.append("nussfrei")

    return tags


# Set aller Tag-Namen die diese Funktion potenziell setzen kann —
# wird vom manuellen Ingredients-PUT genutzt um KI-Stil-Tags und Diät-Tags
# beim auto-Tag-Recompute sauber zu trennen.
DIET_TAGS: frozenset = frozenset({
    "vegan", "vegetarisch", "laktosefrei", "glutenfrei", "eifrei", "nussfrei",
})
