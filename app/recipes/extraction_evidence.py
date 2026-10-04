"""Konservative lokale Belegprüfung für KI-Zutaten, ohne weitere API-Aufrufe."""
from __future__ import annotations

import math
import re
import unicodedata

from .canonical import canonical_name
from .quantities import parse_amount
from .search import matches_excluded_term
from .units import normalize_unit, to_base


def _text(value) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


def review_reasons(content: dict, source: str, *, threshold: float = .75) -> list[str]:
    """Fehlende/abweichende Belege bleiben prüfbedürftig; raw ist kein Beweis,
    solange der Ausschnitt nicht tatsächlich in der Quelle vorkommt.

    Diese Prüfung beweist keine vollständige Rezept- oder Allergensicherheit.
    Sie verhindert erfundene Zutaten/Mengen und vertraut keiner KI-Confidence
    als Ersatz für den Quelltext.
    """
    from .pdf_recipe_extract import parse_ingredient_lines
    reasons = list(content.get("review_reasons") or [])
    confidence = content.get("confidence")
    if confidence is not None:
        try:
            value = float(confidence)
            if not math.isfinite(value) or not threshold <= value <= 1:
                reasons.append("KI meldet eine unsichere Auswertung")
        except (TypeError, ValueError):
            reasons.append("KI-Confidence ist ungültig")
    source_text = _text(source)
    source_items = parse_ingredient_lines(source)
    ai_names = [str(item.get("name") or "") for item in (content.get("ingredients") or []) if isinstance(item, dict)]
    if ai_names:
        for source_item in source_items:
            if not any(canonical_name(name) == source_item.get("canonical_name")
                       or matches_excluded_term(name, source_item.get("name")) for name in ai_names):
                reasons.append(f"Quellenzutat fehlt in der KI-Liste: {source_item.get('name')}")
    for item in (content.get("ingredients") or [])[:120]:
        if not isinstance(item, dict):
            reasons.append("Zutatenformat ist ungültig")
            continue
        name = str(item.get("name") or "").strip()
        target = canonical_name(name)
        raw = str(item.get("raw") or "").strip()
        if not target:
            reasons.append("Zutat ohne Namen")
            continue
        if raw and _text(raw) not in source_text:
            reasons.append(f"Quellbeleg fehlt: {name}")
            continue
        if source_items and not any(
            row.get("canonical_name") == target or matches_excluded_term(name, row.get("name"))
            for row in source_items
        ):
            reasons.append(f"Zutat fehlt in der erkannten Quellenliste: {name}")
            continue
        if raw and re.match(r"\s*(?:ignore|ignoriere|anweisung|system|assistant)\b", raw, re.I):
            reasons.append(f"Anweisung ist kein Zutatenbeleg: {name}")
            continue
        candidates = parse_ingredient_lines("Zutaten:\n" + raw) if raw else source_items
        matching = [row for row in candidates if row.get("canonical_name") == target]
        evidence = raw or source
        if not matching and not matches_excluded_term(name, evidence):
            reasons.append(f"Zutat ist nicht belegt: {name}")
            continue
        amount = item.get("amount")
        if amount is None:
            continue
        try:
            amount = float(amount)
            if not math.isfinite(amount) or amount < 0 or amount > 1_000_000:
                raise ValueError("invalid amount")
        except (TypeError, ValueError, OverflowError):
            reasons.append(f"Menge ist ungültig: {name}")
            continue
        verified = False
        for row in matching:
            if row.get("amount") is None:
                continue
            source_unit = normalize_unit(row.get("unit"))
            ai_unit = normalize_unit(item.get("unit"))
            # Eine unbenannte Stückzahl ("2 Eier") darf als Stück geführt werden.
            source_unit = source_unit or ("Stück" if ai_unit == "Stück" else None)
            base_unit, lower = to_base(source_unit, row["amount"])
            ai_base, value = to_base(ai_unit, amount)
            upper = lower
            range_match = re.match(r"\s*(.+?)\s*[-–]\s*(\S+)", row.get("raw") or "")
            if range_match:
                upper_amount = parse_amount(range_match.group(2))
                if upper_amount is not None:
                    _, upper = to_base(source_unit, upper_amount)
            if base_unit == ai_base and lower - 1e-6 <= value <= upper + 1e-6:
                verified = True
                break
        if not verified:
            reasons.append(f"Menge ist nicht eindeutig belegt: {name}")
    return list(dict.fromkeys(str(reason) for reason in reasons))[:120]
