"""Einheiten-Normalisierung + Konvertierung für den Einkaufskorb-Merge.

Drei Klassen von Einheiten:

  * MASS    — gemessen in Gramm. 1 kg = 1000 g, 1 mg = 0.001 g.
  * VOLUME  — gemessen in Milliliter. 1 l = 1000 ml, 1 cl = 10 ml, 1 dl = 100 ml.
  * COUNT   — diskrete Stückzahl. Ein Token pro Variante: "Stück", "Zehe",
              "Bund", "Scheibe", "Blatt", "Prise". Kein Cross-Konvertieren
              (3 Zehen + 2 Bund Petersilie ergibt KEINE sinnvolle Summe).

Spoon-Einheiten (TL ≈ 5 ml, EL ≈ 15 ml) werden ABSICHTLICH NICHT in VOLUME
konvertiert, weil ein User „2 EL Olivenöl" im Einkaufskorb anders sehen
will als „30 ml Olivenöl" (man kauft eine Flasche, nicht 30 ml). Spoon-
Einheiten bleiben als eigene COUNT-Tokens stehen.

API:
  normalize_unit("KG")        -> "kg"
  unit_class("kg")            -> "mass"
  to_base("kg", 1.5)          -> ("g", 1500.0)
  from_base("mass", 1500)     -> ("kg", 1.5)
  can_merge("g", "kg")        -> True   (beide mass)
  can_merge("Stück", "g")     -> False
  can_merge("Stück", "Stück") -> True
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# ── Alias-Map: alles auf einen kanonischen Token mappen ───────────────────
# Lowercase-key → kanonischer Token. Mehrere Schreibweisen pro Einheit.
_ALIASES: Dict[str, str] = {
    # Masse
    "g": "g", "gramm": "g", "gr": "g",
    "kg": "kg", "kilogramm": "kg", "kilo": "kg",
    "mg": "mg", "milligramm": "mg",
    # Volumen
    "ml": "ml", "milliliter": "ml",
    "l": "l", "liter": "l", "ltr": "l",
    "cl": "cl", "centiliter": "cl", "zentiliter": "cl",
    "dl": "dl", "deziliter": "dl",
    # Spoon (bleiben als eigene Count-Tokens — siehe Modul-Doc)
    "tl": "TL", "teelöffel": "TL", "tsp": "TL",
    "el": "EL", "esslöffel": "EL", "tbsp": "EL",
    # Count
    "stück": "Stück", "stueck": "Stück", "stk": "Stück", "stk.": "Stück",
    "x": "Stück",
    "zehe": "Zehe", "zehen": "Zehe",
    "bund": "Bund",
    "scheibe": "Scheibe", "scheiben": "Scheibe",
    "blatt": "Blatt", "blätter": "Blatt",
    "prise": "Prise", "prisen": "Prise",
    "tasse": "Tasse", "tassen": "Tasse",
    "dose": "Dose", "dosen": "Dose",
    "pck": "Pck", "pck.": "Pck", "packung": "Pck", "packungen": "Pck",
    "päckchen": "Pck", "paeckchen": "Pck",
    "flasche": "Flasche", "flaschen": "Flasche",
    "tüte": "Tüte", "tueten": "Tüte", "tüten": "Tüte",
    "gläser": "Glas", "glas": "Glas",
    "becher": "Becher",
    "handvoll": "Handvoll",
    "stiel": "Stiel", "stiele": "Stiel",
    "stange": "Stange", "stangen": "Stange",
    "kopf": "Kopf", "köpfe": "Kopf", "koepfe": "Kopf",
    "schale": "Schale", "schalen": "Schale",
}

# ── Klassen + Konvertierungs-Faktoren ─────────────────────────────────────
# Pro Einheit der Basis-Wert (z.B. 1 kg = 1000 g, Basis ist g).
_MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0}
_VOLUME = {"ml": 1.0, "cl": 10.0, "dl": 100.0, "l": 1000.0}
_BASE = {"mass": "g", "volume": "ml"}

# Welche Display-Einheit für welchen Mengen-Bereich (in Basis):
_MASS_DISPLAY = [(1000.0, "kg"), (1.0, "g"), (0.0, "mg")]
_VOLUME_DISPLAY = [(1000.0, "l"), (10.0, "cl"), (1.0, "ml")]


def normalize_unit(unit: Optional[str]) -> Optional[str]:
    """Mappt freie Eingaben auf einen kanonischen Token.
    Unbekannte Einheiten werden zurückgegeben wie sie sind (capitalize),
    damit der User-Eintrag erhalten bleibt — Merge findet dann eben nicht
    statt, was korrekt ist."""
    if unit is None:
        return None
    raw = str(unit).strip()
    if not raw:
        return None
    return _ALIASES.get(raw.lower(), raw)


def unit_class(unit: Optional[str]) -> Optional[str]:
    """Klassifiziert eine Einheit: 'mass' / 'volume' / 'count' / None."""
    if unit is None:
        return None
    u = normalize_unit(unit)
    if u is None or u == "":
        return None
    if u in _MASS:
        return "mass"
    if u in _VOLUME:
        return "volume"
    # Alles andere (Stück, EL, Zehe, …) ist Count
    return "count"


def to_base(unit: Optional[str], amount: Optional[float]) -> Tuple[Optional[str], Optional[float]]:
    """Konvertiert Menge in Basis-Einheit der Klasse.
       (kg, 1.5) → (g, 1500.0). Count-Einheiten + unknown bleiben unverändert."""
    if unit is None or amount is None:
        return unit, amount
    u = normalize_unit(unit) or unit
    if u in _MASS:
        return _BASE["mass"], float(amount) * _MASS[u]
    if u in _VOLUME:
        return _BASE["volume"], float(amount) * _VOLUME[u]
    return u, float(amount)  # Count: 1:1


def from_base_display(class_or_unit: str, base_amount: float) -> Tuple[str, float]:
    """Wählt die passende Display-Einheit basierend auf der Menge.
       ('mass', 1500) → ('kg', 1.5); ('mass', 250) → ('g', 250).
       Akzeptiert auch direkt eine Einheit ('g', 1500) und ermittelt die
       Klasse intern."""
    cls = class_or_unit if class_or_unit in ("mass", "volume") else unit_class(class_or_unit)
    table = _MASS_DISPLAY if cls == "mass" else _VOLUME_DISPLAY if cls == "volume" else None
    if not table:
        return class_or_unit, base_amount
    for threshold, display_unit in table:
        if abs(base_amount) >= threshold:
            factor = _MASS[display_unit] if cls == "mass" else _VOLUME[display_unit]
            return display_unit, base_amount / factor
    # fallback auf kleinste
    last_unit = table[-1][1]
    factor = _MASS[last_unit] if cls == "mass" else _VOLUME[last_unit]
    return last_unit, base_amount / factor


def can_merge(unit_a: Optional[str], unit_b: Optional[str]) -> bool:
    """Sind die zwei Einheiten so kompatibel, dass man ihre Mengen summieren darf?
       Regel:
         - Beide None: ja (z.B. "Salz" + "Salz" → noch immer "Salz" ohne Menge).
         - Beide gleiche Klasse mass/volume: ja, mit Umrechnung.
         - Beide gleiche Count-Token ("Stück" == "Stück"): ja.
         - Sonst: nein (Stück + g, Stück + Zehe, EL + ml: separate Zeilen)."""
    if unit_a is None and unit_b is None:
        return True
    if unit_a is None or unit_b is None:
        return False
    na = normalize_unit(unit_a)
    nb = normalize_unit(unit_b)
    ca, cb = unit_class(na), unit_class(nb)
    if ca != cb:
        return False
    if ca in ("mass", "volume"):
        return True
    # Count: nur wenn exakt selber Token
    return na == nb
