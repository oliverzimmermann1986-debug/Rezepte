"""Audit-Funktionen: Dubletten + schlechte Namen + KI-Vorschläge.

Library-Modul, genutzt von zwei Aufrufer-Schichten:
  - tools/recipe_audit.py   → CLI (SSH/PowerShell), rendert Markdown
  - app/routes/api_audit.py → Web-UI, liefert JSON

Beide nutzen die gleichen Such-Funktionen. So bleibt das Audit-Verhalten
konsistent (was im CLI als Dublette zählt, zählt auch im Web als Dublette).

Drei Bausteine:
  1. load_recipes(db_or_path)        → Liste aller Rezepte
  2. find_*(recipes)                 → 5 Findings-Typen
  3. ai_suggest_batch(bad, openai_cfg) → optionale KI-Namensvorschläge

Bewusst KEINE I/O / Side-Effects außer dem read-only-DB-Read. Keine
write-Operationen, keine FS-Touches.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

import requests

from ..core.webhook import server_configured_request

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..db import Database

# Schwelle für Ähnlichkeits-Cluster. 0.85 = "Bolognese"↔"Bolognase" matched,
# "Pasta"↔"Pizza" matched NICHT. Tuned auf False-Positives-min.
SIMILARITY_THRESHOLD = 0.85

# Schlechte-Namen-Patterns
BAD_NAME_PATTERNS = [
    (re.compile(r"^(unbekannt|unknown|test|tmp|temp|untitled)$", re.I), "Generischer Platzhalter"),
    (re.compile(r"https?://|\.com|\.org|\.net|@\w+", re.I), "URL-/Mention-Rest im Namen"),
    (re.compile(r"^[\d\s\-_./:]+$"), "Nur Zahlen/Datum/Sonderzeichen"),
    (re.compile(r"^video[_\-\s]?\d*$", re.I), "Generisches video_xxx"),
    (re.compile(r"\bunbekannt\b", re.I), "'Unbekannt' im Namen"),
    (re.compile(r"^\(.*\)$"), "Komplett in Klammern"),
]

SHORT_NAME_MIN_CHARS = 4
SHORT_NAME_SUSPICIOUS_WORDS = 1

# Skipping-Threshold für O(n²) similar-name-search
SIMILAR_SEARCH_MAX_N = 2000


# ════════════════════════════════════════════════════════════════════════
# DB-Load (read-only)
# ════════════════════════════════════════════════════════════════════════

def load_recipes(db_or_path: Union["Database", Path, str]) -> List[Dict[str, Any]]:
    """Lädt alle Rezepte für die Audit-Analyse.

    Zwei Aufruf-Modi:
      - Database-Instanz (aus app.db.get_db()) → nutzt deren Connection
      - Path/str          → öffnet read-only-Connection direkt

    Der CLI-Pfad nutzt die zweite Variante (kein app.db-Import nötig),
    die Web-Route nutzt die erste (teilt die schon existierende Connection)."""
    # Duck-typing: hat ein conn()-Context-Manager → es ist die Database
    if hasattr(db_or_path, "conn"):
        with db_or_path.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipes WHERE deleted_at IS NULL ORDER BY id"
            ).fetchall()
            return [_normalize(dict(r)) for r in rows]
    # Sonst: Path/str → read-only sqlite-Direktzugriff
    db_path = Path(db_or_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB nicht gefunden: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes'"
        ).fetchone()
        if not tbl:
            raise RuntimeError(
                "'recipes'-Tabelle fehlt. Erst im Web-UI den Tab 'Rezepte' öffnen, "
                "damit der lazy FS-Sync läuft."
            )
        rows = conn.execute(
            "SELECT * FROM recipes WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        return [_normalize(dict(r)) for r in rows]
    finally:
        conn.close()


def _normalize(r: Dict[str, Any]) -> Dict[str, Any]:
    """description NULL → "" damit Audit-Logik nicht None-checken muss."""
    r["description"] = r.get("description") or ""
    return r


# ════════════════════════════════════════════════════════════════════════
# Dubletten + Cluster
# ════════════════════════════════════════════════════════════════════════

def find_exact_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """Gleiche `name`-Werte (case-insensitive). Indikator für Re-Scrapes."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        key = (r.get("name") or "").strip().lower()
        if not key:
            continue
        buckets.setdefault(key, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_url_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """Gleiche URL mehrfach indiziert (sollte über UNIQUE-Constraint nicht
    passieren, aber kann durch manuelles DB-Editieren entstehen)."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        u = (r.get("url") or "").strip()
        if not u:
            continue
        buckets.setdefault(u, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_folder_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """folder_path-Duplikate (DB hat UNIQUE, aber Check ist billig)."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        f = (r.get("folder_path") or "").strip()
        if not f:
            continue
        buckets.setdefault(f, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_similar_names(recipes: List[Dict], threshold: float = SIMILARITY_THRESHOLD,
                       timeout_seconds: float = 2.0,
                      ) -> List[List[Dict]]:
    """Findet Cluster mit ähnlichen Namen (difflib + Union-Find).

    Returns: Liste von Clustern (Liste von Rezepten). Falls Bestand zu groß
    (>SIMILAR_SEARCH_MAX_N) wird ein Warn-Cluster mit ``_warning``-Key
    zurückgegeben statt zu hängen."""
    n = len(recipes)
    if n == 0:
        return []
    if n > SIMILAR_SEARCH_MAX_N:
        return [[{"_warning": f"Skipped: {n} Rezepte > {SIMILAR_SEARCH_MAX_N} "
                              f"(O(n²) zu teuer). Nur exakte Dubletten gecheckt."}]]

    # Filter: nur "echte" Namen vergleichen
    candidates = []
    for r in recipes:
        name = (r.get("name") or "").strip()
        if len(name) < 4 or name.lower() == "unbekannt":
            continue
        candidates.append((r, name.lower()))

    parent = list(range(len(candidates)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    timed_out = False
    compared = 0
    for i in range(len(candidates)):
        name_i = candidates[i][1]
        for j in range(i + 1, len(candidates)):
            if compared % 500 == 0 and time.monotonic() >= deadline:
                timed_out = True
                break
            compared += 1
            name_j = candidates[j][1]
            if abs(len(name_i) - len(name_j)) > max(3, len(name_i) // 3):
                continue
            if name_i == name_j:
                continue
            ratio = SequenceMatcher(None, name_i, name_j).ratio()
            if ratio >= threshold:
                union(i, j)
        if timed_out:
            break

    clusters: Dict[int, List[Dict]] = {}
    for i, (r, _) in enumerate(candidates):
        root = find(i)
        clusters.setdefault(root, []).append(r)
    result = [c for c in clusters.values() if len(c) >= 2]
    if timed_out:
        result.append([{
            "_warning": (
                f"Ähnlichkeitssuche nach {timeout_seconds:g}s beendet; "
                f"{compared} Kandidatenpaare geprüft. Exakte Dubletten sind vollständig."
            )
        }])
    return result


# ════════════════════════════════════════════════════════════════════════
# Schlechte Namen
# ════════════════════════════════════════════════════════════════════════

def find_bad_names(recipes: List[Dict]) -> List[Tuple[Dict, str]]:
    """Liefert (recipe, grund) für jedes Rezept mit fragwürdigem Namen."""
    out = []
    for r in recipes:
        name = (r.get("name") or "").strip()
        if not name:
            out.append((r, "Name leer"))
            continue
        matched = False
        for pat, reason in BAD_NAME_PATTERNS:
            if pat.search(name):
                out.append((r, reason))
                matched = True
                break
        if matched:
            continue
        if len(name) < SHORT_NAME_MIN_CHARS:
            out.append((r, f"Sehr kurz ({len(name)} Zeichen)"))
            continue
        words = name.split()
        if len(words) <= SHORT_NAME_SUSPICIOUS_WORDS and len(name) <= 5:
            out.append((r, "Nur 1 kurzes Wort"))
    return out


# ════════════════════════════════════════════════════════════════════════
# OpenAI-Vorschläge (single batch call, alle bad-names auf einmal)
# ════════════════════════════════════════════════════════════════════════

def ai_suggest_batch(
    bad_items: List[Tuple[Dict, str]],
    openai_cfg: Dict[str, Any],
) -> Dict[int, str]:
    """Ein einzelner OpenAI-Call mit ALLEN bad-name-Rezepten. Returnt
    {recipe_id: suggested_name}. Bei Fehler: leeres Dict + Warning im Log."""
    candidates = []
    for r, _reason in bad_items:
        desc = (r.get("description") or "").strip()
        if len(desc) >= 30:
            candidates.append({
                "id": r["id"],
                "current_name": r.get("name") or "",
                "type": r.get("type") or "",
                "category": r.get("category") or "",
                "description_excerpt": desc[:600],
            })
    if not candidates:
        return {}

    base_url = (openai_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key = openai_cfg["api_key"]
    model = openai_cfg.get("model") or "gpt-4o-mini"
    timeout = int(openai_cfg.get("timeout") or 30)

    system = (
        "Du bekommst eine Liste von Rezept-Einträgen mit unklaren Namen "
        "(z.B. 'Unbekannt', 'video_123', URL-Reste). Für jeden Eintrag "
        "schlage einen knappen, sprechenden deutschen Rezeptnamen vor "
        "(max 5 Wörter, ohne Kategorie-Suffix). Basis ist die Beschreibung. "
        "Antworte AUSSCHLIESSLICH mit gültigem JSON:\n"
        '{"suggestions":[{"id":15,"name":"Tomaten-Basilikum-Pasta"},...]}\n'
        "Wenn die Beschreibung wirklich keinen Hinweis gibt, lass den "
        "Eintrag aus der suggestions-Liste raus (kein null/leerer Name)."
    )
    user = "Einträge:\n" + json.dumps(candidates, ensure_ascii=False, indent=2)

    try:
        r = server_configured_request(
            "POST",
            f"{base_url}/chat/completions",
            trusted_private_bases=(base_url,),
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        data = json.loads(content)
        out = {}
        for s in (data.get("suggestions") or []):
            try:
                rid = int(s["id"])
                name = (s.get("name") or "").strip()
                if name:
                    out[rid] = name
            except (KeyError, ValueError, TypeError):
                continue
        return out
    except requests.exceptions.HTTPError as e:
        logger.warning(
            "Audit OpenAI HTTP %s",
            e.response.status_code if e.response else "?",
        )
        return {}
    except Exception as e:
        logger.warning("Audit OpenAI call failed: %s", type(e).__name__)
        return {}


# ════════════════════════════════════════════════════════════════════════
# All-in-one: gesammelter Audit-Run
# ════════════════════════════════════════════════════════════════════════

def run_audit(
    db_or_path,
    *,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    similarity_timeout_seconds: float = 2.0,
    openai_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Führt alle Audit-Suchen aus und liefert ein strukturiertes Dict.

    Optional mit openai_cfg → KI-Vorschläge für schlechte Namen werden
    ergänzt (in `ai_suggestions: {recipe_id: name}`).

    Rückgabe-Schema (für Frontend-JSON-Konsum entworfen):
      {
        "total_recipes": int,
        "exact_duplicates": [{name, items: [recipe, ...]}, ...],
        "url_duplicates":   [{url, items: [recipe, ...]}, ...],
        "folder_duplicates":[{folder, items: [recipe, ...]}, ...],
        "similar_clusters": [{names: [...], items: [recipe, ...], warning?}, ...],
        "bad_names":        [{recipe, reason}, ...],
        "ai_suggestions":   {recipe_id: suggested_name} | {},
        "ai_available":     bool,  # openai_cfg war vorhanden + brauchbar
      }
    """
    recipes = load_recipes(db_or_path)

    exact = find_exact_duplicates(recipes)
    url_dups = find_url_duplicates(recipes)
    folder_dups = find_folder_duplicates(recipes)
    similar = find_similar_names(
        recipes,
        threshold=similarity_threshold,
        timeout_seconds=similarity_timeout_seconds,
    )
    bad = find_bad_names(recipes)

    ai_suggestions: Dict[int, str] = {}
    ai_available = bool(openai_cfg and openai_cfg.get("api_key"))
    if ai_available and bad:
        ai_suggestions = ai_suggest_batch(bad, openai_cfg)

    return {
        "total_recipes": len(recipes),
        "exact_duplicates": [
            {"name": k, "items": items}
            for k, items in sorted(exact.items(), key=lambda kv: -len(kv[1]))
        ],
        "url_duplicates": [
            {"url": k, "items": items}
            for k, items in sorted(url_dups.items(), key=lambda kv: -len(kv[1]))
        ],
        "folder_duplicates": [
            {"folder": k, "items": items}
            for k, items in folder_dups.items()
        ],
        "similar_clusters": [
            _cluster_to_dict(c) for c in similar
        ],
        "bad_names": [
            {"recipe": r, "reason": reason}
            for r, reason in bad
        ],
        "ai_suggestions": ai_suggestions,
        "ai_available": ai_available,
    }


def _cluster_to_dict(cluster: List[Dict]) -> Dict[str, Any]:
    if cluster and cluster[0].get("_warning"):
        return {"warning": cluster[0]["_warning"]}
    names = sorted(set((r.get("name") or "?") for r in cluster))
    return {"names": names, "items": cluster}
