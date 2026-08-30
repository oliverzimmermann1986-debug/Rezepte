"""Quellennachweis und deterministischer Rezept-TÜV.

Der Quellenwächter vergleicht normalisierte Text-Snapshots. Er schreibt niemals
Inhalte in ein Rezept zurück: Eine erkannte Änderung bleibt ein Review-Hinweis,
bis ein Administrator den beobachteten Stand ausdrücklich als neue Baseline
bestätigt.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from .auto_tags import ALLERGEN_FREE_TAGS
from .canonical import canonical_name


_MISSING_AMOUNT_EXCEPTIONS = (
    "nach geschmack",
    "nach bedarf",
    "optional",
    "prise",
    "etwas",
    "zum garnieren",
)


def normalize_source_text(value: Optional[str]) -> str:
    """Stabilisiert Quelltext für reproduzierbare Fingerprints und Diffs."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    lines: List[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def source_fingerprint(value: Optional[str]) -> Optional[str]:
    normalized = normalize_source_text(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def source_diff(
    baseline_text: Optional[str],
    current_text: Optional[str],
    *,
    max_lines: int = 80,
) -> Dict[str, Any]:
    """Erzeugt eine kompakte, UI-freundliche Änderungsvorschau."""
    baseline = normalize_source_text(baseline_text).splitlines()
    current = normalize_source_text(current_text).splitlines()
    changed = baseline != current
    diff_lines = list(
        difflib.unified_diff(
            baseline,
            current,
            fromfile="Gespeicherte Quelle",
            tofile="Aktuelle Quelle",
            lineterm="",
            n=2,
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    truncated = len(diff_lines) > max_lines
    return {
        "changed": changed,
        "added_lines": added,
        "removed_lines": removed,
        "baseline_lines": len(baseline),
        "current_lines": len(current),
        "similarity": round(difflib.SequenceMatcher(None, baseline, current).ratio(), 3),
        "lines": diff_lines[:max_lines],
        "truncated": truncated,
    }


_MEASURE_RE = re.compile(
    r"(?:^|\s)(?:\d+(?:[.,]\d+)?\s*)?"
    r"(?:mg|g|kg|ml|cl|dl|l|tl|el|stueck|stück|prise|tasse|dose|bund)\b",
    flags=re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:\s|$)")
_STEP_MARKERS = (
    "backen", "kochen", "köcheln", "koecheln", "braten", "schneiden",
    "rühren", "ruehren", "mischen", "erhitzen", "servieren", "ziehen lassen",
    "vorheizen", "abschmecken", "garen",
)
_ALLERGEN_LABELS = {
    "gluten": "Gluten",
    "lactose": "Milch/Laktose",
    "egg": "Ei",
    "nuts": "Nüsse",
}


def _changed_content_lines(comparison: Dict[str, Any], prefix: str) -> List[str]:
    return [
        re.sub(r"^[*+-]\s+", "", line[1:].strip())
        for line in comparison.get("lines", [])
        if line.startswith(prefix)
        and not line.startswith(prefix * 3)
        and line[1:].strip()
    ]


def _term_in_text(term: str, text: str) -> bool:
    normalized_term = term.casefold().strip()
    normalized_text = text.casefold()
    if not normalized_term:
        return False
    canonical_tokens = {
        str(canonical_name(token) or "").casefold()
        for token in re.findall(r"[\wäöüÄÖÜß-]+", normalized_text)
    }
    if normalized_term in canonical_tokens:
        return True
    if len(normalized_term) < 4:
        return bool(re.search(rf"\b{re.escape(normalized_term)}\b", normalized_text))
    return normalized_term in normalized_text


def source_change_impact(
    comparison: Optional[Dict[str, Any]],
    ingredients: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Ordnet Quell-Diffs vorsichtig Zutaten, Schritten und Allergenen zu.

    Das Ergebnis ist immer ein Review-Hinweis. Es behauptet weder, dass eine
    Zeile sicher eine Zutat ist, noch dass ein Rezept allergenfrei oder
    medizinisch sicher sei.
    """
    if not comparison or not comparison.get("changed"):
        return None
    added = _changed_content_lines(comparison, "+")
    removed = _changed_content_lines(comparison, "-")
    known_ingredients = {
        str(item.get(key) or "").casefold().strip()
        for item in ingredients
        for key in ("canonical_name", "name")
        if str(item.get(key) or "").strip()
    }

    def classify(lines: List[str], direction: str) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        ingredient_changes: List[Dict[str, str]] = []
        instruction_changes: List[Dict[str, str]] = []
        for line in lines:
            lower = line.casefold()
            looks_like_ingredient = (
                bool(_MEASURE_RE.search(line))
                or bool(_AMOUNT_RE.search(line))
                or any(_term_in_text(term, lower) for term in known_ingredients)
            )
            looks_like_step = any(marker in lower for marker in _STEP_MARKERS)
            if looks_like_ingredient:
                ingredient_changes.append({"direction": direction, "text": line})
            if looks_like_step:
                instruction_changes.append({"direction": direction, "text": line})
        return ingredient_changes, instruction_changes

    added_ingredients, added_steps = classify(added, "added")
    removed_ingredients, removed_steps = classify(removed, "removed")
    allergen_changes: List[Dict[str, Any]] = []
    for direction, lines in (("added", added), ("removed", removed)):
        for _tag_name, (allergen_key, sources) in ALLERGEN_FREE_TAGS.items():
            evidence: List[str] = []
            matched_terms: set[str] = set()
            for line in lines:
                matches = sorted(term for term in sources if _term_in_text(term, line))
                if matches:
                    evidence.append(line)
                    matched_terms.update(matches)
            if evidence:
                allergen_changes.append({
                    "allergen": allergen_key,
                    "label": _ALLERGEN_LABELS.get(allergen_key, allergen_key),
                    "direction": direction,
                    "matched_terms": sorted(matched_terms),
                    "evidence": evidence[:5],
                })

    return {
        "ingredient_changes": (added_ingredients + removed_ingredients)[:20],
        "instruction_changes": (added_steps + removed_steps)[:20],
        "possible_allergen_changes": allergen_changes,
        "review_required": any(
            item["direction"] == "added" for item in allergen_changes
        ),
        "automatic_safety_claim": False,
    }


def _issue(
    issue_id: str,
    title: str,
    detail: str,
    severity: str,
    section: str,
) -> Dict[str, str]:
    return {
        "id": issue_id,
        "title": title,
        "detail": detail,
        "severity": severity,
        "section": section,
    }


def recipe_quality_report(
    recipe: Dict[str, Any],
    ingredients: Iterable[Dict[str, Any]],
    steps: Iterable[Dict[str, Any]],
    *,
    source_status: str,
) -> Dict[str, Any]:
    """Bewertet nur belastbare, lokal prüfbare Qualitätsmerkmale.

    Es werden bewusst keine semantischen Behauptungen wie "Zutat fehlt im
    Arbeitsschritt" erfunden. Solche Aussagen brauchen sprachliche Auswertung
    und wären bei Gewürzen oder zusammengesetzten Zutaten schnell falsch.
    """
    ingredient_list = list(ingredients)
    step_list = list(steps)
    issues: List[Dict[str, str]] = []

    if not recipe.get("url"):
        issues.append(_issue(
            "source-missing", "Originalquelle fehlt",
            "Link, PDF oder Fotoquelle ergänzen, damit die Herkunft nachvollziehbar bleibt.",
            "warning", "source",
        ))
    elif source_status == "unchecked":
        issues.append(_issue(
            "source-unchecked", "Quelle noch nicht geprüft",
            "Der Quellenwächter hat für diese Adresse noch keinen Vergleich durchgeführt.",
            "info", "source",
        ))
    elif source_status == "changed":
        issues.append(_issue(
            "source-changed", "Originalquelle wurde verändert",
            "Die gespeicherte Fassung bleibt bestehen. Bitte den Unterschied manuell prüfen.",
            "warning", "source",
        ))
    elif source_status == "unavailable":
        issues.append(_issue(
            "source-unavailable", "Originalquelle nicht erreichbar",
            "Der letzte Abruf ist fehlgeschlagen; das gespeicherte Rezept bleibt verfügbar.",
            "warning", "source",
        ))

    if not ingredient_list:
        issues.append(_issue(
            "ingredients-missing", "Zutaten fehlen",
            "Ohne Zutaten sind Skalierung und Einkaufsliste nicht vollständig nutzbar.",
            "critical", "ingredients",
        ))
    if not step_list:
        issues.append(_issue(
            "steps-missing", "Zubereitung fehlt",
            "Für den Kochmodus werden mindestens ein Arbeitsschritt benötigt.",
            "critical", "steps",
        ))
    if recipe.get("servings") is None:
        issues.append(_issue(
            "servings-missing", "Portionszahl fehlt",
            "Mengen können erst mit einer Ausgangs-Portionszahl zuverlässig skaliert werden.",
            "warning", "servings",
        ))

    canonical_counts: Dict[str, int] = {}
    incomplete_amounts = 0
    for ingredient in ingredient_list:
        canonical = str(ingredient.get("canonical_name") or ingredient.get("name") or "").strip().casefold()
        if canonical:
            canonical_counts[canonical] = canonical_counts.get(canonical, 0) + 1
        searchable = " ".join(str(ingredient.get(key) or "") for key in ("raw", "name")).casefold()
        has_exception = any(marker in searchable for marker in _MISSING_AMOUNT_EXCEPTIONS)
        if ingredient.get("amount") is None and not has_exception:
            incomplete_amounts += 1
    duplicates = sorted(name for name, count in canonical_counts.items() if count > 1)
    if duplicates:
        preview = ", ".join(duplicates[:4])
        issues.append(_issue(
            "ingredients-duplicate", "Doppelte Zutaten prüfen",
            f"Mehrfach erkannt: {preview}.", "warning", "ingredients",
        ))
    if incomplete_amounts:
        issues.append(_issue(
            "amounts-missing", "Mengenangaben prüfen",
            f"{incomplete_amounts} Zutat(en) haben keine erkennbare Menge.",
            "info", "ingredients",
        ))

    short_steps = sum(
        1 for step in step_list
        if len(str(step.get("instruction") or "").strip()) < 12
    )
    if short_steps:
        issues.append(_issue(
            "steps-short", "Sehr kurze Schritte prüfen",
            f"{short_steps} Schritt(e) könnten unvollständig sein.",
            "info", "steps",
        ))

    if recipe.get("user_verified") and not any(
        issue["severity"] in {"critical", "warning"} for issue in issues
    ):
        status = "verified"
    elif any(issue["severity"] == "critical" for issue in issues):
        status = "incomplete"
    else:
        status = "review"

    penalty = {"critical": 30, "warning": 12, "info": 4}
    score = max(0, 100 - sum(penalty[issue["severity"]] for issue in issues))
    return {
        "status": status,
        "score": score,
        "issues": issues,
        "checked_rules": 8,
    }
