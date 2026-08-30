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

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

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
    "joghurt", "quark", "skyr", "molke", "milchpulver", "süßmolkenpulver",
    "magermilchpulver", "kondensmilch",
}

# Gluten — vereinfachte Liste der häufigsten Quellen
_GLUTEN = {
    "mehl", "weizenmehl", "roggenmehl", "weizen", "roggen", "gerste", "dinkel",
    "hafer", "haferflocken", "hafermehl",
    "nudeln", "spaghetti", "penne", "tagliatelle", "ravioli", "tortellini",
    "brot", "brötchen", "toast", "baguette", "fladenbrot",
    "couscous", "bulgur", "grieß", "semmelbrösel", "paniermehl", "sojasauce",
}

# Eier
_EGGS = {"ei", "eigelb", "eiweiß", "mayonnaise", "eiernudeln"}

# Nüsse
_NUTS = {
    "mandeln", "haselnüsse", "walnüsse", "cashew", "cashews",
    "pistazien", "macadamia", "pinienkerne", "erdnüsse", "erdnussbutter",
    "marzipan", "nougat", "nussnougatcreme", "nutella", "pesto",
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


ALLERGEN_STATUSES: frozenset[str] = frozenset({"frei", "enthält", "unklar"})
ALLERGEN_FREE_TAGS: Dict[str, tuple[str, Set[str]]] = {
    "laktosefrei": ("lactose", _LACTOSE),
    "glutenfrei": ("gluten", _GLUTEN),
    "eifrei": ("egg", _EGGS),
    "nussfrei": ("nuts", _NUTS),
}


def normalize_allergen_info(value: Any) -> Optional[Dict[str, str]]:
    """Normalisiert ein KI-Allergenurteil defensiv.

    Sobald die KI das Feld liefert, wird jeder fehlende oder ungültige Wert
    als ``unklar`` behandelt. Fehlt das gesamte Feld (ältere Analyzer/Mocks),
    bleibt der Rückgabewert ``None`` und die bisherige deterministische Logik
    funktioniert unverändert.
    """
    if not isinstance(value, Mapping):
        return None
    normalized: Dict[str, str] = {}
    for key in ("gluten", "lactose", "egg", "nuts"):
        raw = str(value.get(key) or "").strip().casefold()
        normalized[key] = raw if raw in ALLERGEN_STATUSES else "unklar"
    return normalized


def _contains_source(ingredients: Set[str], sources: Set[str]) -> bool:
    """Erkennt Quellen auch in konkreteren Zutatenbezeichnungen.

    Canonical-Namen im Altbestand sind nicht immer reine Einzelwörter
    (z.B. ``weizenmehl type 405`` oder ``pesto genovese``). Kurze Quellen wie
    ``ei`` werden nur als ganzes Wort geprüft, damit etwa ``reis`` nicht als
    Ei-Quelle gilt. Bei längeren Begriffen ist ein Teiltreffer absichtlich
    konservativ: lieber kein Frei-von-Tag als eine unsichere Positivaussage.
    """
    for ingredient in ingredients:
        tokens = set(re.findall(r"\w+", ingredient, flags=re.UNICODE))
        for source in sources:
            if ingredient == source or source in tokens:
                return True
            if len(source) >= 4 and source in ingredient:
                return True
    return False


def compute_diet_tags(
    canonical_ingredients: List[str],
    allergen_info: Optional[Mapping[str, str]] = None,
    blocked_tags: Optional[Iterable[str]] = None,
) -> List[str]:
    """Bestimmt deterministisch welche Diät/Allergie-Tags zutreffen.

    Args:
        canonical_ingredients: Liste von canonical_name-Werten aus
            recipe_ingredients. NULL/None-Werte werden ignoriert.
        allergen_info: Optionales strukturiertes KI-Urteil. Wenn vorhanden,
            muss die KI für ein Frei-von-Tag zusätzlich eindeutig ``frei``
            melden. ``enthält`` und ``unklar`` wirken nur als Veto; die KI
            kann niemals eine erkannte Allergenquelle überstimmen.
        blocked_tags: Positive Tags, die wegen einer produktabhängigen
            Unsicherheit der konkret gewählten Zutat nicht gesetzt werden dürfen.

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
    blocked = {
        str(tag).strip().casefold()
        for tag in (blocked_tags or [])
        if str(tag).strip()
    }

    is_vegetarian = not _contains_source(ings, _NON_VEGETARIAN)
    is_animal_free = is_vegetarian and not _contains_source(ings, _ANIMAL_PRODUCTS)
    has_vegan_indicator = bool(ings & _VEGAN_INDICATORS)

    if is_animal_free and has_vegan_indicator:
        tags.append("vegan")
    elif is_vegetarian and has_vegan_indicator:
        tags.append("vegetarisch")

    normalized_allergens = normalize_allergen_info(allergen_info)

    # Free-of-Tags nur ab ausreichend Zutaten — sonst werden sie zu Spam.
    # Bei neuen KI-Analysen gilt außerdem ein Zwei-Schlüssel-Prinzip:
    # Regelwerk UND KI müssen die positive Frei-von-Aussage erlauben.
    if len(ings) >= 5:
        for tag_name, (allergen_key, forbidden) in ALLERGEN_FREE_TAGS.items():
            if tag_name in blocked:
                continue
            deterministic_free = not _contains_source(ings, forbidden)
            ai_allows = (
                normalized_allergens is None
                or normalized_allergens[allergen_key] == "frei"
            )
            if deterministic_free and ai_allows:
                tags.append(tag_name)

    return tags


# Set aller Tag-Namen die diese Funktion potenziell setzen kann —
# wird vom manuellen Ingredients-PUT genutzt um KI-Stil-Tags und Diät-Tags
# beim auto-Tag-Recompute sauber zu trennen.
DIET_TAGS: frozenset = frozenset({
    "vegan", "vegetarisch", "laktosefrei", "glutenfrei", "eifrei", "nussfrei",
})
SAFETY_CLAIM_TAGS: frozenset[str] = frozenset(ALLERGEN_FREE_TAGS)


def remove_manual_safety_claim_tags(db: Any, recipe_id: int) -> List[str]:
    """Entfernt geerbte manuelle Frei-von-Claims aus einer neuen Variante."""
    current_tags = db.recipe_tags_get(recipe_id)
    preserved_manual = [
        str(tag["name"])
        for tag in current_tags
        if tag.get("auto") == 0
        and str(tag["name"]).strip().casefold() not in SAFETY_CLAIM_TAGS
    ]
    removed = sorted({
        str(tag["name"]).strip().casefold()
        for tag in current_tags
        if tag.get("auto") == 0
        and str(tag["name"]).strip().casefold() in SAFETY_CLAIM_TAGS
    })
    db.recipe_tags_set(recipe_id, preserved_manual)
    return removed


def refresh_diet_auto_tags(
    db: Any,
    recipe_id: int,
    canonical_ingredients: List[str],
    *,
    blocked_tags: Optional[Iterable[str]] = None,
) -> List[str]:
    """Ersetzt nur Diät-Auto-Tags und bewahrt stilistische KI-Tags."""
    current_tags = db.recipe_tags_get(recipe_id)
    non_diet_auto = [
        tag["name"]
        for tag in current_tags
        if tag.get("auto") == 1 and tag["name"] not in DIET_TAGS
    ]
    merged = sorted(
        set(non_diet_auto)
        | set(compute_diet_tags(canonical_ingredients, blocked_tags=blocked_tags))
    )
    db.recipe_auto_tags_set(recipe_id, merged)
    return merged


def backfill_diet_auto_tags_connection(connection: Any) -> Dict[str, Any]:
    """Zieht Diät-/Allergiker-Auto-Tags im Altbestand transaktional nach.

    Die Funktion verwendet absichtlich die Connection des Aufrufers, damit
    sie sowohl in einer Schema-Migration als auch in einem Admin-Lauf atomar
    ausgeführt werden kann. Manuelle Tags und stilistische Auto-Tags werden
    nicht verändert.
    """
    rows = connection.execute(
        "SELECT r.id FROM recipes r WHERE r.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id=r.id) "
        "ORDER BY r.id"
    ).fetchall()
    assigned = {name: 0 for name in ALLERGEN_FREE_TAGS}
    changed = 0
    recipes_with_allergen_info = 0
    skipped_too_few_ingredients = 0

    diet_names = sorted(DIET_TAGS)
    diet_slots = ",".join("?" for _ in diet_names)
    for row in rows:
        recipe_id = int(row[0])
        ingredient_rows = connection.execute(
            "SELECT canonical_name FROM recipe_ingredients "
            "WHERE recipe_id=? AND TRIM(COALESCE(canonical_name, ''))<>''",
            (recipe_id,),
        ).fetchall()
        canonical = [ingredient[0] for ingredient in ingredient_rows]
        distinct_count = len({str(item).strip().casefold() for item in canonical if item})
        if distinct_count < 5:
            skipped_too_few_ingredients += 1

        desired = set(compute_diet_tags(canonical))
        allergen_tags = desired & set(ALLERGEN_FREE_TAGS)
        if allergen_tags:
            recipes_with_allergen_info += 1
            for tag_name in allergen_tags:
                assigned[tag_name] += 1

        current_auto = {
            str(tag_row[0]).casefold()
            for tag_row in connection.execute(
                "SELECT t.name FROM recipe_tags rt JOIN tags t ON t.id=rt.tag_id "
                f"WHERE rt.recipe_id=? AND rt.auto=1 AND lower(t.name) IN ({diet_slots})",
                (recipe_id, *diet_names),
            ).fetchall()
        }
        manual = {
            str(tag_row[0]).casefold()
            for tag_row in connection.execute(
                "SELECT t.name FROM recipe_tags rt JOIN tags t ON t.id=rt.tag_id "
                f"WHERE rt.recipe_id=? AND rt.auto=0 AND lower(t.name) IN ({diet_slots})",
                (recipe_id, *diet_names),
            ).fetchall()
        }
        desired_auto = desired - manual
        if current_auto != desired_auto:
            changed += 1

        connection.execute(
            "DELETE FROM recipe_tags WHERE recipe_id=? AND auto=1 AND tag_id IN "
            f"(SELECT id FROM tags WHERE lower(name) IN ({diet_slots}))",
            (recipe_id, *diet_names),
        )
        for tag_name in sorted(desired_auto):
            connection.execute(
                "INSERT OR IGNORE INTO tags(name) VALUES (?)",
                (tag_name,),
            )
            tag_id = connection.execute(
                "SELECT id FROM tags WHERE name=? COLLATE NOCASE",
                (tag_name,),
            ).fetchone()[0]
            connection.execute(
                "INSERT OR IGNORE INTO recipe_tags(recipe_id, tag_id, auto) "
                "VALUES (?, ?, 1)",
                (recipe_id, tag_id),
            )

    return {
        "ok": True,
        "recipes_checked": len(rows),
        "recipes_with_allergen_info": recipes_with_allergen_info,
        "recipes_changed": changed,
        "skipped_too_few_ingredients": skipped_too_few_ingredients,
        "assigned": assigned,
    }


def backfill_diet_auto_tags(db: Any) -> Dict[str, Any]:
    """Wiederholbarer Admin-Backfill mit einer gemeinsamen Transaktion."""
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        return backfill_diet_auto_tags_connection(connection)
