#!/usr/bin/env python3
"""tools/recipe_audit.py — Dubletten + Namens-Audit für die recipes-DB.

Standalone-CLI, gedacht für Ad-hoc-Ausführung per SSH (oder PowerShell mit
ssh-Forwarding). Läuft NICHT als Webservice — bewusst getrennt damit
schwergewichtige Audit-Operationen nicht die laufende UI bremsen.

Aufruf:
  cd /opt/scrapper
  sudo -u scrapper venv/bin/python -m tools.recipe_audit            # nur Report
  sudo -u scrapper venv/bin/python -m tools.recipe_audit --ai       # mit KI-Namensvorschlägen
  sudo -u scrapper venv/bin/python -m tools.recipe_audit --out /tmp/audit.md

Aus Windows-PowerShell, ohne dass man sich erst auf den Container einloggen muss:
  ssh proxmox "pct exec 200 -- sudo -u scrapper /opt/scrapper/venv/bin/python -m tools.recipe_audit"

Was es tut:
  1. Exakte Namens-Dubletten (gleicher Name in mehreren recipes-Zeilen)
  2. URL-Dubletten (gleiche TikTok/Instagram-URL doppelt indiziert)
  3. Ähnliche Namen (difflib.SequenceMatcher >= 0.85, sortiert in Cluster)
  4. Folder-Path-Dubletten (sollte nicht passieren — DB hat UNIQUE-Constraint —
     aber wir checken's defensiv falls jemand DB manuell editiert hat)
  5. Schlechte/unklare Namen: 'Unbekannt', URL-Reste, sehr kurze Namen,
     Datums-/Zahlen-Müll. Mit --ai: ein einzelner Batch-Call holt KI-Vorschläge.

Output: Markdown nach stdout oder --out. Auf der Konsole rendert das gut
genug zum Drüberlesen (Header sind klar erkennbar auch ohne Renderer).

Read-Only: das Tool ändert NIE etwas in DB oder Filesystem. Der User fixt
händisch via Web-UI History-Tab (Rename/Move/Delete).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ────────────────────────────────────────────────────────────────────────
# Defaults
# ────────────────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path("/opt/scrapper/data/scrapper.db")

# Schwelle für Ähnlichkeits-Cluster. 0.85 = "Bolognese"↔"Bolognase" matched,
# "Pasta"↔"Pizza" matched NICHT. Tuned auf False-Positives-min.
SIMILARITY_THRESHOLD = 0.85

# Schlechte-Namen-Patterns
BAD_NAME_PATTERNS = [
    (re.compile(r"^(unbekannt|unknown|test|tmp|temp|untitled)$", re.I), "Generischer Platzhalter"),
    (re.compile(r"https?://|\.com|\.org|\.net|@\w+", re.I), "URL-/Mention-Rest im Namen"),
    (re.compile(r"^[\d\s\-_./:]+$"), "Nur Zahlen/Datum/Sonderzeichen"),
    (re.compile(r"^video[_\-\s]?\d*$", re.I), "Generisches video_xxx"),
    (re.compile(r"\bunbekannt\b", re.I), "'Unbekannt' im Namen (sollte rein)"),
    (re.compile(r"^\(.*\)$"), "Komplett in Klammern (vermutlich Subtitle)"),
]

# Sehr-kurze-Namen-Heuristik
SHORT_NAME_MIN_CHARS = 4   # alles ≤3 chars ist verdächtig
SHORT_NAME_SUSPICIOUS_WORDS = 1   # 1 Wort + <= 5 chars = verdächtig


# ────────────────────────────────────────────────────────────────────────
# DB
# ────────────────────────────────────────────────────────────────────────

def load_recipes(db_path: Path) -> List[Dict[str, Any]]:
    """Holt alle recipes (read-only). Kein WAL-Touch, kein Schreibzugriff."""
    if not db_path.exists():
        sys.exit(f"FEHLER: DB nicht gefunden: {db_path}")
    # mode=ro damit wir wirklich nie schreiben — auch nicht versehentlich
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Tolerieren falls die recipes-Tabelle noch nicht existiert (kein
        # Recipe-Browser ausgeführt) — dann ist die Liste eben leer.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes'"
        ).fetchone()
        if not rows:
            sys.exit(
                "FEHLER: 'recipes'-Tabelle fehlt. Erst im Web-UI den Tab "
                "'Rezepte' öffnen, damit der lazy FS-Sync läuft."
            )
        out = []
        for r in conn.execute("SELECT * FROM recipes ORDER BY id").fetchall():
            d = dict(r)
            # description weiter optional; bei NULL → leer
            d["description"] = d.get("description") or ""
            out.append(d)
        return out
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────
# Dublette: exakt + URL + folder + similar
# ────────────────────────────────────────────────────────────────────────

def find_exact_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """Gleiche `name`-Werte (case-insensitive) — Indikator für Re-Scrapes."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        key = (r.get("name") or "").strip().lower()
        if not key:
            continue
        buckets.setdefault(key, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_url_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """Gleiche URL mehrfach indiziert (sollte über history.url eigentlich
    nicht passieren, kann aber durch manuelles Editieren entstehen)."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        u = (r.get("url") or "").strip()
        if not u:
            continue
        buckets.setdefault(u, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_folder_duplicates(recipes: List[Dict]) -> Dict[str, List[Dict]]:
    """folder_path-Duplikate (DB hat UNIQUE, aber check ist billig)."""
    buckets: Dict[str, List[Dict]] = {}
    for r in recipes:
        f = (r.get("folder_path") or "").strip()
        if not f:
            continue
        buckets.setdefault(f, []).append(r)
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_similar_names(recipes: List[Dict], threshold: float = SIMILARITY_THRESHOLD,
                      ) -> List[List[Dict]]:
    """Findet Cluster von Rezepten mit ähnlichen Namen (Levenshtein-Variante
    via difflib). Union-Find für Cluster-Bildung.

    Komplexität: O(n²). Bei n=500 sind das 125k Vergleiche — auf einer
    schnellen Maschine ~1s. Bei n=5000 wären's 12.5M und 30s+. Wir
    haben eine Cutoff bei n=2000, danach skippen wir dieses Modul mit
    einer Warnung statt 5min zu hängen.
    """
    n = len(recipes)
    if n == 0:
        return []
    if n > 2000:
        return [[{"_warning": f"Skipped similar-name-search wegen {n} Rezepten "
                              f">2000 (O(n²) zu teuer). Nur exakte Dubletten gecheckt."}]]

    # Vorab Filter: nur "echte" Namen vergleichen (kein "Unbekannt", kein <4 char)
    candidates = []
    for r in recipes:
        name = (r.get("name") or "").strip()
        if len(name) < 4 or name.lower() == "unbekannt":
            continue
        candidates.append((r, name.lower()))

    # Union-Find für Cluster
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

    for i in range(len(candidates)):
        name_i = candidates[i][1]
        for j in range(i + 1, len(candidates)):
            name_j = candidates[j][1]
            # Kurze Wörter: nur wenn Längen-Differenz klein
            if abs(len(name_i) - len(name_j)) > max(3, len(name_i) // 3):
                continue
            # Exakt-Dubletten wurden vorher schon gefunden — hier nur "ähnlich"
            if name_i == name_j:
                continue
            ratio = SequenceMatcher(None, name_i, name_j).ratio()
            if ratio >= threshold:
                union(i, j)

    # Cluster sammeln (nur Cluster mit ≥2 Mitgliedern)
    clusters: Dict[int, List[Dict]] = {}
    for i, (r, _) in enumerate(candidates):
        root = find(i)
        clusters.setdefault(root, []).append(r)
    return [c for c in clusters.values() if len(c) >= 2]


# ────────────────────────────────────────────────────────────────────────
# Schlechte Namen
# ────────────────────────────────────────────────────────────────────────

def find_bad_names(recipes: List[Dict]) -> List[Tuple[Dict, str]]:
    """Liefert (recipe, grund) für jedes Rezept mit fragwürdigem Namen."""
    out = []
    seen_ids = set()
    for r in recipes:
        name = (r.get("name") or "").strip()
        if not name:
            out.append((r, "Name leer"))
            seen_ids.add(r["id"])
            continue
        # Pattern-Checks
        matched = False
        for pat, reason in BAD_NAME_PATTERNS:
            if pat.search(name):
                out.append((r, reason))
                seen_ids.add(r["id"])
                matched = True
                break
        if matched:
            continue
        # Längen-Heuristik
        if len(name) < SHORT_NAME_MIN_CHARS:
            out.append((r, f"Sehr kurz ({len(name)} Zeichen)"))
            seen_ids.add(r["id"])
            continue
        words = name.split()
        if len(words) <= SHORT_NAME_SUSPICIOUS_WORDS and len(name) <= 5:
            out.append((r, "Nur 1 kurzes Wort"))
            seen_ids.add(r["id"])
    return out


# ────────────────────────────────────────────────────────────────────────
# OpenAI-Vorschläge (optional, mit --ai)
# ────────────────────────────────────────────────────────────────────────

def _read_openai_config() -> Optional[Dict[str, str]]:
    """Lädt OpenAI-Cfg aus der scrapper-config.yaml. Wir importieren nicht
    `app.config_store` (das würde Side-Effects auslösen), sondern lesen die
    YAML direkt."""
    cfg_path = Path("/opt/scrapper/data/config.yaml")
    if not cfg_path.exists():
        return None
    try:
        import yaml  # ist in requirements.txt
    except ImportError:
        return None
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    oa = (cfg.get("ai") or {}).get("openai") or {}
    api_key = (oa.get("api_key") or "").strip()
    if not api_key or set(api_key) <= {"*", "•"}:
        return None
    return {
        "api_key": api_key,
        "model": (oa.get("model") or "gpt-4o-mini").strip(),
        "base_url": (oa.get("base_url") or "").strip() or "https://api.openai.com/v1",
        "timeout": int(oa.get("timeout") or 30),
    }


def ai_suggest_batch(bad_items: List[Tuple[Dict, str]], openai_cfg: Dict[str, str],
                    ) -> Dict[int, str]:
    """Ein einzelner OpenAI-Call mit ALLEN bad-name-Rezepten. Spart Roundtrips
    + Geld. Returnt {recipe_id: suggested_name}."""
    import requests

    # Nur Rezepte mit verwertbarer Beschreibung an die KI geben
    candidates = []
    for r, reason in bad_items:
        desc = (r.get("description") or "").strip()
        if len(desc) >= 30:
            # Caption auf max 600 chars beschneiden — sonst wird der Prompt
            # unnötig teuer und KI verzettelt sich
            candidates.append({
                "id": r["id"],
                "current_name": r.get("name") or "",
                "type": r.get("type") or "",
                "category": r.get("category") or "",
                "description_excerpt": desc[:600],
            })

    if not candidates:
        return {}

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
        r = requests.post(
            f"{openai_cfg['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {openai_cfg['api_key']}"},
            json={
                "model": openai_cfg["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=openai_cfg["timeout"],
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
        print(f"  OpenAI HTTP error: {e.response.status_code} — {e.response.text[:200]}",
              file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  OpenAI call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {}


# ────────────────────────────────────────────────────────────────────────
# Report-Renderer (Markdown)
# ────────────────────────────────────────────────────────────────────────

def fmt_recipe_line(r: Dict, *, indent: str = "  ") -> str:
    added = r.get("source_added_at")
    added_str = ""
    if added:
        try:
            added_str = f"  ({datetime.fromtimestamp(float(added)).date()})"
        except (ValueError, OSError):
            pass
    folder = r.get("folder_path") or "(kein Folder)"
    return (
        f"{indent}- **#{r['id']}** `{r.get('name') or '(leer)'}`"
        f"{added_str}  \n"
        f"{indent}  Pfad: `{folder}`"
    )


def render_report(
    recipes: List[Dict],
    exact: Dict[str, List[Dict]],
    url_dups: Dict[str, List[Dict]],
    folder_dups: Dict[str, List[Dict]],
    similar: List[List[Dict]],
    bad: List[Tuple[Dict, str]],
    ai_suggestions: Dict[int, str],
    db_path: Path,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out: List[str] = []
    out.append(f"# Recipe-Audit · {now}")
    out.append("")
    out.append(f"Indizierte Rezepte: **{len(recipes)}**")
    out.append("")
    out.append(
        "Bearbeitung: jeder Treffer hat eine `#ID` — im Web-UI unter "
        "**Historie** über die URL/Namens-Suche findbar, dort "
        "umbenennen/verschieben/löschen. Das Tool selbst ändert nichts."
    )
    out.append("")

    # Übersicht
    out.append("## Übersicht")
    out.append("")
    out.append(f"- Exakte Namens-Dubletten: **{sum(len(v) for v in exact.values())}** "
               f"in {len(exact)} Gruppen")
    out.append(f"- URL-Dubletten: **{sum(len(v) for v in url_dups.values())}** "
               f"in {len(url_dups)} Gruppen")
    out.append(f"- Folder-Path-Dubletten: **{sum(len(v) for v in folder_dups.values())}** "
               f"in {len(folder_dups)} Gruppen")
    sim_count = sum(len(c) for c in similar if not c[0].get("_warning"))
    out.append(f"- Ähnliche Namen (Cluster): **{sim_count}** in "
               f"{len([c for c in similar if not c[0].get('_warning')])} Clustern")
    out.append(f"- Verdächtige Namen: **{len(bad)}**" + 
               (f" (mit {len(ai_suggestions)} KI-Vorschlägen)" if ai_suggestions else ""))
    out.append("")

    # 1. Exakte Dubletten
    out.append("## 1) Exakte Namens-Dubletten")
    out.append("")
    if not exact:
        out.append("_Keine. Sauber._")
    else:
        for name, items in sorted(exact.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### `{name}` — {len(items)}× indiziert")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    # 2. URL-Dubletten
    out.append("## 2) URL-Dubletten (gleicher Quell-Link mehrfach)")
    out.append("")
    if not url_dups:
        out.append("_Keine._")
    else:
        for url, items in sorted(url_dups.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### {url}")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    # 3. Folder-Path-Dubletten (selten — UNIQUE-Constraint im Schema)
    out.append("## 3) Folder-Path-Dubletten")
    out.append("")
    if not folder_dups:
        out.append("_Keine. (Schema hat UNIQUE-Constraint — sollte hier nie was stehen.)_")
    else:
        out.append("⚠️  _DB-Inkonsistenz — sollte nicht passieren. Bitte manuell prüfen._")
        out.append("")
        for fp, items in folder_dups.items():
            out.append(f"### `{fp}`")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    # 4. Ähnliche Namen
    out.append("## 4) Ähnliche Namen (potentielle Tippfehler)")
    out.append("")
    if not similar:
        out.append("_Keine._")
    else:
        for cluster in similar:
            if cluster and cluster[0].get("_warning"):
                out.append(f"> ⚠️  {cluster[0]['_warning']}")
                continue
            names = sorted(set((r.get("name") or "?") for r in cluster))
            out.append(f"### Cluster: {' / '.join(repr(n) for n in names)}")
            for r in cluster:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    # 5. Schlechte Namen + AI-Vorschläge
    out.append("## 5) Schlechte / unklare Namen")
    out.append("")
    if not bad:
        out.append("_Keine._")
    else:
        # Gruppieren nach Grund — übersichtlicher
        by_reason: Dict[str, List[Tuple[Dict, str]]] = {}
        for r, reason in bad:
            by_reason.setdefault(reason, []).append((r, reason))
        for reason, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### {reason} ({len(items)}×)")
            out.append("")
            for r, _ in items:
                line = fmt_recipe_line(r)
                # KI-Vorschlag?
                sug = ai_suggestions.get(r["id"])
                if sug:
                    line += f"  \n  💡 **KI-Vorschlag:** `{sug}`"
                # Caption-Excerpt
                desc = (r.get("description") or "").strip()
                if desc:
                    excerpt = desc[:160].replace("\n", " ")
                    if len(desc) > 160:
                        excerpt += "…"
                    line += f"  \n  _Caption:_ {excerpt}"
                else:
                    line += "  \n  _Caption fehlt — manuell anschauen._"
                out.append(line)
                out.append("")
            out.append("")

    out.append("---")
    out.append("")
    out.append(f"_Generiert von `tools/recipe_audit.py` · "
               f"DB: `{db_path}` · "
               f"{len(recipes)} Rezepte gescannt._")
    return "\n".join(out)


# ────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit der recipes-DB: Dubletten + Namens-Vorschläge.",
        epilog=(
            "Beispiel:\n"
            "  sudo -u scrapper /opt/scrapper/venv/bin/python -m tools.recipe_audit --ai --out /tmp/audit.md\n"
            "  less /tmp/audit.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH),
                   help=f"SQLite-DB-Pfad (default: {DEFAULT_DB_PATH})")
    p.add_argument("--ai", action="store_true",
                   help="OpenAI für Namens-Vorschläge fragen (kostet ~$0.001 pro 50 Items)")
    p.add_argument("--out", default=None,
                   help="Markdown in Datei schreiben (default: stdout)")
    p.add_argument("--similarity", type=float, default=SIMILARITY_THRESHOLD,
                   help=f"Schwelle für Ähnlichkeits-Cluster (default {SIMILARITY_THRESHOLD})")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    print(f"→ Lade Rezepte aus {db_path} …", file=sys.stderr)
    t0 = time.time()
    recipes = load_recipes(db_path)
    print(f"  {len(recipes)} Rezepte geladen ({time.time() - t0:.1f}s)", file=sys.stderr)

    print(f"→ Suche exakte Dubletten …", file=sys.stderr)
    exact = find_exact_duplicates(recipes)
    print(f"  {len(exact)} Gruppen", file=sys.stderr)

    print(f"→ Suche URL-Dubletten …", file=sys.stderr)
    url_dups = find_url_duplicates(recipes)
    print(f"  {len(url_dups)} Gruppen", file=sys.stderr)

    print(f"→ Suche Folder-Path-Dubletten …", file=sys.stderr)
    folder_dups = find_folder_duplicates(recipes)
    print(f"  {len(folder_dups)} Gruppen", file=sys.stderr)

    print(f"→ Suche ähnliche Namen (Schwelle {args.similarity}) …", file=sys.stderr)
    t1 = time.time()
    similar = find_similar_names(recipes, threshold=args.similarity)
    print(f"  {len(similar)} Cluster ({time.time() - t1:.1f}s)", file=sys.stderr)

    print(f"→ Suche schlechte Namen …", file=sys.stderr)
    bad = find_bad_names(recipes)
    print(f"  {len(bad)} verdächtig", file=sys.stderr)

    ai_suggestions: Dict[int, str] = {}
    if args.ai and bad:
        print(f"→ Frage OpenAI nach Namens-Vorschlägen …", file=sys.stderr)
        oa = _read_openai_config()
        if not oa:
            print("  ⚠️  OpenAI-Config nicht gefunden oder api_key gemaskt — skip --ai",
                  file=sys.stderr)
        else:
            t2 = time.time()
            ai_suggestions = ai_suggest_batch(bad, oa)
            print(f"  {len(ai_suggestions)} Vorschläge ({time.time() - t2:.1f}s)",
                  file=sys.stderr)

    report = render_report(recipes, exact, url_dups, folder_dups, similar, bad,
                           ai_suggestions, db_path=db_path)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n✓ Report geschrieben: {args.out}", file=sys.stderr)
    else:
        print(report)

    # Exit-Code: 0 = nichts gefunden, 1 = Findings da (script-freundlich)
    has_findings = bool(exact or url_dups or folder_dups or similar or bad)
    return 1 if has_findings else 0


if __name__ == "__main__":
    sys.exit(main())
