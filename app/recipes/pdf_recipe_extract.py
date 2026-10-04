"""Strukturierte Rezeptdaten aus PDF-/OCR-Text extrahieren.

Die Pipeline kombiniert einen deterministischen lokalen Parser mit dem bereits
konfigurierten KI-Analyzer. Dadurch werden klassische Zutatenlisten auch dann
gefunden, wenn OpenAI vorübergehend nicht erreichbar ist; bei verfügbarer KI
kommen zusätzlich Schritte, Portionen und Tags hinzu.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .auto_tags import compute_diet_tags
from .canonical import canonical_name
from .units import normalize_unit
from .quantities import AMOUNT_PATTERN, parse_amount

logger = logging.getLogger(__name__)

_SECTION_INGREDIENTS = re.compile(
    r"^\s*(?:zutaten(?:liste)?|ingredients?|du\s+brauchst|einkaufsliste)\s*:??\s*$",
    re.IGNORECASE,
)
_SECTION_STOP = re.compile(
    r"^\s*(?:zubereitung|zubereitungs?schritte?|anleitung|ubereitung|instructions?|"
    r"preparation|nährwerte|naehrwerte|nutrition|tipps?|hinweise?|notes?)\s*:??\s*$",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-–—•▪◦*✓☐]\s*|\d+[.)]\s+)")
_UNIT_PATTERN = (
    r"kg|g|mg|l|ml|cl|dl|tl|el|esslöffel|essloeffel|teelöffel|teeloeffel|"
    r"stück|stueck|stk\.?|prise[n]?|bund|zehe[n]?|scheibe[n]?|blatt|blätter|blaetter|"
    r"päckchen|paeckchen|pck\.?|packung|dose[n]?|tasse[n]?|flasche[n]?|glas|gläser|glaeser"
)
_AMOUNT = AMOUNT_PATTERN
_INGREDIENT_LINE = re.compile(
    rf"^\s*(?P<amount>{_AMOUNT})?\s*(?:(?P<unit>{_UNIT_PATTERN})(?=\s|$))?\s*(?P<name>[^:;]{{2,120}}?)\s*$",
    re.IGNORECASE,
)


@dataclass
class ExtractedRecipeData:
    text: str = ""
    ingredients: List[Dict[str, Any]] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    servings: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    allergen_info: Optional[Dict[str, str]] = None
    method: str = "none"
    warnings: List[str] = field(default_factory=list)
    needs_review: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "ingredients": self.ingredients,
            "steps": self.steps,
            "servings": self.servings,
            "tags": self.tags,
            "allergen_info": self.allergen_info,
            "method": self.method,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


def extract_pdf_text(source: bytes | Path, *, max_chars: int = 60000) -> str:
    """Liest den Text-Layer einer PDF. OCR-Textlayer aus der PDF-Aufbereitung
    werden dabei automatisch berücksichtigt. Fehler werden als leerer Text
    behandelt, damit ein einzelnes defektes Dokument keinen Batch abbricht."""
    try:
        import pymupdf
        if isinstance(source, Path):
            doc = pymupdf.open(str(source))
        else:
            doc = pymupdf.open(stream=source, filetype="pdf")
        try:
            parts: List[str] = []
            used = 0
            for page in doc:
                text = (page.get_text("text") or "").strip()
                if not text:
                    continue
                remaining = max_chars - used
                if remaining <= 0:
                    break
                parts.append(text[:remaining])
                used += min(len(text), remaining)
            return "\n\n".join(parts).strip()
        finally:
            doc.close()
    except Exception as exc:
        logger.warning("PDF-Text konnte nicht gelesen werden: %s", exc)
        return ""


def _amount_value(raw: Optional[str]) -> Optional[float]:
    return parse_amount(raw)


def _clean_ingredient_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" .,:;–—-")
    value = re.sub(r"\s*\([^)]*(?:optional|nach geschmack|zum garnieren)[^)]*\)\s*$", "", value, flags=re.I)
    return value.strip()


def parse_ingredient_lines(text: str) -> List[Dict[str, Any]]:
    """Konservativer lokaler Zutatenparser für typische PDF-Listen.

    Es werden vorrangig Zeilen in einem erkannten Zutatenabschnitt verwendet.
    Ohne Überschrift akzeptiert der Parser nur Zeilen mit Menge oder Einheit,
    damit Fließtext nicht fälschlich als Zutatenliste gespeichert wird.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    ingredients: List[Dict[str, Any]] = []
    in_section = False
    section_seen = False

    for raw_line in lines:
        if not raw_line:
            continue
        if _SECTION_INGREDIENTS.match(raw_line):
            in_section = True
            section_seen = True
            continue
        if in_section and _SECTION_STOP.match(raw_line):
            break

        line = _BULLET.sub("", raw_line).strip()
        if not line or len(line) > 150:
            continue
        match = _INGREDIENT_LINE.match(line)
        if not match:
            continue
        amount_raw = match.group("amount")
        unit_raw = match.group("unit")
        name = _clean_ingredient_name(match.group("name") or "")
        if not name or len(name) < 2:
            continue
        # Ohne klare Zutatenüberschrift nur starke Signale akzeptieren.
        if not in_section and not amount_raw and not unit_raw:
            continue
        # Überschriften und typische Anweisungszeilen aussortieren.
        if _SECTION_STOP.match(name) or re.match(r"^(?:alles|danach|anschließend|zuerst|nun)\b", name, re.I):
            continue
        # Reine Zeit-/Temperaturangaben sind keine Zutaten.
        if re.search(r"\b(?:minuten?|stunden?|grad|°c)\b", name, re.I) and not unit_raw:
            continue
        unit = normalize_unit(unit_raw)
        item = {
            "name": name,
            "canonical_name": canonical_name(name),
            "amount": _amount_value(amount_raw),
            "unit": unit,
            "raw": raw_line,
        }
        ingredients.append(item)

    # Deduplizieren, Reihenfolge erhalten.
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in ingredients:
        key = (item.get("canonical_name") or item["name"].casefold(), item.get("amount"), item.get("unit"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:120]


def prepare_recipe_ingredients(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        amount = item.get("amount")
        if amount is not None:
            try:
                amount = float(amount)
                if not math.isfinite(amount) or amount < 0:
                    amount = None
            except (TypeError, ValueError):
                amount = None
        normalized = {
            "name": name,
            "canonical_name": canonical_name(name),
            "amount": amount,
            "unit": normalize_unit(item.get("unit")),
            "raw": (str(item.get("raw") or "").strip() or None),
        }
        # KI-Antworten enthalten gelegentlich dieselbe Zutat mehrfach. Exakt
        # gleiche Mengen/Einheiten werden zusammengeführt; dieselbe Zutat mit
        # einer anderen Menge (z.B. Teig und Füllung) bleibt bewusst erhalten.
        key = (
            normalized["canonical_name"] or name.casefold(),
            normalized["amount"],
            normalized["unit"],
        )
        if key in seen:
            continue
        seen.add(key)
        prepared.append(normalized)
    return prepared[:120]


def extract_recipe_data(text: str, *, analyzer=None,
                        existing_tags: Optional[List[str]] = None,
                        existing_canonical: Optional[List[str]] = None) -> ExtractedRecipeData:
    """Extrahiert strukturierte Daten aus PDF-/OCR-Text.

    Der lokale Parser liefert immer einen Fallback. Wenn ein Analyzer vorhanden
    ist, wird dessen kombiniertes Rezept-Schema genutzt und ein leeres/kaputtes
    KI-Ergebnis automatisch durch den lokalen Parser ergänzt.
    """
    clean_text = (text or "").strip()
    local = parse_ingredient_lines(clean_text)
    result = ExtractedRecipeData(text=clean_text, ingredients=local,
                                 method="local" if local else "none")
    if not clean_text:
        result.warnings.append("PDF enthält keinen lesbaren Textlayer")
        return result

    if analyzer is None:
        if not local:
            result.warnings.append("Keine eindeutige Zutatenliste erkannt")
        return result

    try:
        content = analyzer.analyze_recipe_content(
            clean_text,
            existing_tags=existing_tags or [],
            existing_canonical=existing_canonical or [],
        )
    except Exception as exc:
        logger.warning("KI-Rezeptauswertung fehlgeschlagen: %s", exc)
        result.warnings.append(f"KI-Auswertung fehlgeschlagen: {exc}")
        return result

    if not isinstance(content, dict):
        result.warnings.append("KI lieferte keine strukturierten Rezeptdaten")
        return result

    raw_ingredients = content.get("ingredients") or []
    if not isinstance(raw_ingredients, list):
        result.warnings.append("KI-Zutatenformat muss geprüft werden")
        result.needs_review = True
        return result
    ai_ingredients = prepare_recipe_ingredients(raw_ingredients)
    from .extraction_evidence import review_reasons
    reasons = review_reasons(content, clean_text, threshold=getattr(analyzer, "confidence_threshold", .75))
    result.needs_review = bool(reasons)
    result.warnings.extend(reasons)
    if ai_ingredients:
        # Der lokale Quelltext darf nicht durch unbelegte KI-Zutaten ersetzt
        # werden. Ohne lokale Liste bleiben Vorschläge als Fehler/Entwurf sichtbar.
        result.ingredients = local if reasons and local else ai_ingredients
        result.method = "ai+local" if local else "ai"
    elif local:
        result.warnings.append("KI erkannte keine Zutaten; lokaler Parser wurde verwendet")
        result.needs_review = True

    steps: List[Dict[str, Any]] = []
    step_indexes: Dict[str, int] = {}
    for item in content.get("steps") or []:
        if not isinstance(item, dict):
            continue
        instruction = str(item.get("instruction") or "").strip()
        if not instruction:
            continue
        timer = item.get("timer_seconds")
        try:
            timer = int(timer) if timer is not None and int(timer) > 0 else None
        except (TypeError, ValueError):
            timer = None
        step_key = " ".join(instruction.split()).casefold()
        duplicate_index = step_indexes.get(step_key)
        if duplicate_index is not None:
            # Wenn nur eine der beiden KI-Kopien einen Timer enthält, bewahren
            # wir die informativere Variante auf.
            if steps[duplicate_index]["timer_seconds"] is None and timer is not None:
                steps[duplicate_index]["timer_seconds"] = timer
            continue
        step_indexes[step_key] = len(steps)
        steps.append({"instruction": instruction, "timer_seconds": timer})
    result.steps = steps[:100]

    servings = content.get("servings")
    try:
        servings = int(servings) if servings is not None and int(servings) > 0 else None
    except (TypeError, ValueError):
        servings = None
    result.servings = servings
    result.tags = sorted({str(tag).strip() for tag in (content.get("tags") or []) if str(tag).strip()})[:60]
    from .auto_tags import normalize_allergen_info
    result.allergen_info = normalize_allergen_info(content.get("allergen_info"))
    return result


def existing_hints(db, recipe_id=None) -> tuple[List[str], List[str]]:
    try:
        owner = getattr(db, "import_owner", None)
        if recipe_id is not None:
            recipe = db.recipe_get(recipe_id)
            owner = recipe.get("owner_account_id") if recipe else None
        visible = "r.owner_account_id IS NULL"
        if owner is not None:
            visible = f"({visible} OR r.owner_account_id={int(owner)})"
        with db.conn() as conn:
            tags = [row[0] for row in conn.execute(
                "SELECT DISTINCT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id "
                f"JOIN recipes r ON r.id=rt.recipe_id WHERE r.deleted_at IS NULL AND ({visible})"
            ).fetchall()]
            canon = [row[0] for row in conn.execute(
                "SELECT MIN(TRIM(i.name)) AS display_name,COUNT(DISTINCT i.recipe_id) AS uses "
                "FROM recipe_ingredients i JOIN recipes r ON r.id=i.recipe_id "
                f"WHERE r.deleted_at IS NULL AND ({visible}) "
                "AND TRIM(COALESCE(i.name,''))<>'' AND COALESCE(r.ingredients_status,'')<>'variant_pending' "
                "GROUP BY LOWER(TRIM(i.name)) ORDER BY uses DESC,LENGTH(display_name),display_name COLLATE NOCASE LIMIT 500"
            ).fetchall()]
        return tags, canon
    except Exception as exc:
        logger.warning("Stammdaten-Hints konnten nicht geladen werden: %s", exc)
        return [], []


def apply_extracted_recipe_data(db, recipe_id: int, data: ExtractedRecipeData, *,
                                actor: str = "system", overwrite: bool = False,
                                create_version: bool = True,
                                update_description: bool = True) -> Dict[str, Any]:
    """Übernimmt erkannte Daten in ein Rezept und schützt bestehende Handarbeit.

    Standardmäßig werden nur fehlende Zutaten/Schritte/Portionen ergänzt. Mit
    ``overwrite=True`` wird vorher eine Rezeptversion erzeugt und der erkannte
    Stand ersetzt die vorhandenen strukturierten Daten.
    """
    recipe = db.recipe_get(recipe_id)
    if not recipe:
        return {"ok": False, "error": "Rezept nicht gefunden"}
    current_ingredients = db.recipe_ingredients_get(recipe_id)
    current_steps = db.recipe_steps_get(recipe_id)
    will_write_ingredients = bool(data.ingredients) and (overwrite or not current_ingredients)
    will_write_steps = bool(data.steps) and (overwrite or not current_steps)
    will_write_servings = data.servings is not None and (overwrite or not recipe.get("servings"))
    will_write_description = bool(data.text) and update_description and (overwrite or not (recipe.get("description") or "").strip())
    changed = will_write_ingredients or will_write_steps or will_write_servings or will_write_description

    if changed and create_version and (current_ingredients or current_steps or recipe.get("servings") or recipe.get("description")):
        version_id = db.recipe_version_create(
            recipe_id, created_by=actor, source="pdf",
            reason="Vor PDF-Rezeptdaten-Extraktion",
        )
        if version_id is None:
            return {"ok": False, "error": "Sicherung des bisherigen Rezeptstands fehlgeschlagen"}

    if will_write_description:
        with db.conn() as conn:
            conn.execute("UPDATE recipes SET description=? WHERE id=?", (data.text, recipe_id))
    if will_write_ingredients:
        auto_tags = sorted(set(data.tags) | set(compute_diet_tags(
            [
                item.get("canonical_name")
                for item in data.ingredients
                if item.get("canonical_name")
            ],
            allergen_info=data.allergen_info,
        )))
        db.recipe_apply_extraction_result(
            recipe_id,
            ingredients=data.ingredients,
            steps=data.steps if will_write_steps else current_steps,
            servings=data.servings if will_write_servings else recipe.get("servings"),
            auto_tags=auto_tags,
            status="error" if data.needs_review else "ok",
        )
    elif not current_ingredients and not data.ingredients:
        with db.conn() as conn:
            conn.execute("UPDATE recipes SET ingredients_status='error' WHERE id=?", (recipe_id,))
    if will_write_steps and not will_write_ingredients:
        db.recipe_steps_set(recipe_id, data.steps)
    if will_write_servings and not will_write_ingredients:
        db.recipe_set_servings(recipe_id, data.servings)

    return {
        "ok": True,
        "changed": changed,
        "ingredients_written": len(data.ingredients) if will_write_ingredients else 0,
        "steps_written": len(data.steps) if will_write_steps else 0,
        "servings_written": data.servings if will_write_servings else None,
        "description_written": will_write_description,
        "needs_review": data.needs_review,
        "warnings": data.warnings,
        "skipped_existing": {
            "ingredients": bool(current_ingredients and data.ingredients and not overwrite),
            "steps": bool(current_steps and data.steps and not overwrite),
            "servings": bool(recipe.get("servings") and data.servings is not None and not overwrite),
        },
    }
