"""Intelligente, lokal arbeitende Rezeptsuche.

Unterstützt:
- administrierbare Synonyme
- Ausschlüsse mit ``-zwiebel`` oder ``ohne zwiebel``
- Tippfehler-Vorschläge über lokalen Wortschatz
- nachvollziehbare Relevanzbewertung ohne externen Dienst
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)', re.UNICODE)


def fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold().strip()


def _clean_token(value: str) -> str:
    return re.sub(r"[^\w\-äöüÄÖÜß]+", "", value or "", flags=re.UNICODE).strip()


@dataclass
class SearchPlan:
    raw: str
    positive_groups: List[List[str]] = field(default_factory=list)
    negative_terms: List[str] = field(default_factory=list)
    corrected_query: Optional[str] = None
    corrections: Dict[str, str] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.positive_groups or self.negative_terms)


def parse_search_query(raw: str, synonym_map: Optional[Dict[str, List[str]]] = None) -> SearchPlan:
    synonym_map = synonym_map or {}
    plan = SearchPlan(raw=str(raw or "").strip())
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(plan.raw):
        token = (match.group(1) or match.group(2) or "").strip()
        if token:
            tokens.append(token)

    negative_next = False
    seen_positive = set()
    seen_negative = set()
    for token in tokens[:16]:
        folded = fold(token)
        if folded in {"ohne", "exclude", "ausser", "außer"}:
            negative_next = True
            continue
        is_negative = negative_next or token.startswith("-")
        negative_next = False
        cleaned = _clean_token(token[1:] if token.startswith("-") else token)
        if len(cleaned) < 2:
            continue
        key = fold(cleaned)
        if is_negative:
            if key not in seen_negative:
                plan.negative_terms.append(cleaned)
                seen_negative.add(key)
            continue
        group = synonym_map.get(key) or [cleaned]
        normalized_group = []
        for value in group:
            value = str(value or "").strip()
            if len(value) >= 2 and fold(value) not in {fold(x) for x in normalized_group}:
                normalized_group.append(value)
        marker = tuple(sorted(fold(x) for x in normalized_group))
        if marker and marker not in seen_positive:
            plan.positive_groups.append(normalized_group[:8])
            seen_positive.add(marker)
    return plan


def suggest_query(raw: str, vocabulary: Iterable[str], synonym_map: Optional[Dict[str, List[str]]] = None) -> Optional[SearchPlan]:
    synonym_map = synonym_map or {}
    base = parse_search_query(raw, synonym_map)
    vocab = {fold(v): str(v) for v in vocabulary if len(str(v).strip()) >= 3}
    vocab.update({fold(k): k for k in synonym_map})
    if not vocab:
        return None

    replacements: Dict[str, str] = {}
    for group in base.positive_groups:
        original = group[0]
        key = fold(original)
        if key in vocab or key in synonym_map:
            continue
        matches = difflib.get_close_matches(key, list(vocab), n=1, cutoff=0.78)
        if matches and matches[0] != key:
            replacements[original] = vocab[matches[0]]
    if not replacements:
        return None

    corrected_tokens = []
    for match in _TOKEN_RE.finditer(str(raw or "")):
        token = (match.group(1) or match.group(2) or "").strip()
        corrected_tokens.append(replacements.get(token, token))
    corrected = " ".join(corrected_tokens)
    result = parse_search_query(corrected, synonym_map)
    result.corrected_query = corrected
    result.corrections = replacements
    return result


def score_recipe(recipe: dict, ingredients: Iterable[dict], plan: SearchPlan) -> float:
    """Kleine, transparente Relevanzfunktion. Höher ist besser."""
    name = fold(recipe.get("name") or "")
    description = fold(recipe.get("description") or "")
    type_category = fold(f"{recipe.get('type') or ''} {recipe.get('category') or ''}")
    ing_text = " ".join(fold(i.get("canonical_name") or i.get("name") or "") for i in ingredients)
    score = 0.0
    for group in plan.positive_groups:
        values = [fold(v) for v in group]
        best = 0.0
        for term in values:
            if name == term:
                best = max(best, 100.0)
            elif name.startswith(term):
                best = max(best, 75.0)
            elif term in name:
                best = max(best, 55.0)
            if term in ing_text:
                best = max(best, 38.0)
            if term in type_category:
                best = max(best, 24.0)
            if term in description:
                best = max(best, 12.0)
        score += best
    # Leichte Aktualitätskomponente, ohne gute Titel-Matches zu überstimmen.
    score += min(2.0, float(recipe.get("source_added_at") or 0) / 10_000_000_000)
    return score
