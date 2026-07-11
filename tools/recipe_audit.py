#!/usr/bin/env python3
"""tools/recipe_audit.py — CLI-Wrapper für den Recipe-Audit.

Die Such-Logik liegt in app/recipes/audit.py und wird vom Web-UI-Dashboard
(/api/audit) und diesem CLI-Tool gemeinsam genutzt. Hier rendern wir nur
das Markdown-Output für SSH/PowerShell-Konsum.

Aufruf:
  cd /opt/scrapper
  sudo -u scrapper venv/bin/python -m tools.recipe_audit
  sudo -u scrapper venv/bin/python -m tools.recipe_audit --ai
  sudo -u scrapper venv/bin/python -m tools.recipe_audit --out /tmp/audit.md

Aus PowerShell (ohne sich einzuloggen):
  ssh proxmox "pct exec 200 -- sudo -u scrapper /opt/scrapper/venv/bin/python -m tools.recipe_audit --ai"

Read-Only. Ändert NIE etwas in DB oder Filesystem.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.recipes.audit import (
    SIMILARITY_THRESHOLD,
    load_recipes,
    find_exact_duplicates,
    find_url_duplicates,
    find_folder_duplicates,
    find_similar_names,
    find_bad_names,
    ai_suggest_batch,
)

DEFAULT_DB_PATH = Path("/opt/scrapper/data/scrapper.db")


def _read_openai_config() -> Optional[Dict[str, Any]]:
    """Lädt OpenAI-Cfg direkt aus der YAML (ohne app.config_store-Import,
    der würde Side-Effects auslösen)."""
    cfg_path = Path("/opt/scrapper/data/config.yaml")
    if not cfg_path.exists():
        return None
    try:
        import yaml
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
        "**Rezepte** über die Suche findbar. Das Tool selbst ändert nichts."
    )
    out.append("")

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

    out.append("## 1) Exakte Namens-Dubletten\n")
    if not exact:
        out.append("_Keine. Sauber._")
    else:
        for name, items in sorted(exact.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### `{name}` — {len(items)}× indiziert")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    out.append("## 2) URL-Dubletten\n")
    if not url_dups:
        out.append("_Keine._")
    else:
        for url, items in sorted(url_dups.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### {url}")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    out.append("## 3) Folder-Path-Dubletten\n")
    if not folder_dups:
        out.append("_Keine. (UNIQUE-Constraint im Schema.)_")
    else:
        out.append("⚠️  _DB-Inkonsistenz._\n")
        for fp, items in folder_dups.items():
            out.append(f"### `{fp}`")
            for r in items:
                out.append(fmt_recipe_line(r))
            out.append("")
    out.append("")

    out.append("## 4) Ähnliche Namen\n")
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

    out.append("## 5) Schlechte / unklare Namen\n")
    if not bad:
        out.append("_Keine._")
    else:
        by_reason: Dict[str, List[Tuple[Dict, str]]] = {}
        for r, reason in bad:
            by_reason.setdefault(reason, []).append((r, reason))
        for reason, items in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            out.append(f"### {reason} ({len(items)}×)\n")
            for r, _ in items:
                line = fmt_recipe_line(r)
                sug = ai_suggestions.get(r["id"])
                if sug:
                    line += f"  \n  💡 **KI-Vorschlag:** `{sug}`"
                desc = (r.get("description") or "").strip()
                if desc:
                    excerpt = desc[:160].replace("\n", " ")
                    if len(desc) > 160:
                        excerpt += "…"
                    line += f"  \n  _Caption:_ {excerpt}"
                else:
                    line += "  \n  _Caption fehlt._"
                out.append(line)
                out.append("")
            out.append("")

    out.append("---\n")
    out.append(f"_Generiert von `tools/recipe_audit.py` · DB: `{db_path}` · "
               f"{len(recipes)} Rezepte gescannt._")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit der recipes-DB: Dubletten + Namens-Vorschläge.",
        epilog=("Beispiel:\n"
                "  sudo -u scrapper /opt/scrapper/venv/bin/python -m tools.recipe_audit --ai --out /tmp/audit.md\n"
                "  less /tmp/audit.md"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH),
                   help=f"SQLite-DB-Pfad (default: {DEFAULT_DB_PATH})")
    p.add_argument("--ai", action="store_true",
                   help="OpenAI für Namens-Vorschläge fragen")
    p.add_argument("--out", default=None,
                   help="Markdown in Datei (default: stdout)")
    p.add_argument("--similarity", type=float, default=SIMILARITY_THRESHOLD,
                   help=f"Cluster-Schwelle (default {SIMILARITY_THRESHOLD})")
    args = p.parse_args(argv)

    db_path = Path(args.db)
    print(f"→ Lade Rezepte aus {db_path} …", file=sys.stderr)
    t0 = time.time()
    try:
        recipes = load_recipes(db_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 2
    print(f"  {len(recipes)} Rezepte ({time.time() - t0:.1f}s)", file=sys.stderr)

    exact = find_exact_duplicates(recipes)
    url_dups = find_url_duplicates(recipes)
    folder_dups = find_folder_duplicates(recipes)
    t1 = time.time()
    similar = find_similar_names(recipes, threshold=args.similarity)
    bad = find_bad_names(recipes)
    print(f"→ Findings: {len(exact)} exakt, {len(url_dups)} URL, "
          f"{len(folder_dups)} folder, {len(similar)} similar, "
          f"{len(bad)} bad ({time.time() - t1:.1f}s)", file=sys.stderr)

    ai_suggestions: Dict[int, str] = {}
    if args.ai and bad:
        oa = _read_openai_config()
        if not oa:
            print("  ⚠️  OpenAI-Config nicht gefunden — skip --ai", file=sys.stderr)
        else:
            t2 = time.time()
            ai_suggestions = ai_suggest_batch(bad, oa)
            print(f"  KI: {len(ai_suggestions)} Vorschläge ({time.time() - t2:.1f}s)",
                  file=sys.stderr)

    # Markdown braucht das alte Dict-Format — find_*_duplicates() liefert das schon
    report = render_report(recipes, exact, url_dups, folder_dups,
                           similar, bad, ai_suggestions, db_path=db_path)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n✓ Report geschrieben: {args.out}", file=sys.stderr)
    else:
        print(report)

    return 1 if (exact or url_dups or folder_dups or similar or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
