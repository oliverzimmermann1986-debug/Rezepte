"""Gemeinsame Regeln fuer sichtbare Rezeptnamen."""

from __future__ import annotations

import re
import unicodedata


def normalize_recipe_name(value: str | None) -> str:
    """Ersetzt Unterstriche durch Leerzeichen und glättet den Anzeigenamen.

    Technische Datei- und Ordnernamen werden absichtlich nicht verändert.
    Durch NFKC werden auch kompatible Unicode-Unterstriche vor der Ersetzung
    vereinheitlicht.
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"_+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()
