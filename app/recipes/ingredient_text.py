"""Discard source metadata without interpreting hashtags as food names."""
from __future__ import annotations

import re

_HASHTAG = re.compile(r"(?<!\w)[#＃][^\W\d_][\w-]*", re.UNICODE)
_HEADING = re.compile(r"^#{1,6}\s+")
_LABEL = re.compile(
    r"^(?:zutaten(?:liste)?|ingredients?|einkaufsliste|du\s+brauchst|"
    r"zubereitung|anleitung|instructions?|preparation|nährwerte|naehrwerte|"
    r"nutrition|tipps?|hinweise?|notes?|lead|außerdem|ausserdem|"
    r"beschreibung|description|rezepttitel|title)\s*(?:\([^)]*\))?\s*:?$",
    re.IGNORECASE,
)
_COMPONENT = re.compile(
    r"^(?:(?:so(?:ß|ss)e|sauce|dip)\s+\d+\s*(?:\([^)]*\))?|"
    r"für\s+(?:den|die|das)\s+.+)\s*:?$", re.IGNORECASE,
)
_RECIPE_TITLE = re.compile(r"\brezept\s*(?:[–—-]|für\b|:|$)", re.IGNORECASE)


def clean_ingredient_text(value: str) -> str:
    """Keep a real ingredient preceding trailing social hashtags."""
    text = _HEADING.sub('', str(value or '').strip())
    return ' '.join(_HASHTAG.sub('', text).split()).strip()


def is_non_ingredient(name: str, raw: str | None = None) -> bool:
    text = clean_ingredient_text(name).strip(' :')
    if not text or _LABEL.fullmatch(text) or _COMPONENT.fullmatch(text) or _RECIPE_TITLE.search(text):
        return True
    # A translated food name backed only by a hashtag is still metadata.
    if raw and _HASHTAG.search(str(raw)) and not clean_ingredient_text(raw).strip(' -–—•*'):
        return True
    return False


def filter_imported_ingredients(items: list[dict]) -> list[dict]:
    from .canonical import canonical_name

    result = []
    for item in items:
        if is_non_ingredient(item.get('name'), item.get('raw')):
            continue
        name = clean_ingredient_text(item.get('name'))
        if name != item.get('name'):
            item = {**item, 'name': name, 'canonical_name': canonical_name(name)}
        result.append(item)
    return result
