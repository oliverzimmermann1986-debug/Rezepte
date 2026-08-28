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
        "apfel", "avocado", "banane", "basilikum", "beere", "birne",
        "brokkoli", "champignon", "chili", "dill", "eisbergsalat", "feldsalat",
        "frühlingszwiebel", "gurke", "ingwer", "jalapeño", "karotte", "kartoffel",
        "kerbel", "knoblauch", "kohl", "koriander", "kresse", "kräuter", "kürbis",
        "lauch", "limette", "minze", "möhre", "orange", "oregano", "pak choi",
        "paprika", "petersilie", "pilz", "porree", "radieschen", "rucola", "salat",
        "schalotte", "schnittlauch", "sellerie", "spargel", "spinat", "sprossen",
        "süßkartoffel", "thymian", "tomate", "zitrone", "zucchini", "zuckerschote",
        "zwiebel",
    },
    "Bäckerei": {
        "bagel", "baguette", "brötchen", "brot", "burger bun", "croissant",
        "fladenbrot", "lavashbrot", "pita", "toast", "tortilla", "wrap",
    },
    "Fleisch & Fisch": {
        "fleisch", "fisch", "garnelen", "guanciale", "hackfleisch", "hähnchen",
        "lachs", "muscheln", "pancetta", "prosciutto", "pute", "rind", "salami",
        "schinken", "schnitzel", "schwein", "speck", "thunfisch", "wurst",
    },
    "Kühlregal": {
        "burrata", "butter", "cheddar", "comté", "creme fraiche", "feta",
        "frischkäse", "gouda", "halloumi", "hartkäse", "hüttenkäse", "joghurt",
        "käse", "mascarpone", "milch", "mozzarella", "ofenkäse", "parmesan",
        "pecorino", "philadelphia", "quark", "ricotta", "sahne", "sauerrahm",
        "schmand", "schmelzkäse", "skyr", "sour cream", "tofu",
    },
    "Vorrat & Konserven": {
        "ahornsirup", "backpulver", "brühe", "cashew", "cayennepfeffer", "cumin",
        "curry", "datteln", "ei",
        "eierspätzle", "eigelb", "eiklar", "eiweiß", "erbse", "erdnuss",
        "essig", "gewürz", "haferflocken", "hefe", "honig", "kakao", "kapern",
        "garam masala", "kichererbse", "konserve", "kreuzkümmel", "kurkuma", "linse",
        "mais", "mehl", "nachos", "nori",
        "nudel", "olive", "öl", "panko", "pasta", "pfeffer", "pinienkern",
        "pistazie", "reis", "salz", "senf", "sesam", "soße", "spätzle",
        "stärke", "sumak", "tomatenmark", "walnuss", "zimt", "zucker",
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

# Produktformen werden vor den Grundzutaten geprüft. So landet
# "Kartoffelgewürz" im Regal und nicht beim Gemüse, "Avocadoöl" nicht neben
# frischen Avocados und "Fischstäbchen" zuverlässig im Tiefkühlbereich.
_CATEGORY_FRAGMENTS = {
    "Tiefkühl": {
        "edamame", "fischstäbchen", "gefroren", "rahmspinat", "tiefkühl",
    },
    "Vorrat & Konserven": {
        "agavendicksaft", "balsamico", "barbecue", "brösel", "brühe", "chutney",
        "bechamel", "cashew", "chipotle", "cornflakes", "croutons", "currypaste", "dressing",
        "erdnuss", "essig", "farfalle", "flocken", "fond", "fondor", "glace", "gewürz",
        "harissa", "hefe", "hefeflocken", "hoisin", "hollandaise", "kakao", "ketchup",
        "knobilicious", "kokosmilch",
        "konserve", "kumpir", "mayo", "mehl", "miracel", "miso", "mirin", "nachos",
        "muskat", "nori", "nudel", "nutella", "öl", "orzo", "panko", "panier",
        "paprikapulver", "passiert", "pasta", "paste", "pesto", "pinien", "pistaz",
        "pulver", "reis", "rigatoni", "röstzwiebel", "sambal",
        "sauce", "senf", "siracha", "sojasauce", "sojasoße", "soße", "sosse",
        "sriracha", "stärke", "sumak", "tamari", "teriyaki", "vegeta", "vanille",
        "schoko", "sesam", "stückig", "tagliatelle", "udon", "waln", "worcester",
        "würz", "zucker", "zwiebelsuppe",
    },
    "Kühlregal": {
        "alpro", "burrata", "butter", "cheddar", "comté", "creme", "cremfine", "crème",
        "créme", "cuisine", "emmentaler", "feta", "frischkäse", "gnocchi", "gouda",
        "grana", "halloumi", "hafersahne",
        "hirtenkäse", "hüttenkäse", "joghurt", "käse", "kochcreme", "kochsahne",
        "manti", "mascarpone", "maultasche", "milch", "mozzarella", "ofenkäse",
        "parmesan", "pecorino", "philadelphia", "pizzateig", "quark", "ravioli",
        "ricotta", "sahne", "sauerrahm", "schmand", "schmelzkäse", "schupfnudel",
        "skyr", "sourcream", "tofu", "tortellini",
    },
    "Obst & Gemüse": {
        "apfel", "avocado", "banane", "basilikum", "birne", "brokkoli", "champignon",
        "chili", "dill", "gurke", "ingwer", "jalape", "karotte", "kartoffel", "kerbel",
        "drilling", "gemüse", "knoblauch", "kohl", "koriander", "kresse", "kräuter", "kürbis", "lauch",
        "limette", "marone", "minze", "möhre", "oregano", "paprika", "peperoni", "petersilie", "pilz",
        "porree", "radies", "rucola", "salat", "schalotte", "schnittlauch", "sellerie",
        "spargel", "spinat", "sprossen", "süßkartoffel", "thymian", "tomate", "zitrone",
        "wirsing", "zucchini", "zuckerschote", "zwiebel",
    },
    "Bäckerei": {
        "bagel", "baguette", "brötchen", "brot", "bun", "croissant", "fladenbrot",
        "lavash", "pita", "toast", "tortilla", "wrap",
    },
    "Fleisch & Fisch": {
        "bacon", "fisch", "garnel", "guanciale", "hack", "hähnchen", "lachs",
        "muschel", "pancetta", "prosciutto", "rind", "salami", "schinken", "schnitzel",
        "schwein", "speck", "thunfisch", "wurst", "würstchen",
    },
    "Getränke": {
        "mineralwasser", "saft", "sake", "wein",
    },
}

_CATALOG_EXCLUDED_PRODUCTS = {
    "essiggurkenwasser",
    "gurkenwasser",
    "manti-kochwasser",
    "nudelwasser",
    "pastawasser",
    "wasser",
}

_INFLECTION_SUFFIXES = ("", "e", "en", "er", "n", "s")


def _matches_product_term(haystack: str, term: str) -> bool:
    r"""Matcht Produktwörter, ohne beliebige Zusammensetzungen mitzunehmen.

    Die alte ``term\w*``-Regel machte beispielsweise aus Eisbergsalat ein
    Tiefkühlprodukt und aus Eierspätzle ein Ei. Übliche deutsche Pluralformen
    bleiben erlaubt, müssen aber am Wortende stehen.
    """
    clean_term = " ".join(term.casefold().split())
    escaped = re.escape(clean_term).replace(r"\ ", r"\s+")
    # Der Plural von "Ei" ist "Eier". Ein generisches -s würde hingegen
    # fälschlich das eigenständige Produkt "Eis" verschlucken.
    allowed_suffixes = ("", "er") if clean_term == "ei" else _INFLECTION_SUFFIXES
    suffixes = "|".join(re.escape(suffix) for suffix in allowed_suffixes)
    return re.search(rf"(?<!\w){escaped}(?:{suffixes})(?!\w)", haystack) is not None


def _compact_product_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE)


def is_shopping_catalog_candidate(
    name: str,
    canonical_name: Optional[str] = None,
) -> bool:
    """Blendet reine Koch-Hilfszutaten aus den Kaufvorschlägen aus."""
    values = {
        " ".join(str(value or "").casefold().split())
        for value in (name, canonical_name)
        if str(value or "").strip()
    }
    return bool(values) and not any(value in _CATALOG_EXCLUDED_PRODUCTS for value in values)


def normalize_shopping_category(value: Optional[str]) -> str:
    candidate = " ".join(str(value or "").split())
    return candidate if candidate in SHOPPING_CATEGORY_ICONS else "Sonstiges"


def infer_shopping_category(name: str, canonical_name: Optional[str] = None) -> str:
    haystack = f" {canonical_name or ''} {name or ''} ".casefold()
    compact = _compact_product_text(haystack)
    if _matches_product_term(haystack, "tk"):
        return "Tiefkühl"
    for category, fragments in _CATEGORY_FRAGMENTS.items():
        if any(_compact_product_text(fragment) in compact for fragment in fragments):
            return category
    for category, terms in _CATEGORY_TERMS.items():
        if any(_matches_product_term(haystack, term) for term in terms):
            return category
    return "Sonstiges"


def repaired_shopping_category(
    name: str,
    canonical_name: Optional[str],
    current_category: Optional[str],
) -> str:
    """Korrigiert nur sichere Alt-Klassifizierungen.

    Explizit gewählte, bereits konkrete Bereiche bleiben erhalten. Neben
    ehemals unbekannten Artikeln werden nur die bekannten Fehlpaare aus der
    früheren Präfixlogik migriert.
    """
    current = normalize_shopping_category(current_category)
    inferred = infer_shopping_category(name, canonical_name)
    if current == "Sonstiges" and inferred != "Sonstiges":
        return inferred
    haystack = f" {canonical_name or ''} {name or ''} ".casefold()
    is_legacy_egg = any(
        _matches_product_term(haystack, term)
        for term in ("ei", "eierspätzle", "eigelb", "eiklar", "eiweiß", "spätzle")
    )
    if current == "Kühlregal" and inferred == "Vorrat & Konserven" and is_legacy_egg:
        return inferred
    if (
        current == "Tiefkühl"
        and inferred == "Obst & Gemüse"
        and _matches_product_term(haystack, "eisbergsalat")
    ):
        return inferred
    return current


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
