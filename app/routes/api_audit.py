"""Audit-Dashboard-API.

Endpoints:
  GET  /api/audit                       — alle Findings als JSON (read-only)
  GET  /api/audit?with_ai=true          — zusätzlich KI-Namensvorschläge
  POST /api/audit/ai-sanity             — Background-Job: KI prüft Pfad+Name-Konsistenz für ALLE Rezepte
  GET  /api/audit/ai-sanity/status      — Progress des laufenden Jobs
  POST /api/audit/finding/{id}/resolve  — KI-Finding als 'erledigt' markieren
  POST /api/audit/finding/{id}/apply    — KI-Vorschlag tatsächlich auf Folder/Name anwenden
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import require_auth
from ..config_store import get_config
from ..core.analyzer import build_analyzer
from ..db import get_db
from ..recipes.audit import run_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_auth)])


# ─── KI-Sanity Background-Job State ─────────────────────────────────────
# Single-threaded global state — gleichzeitige Runs verbieten weil API-Cost
# und weil die DB-Findings sonst durcheinander geschrieben werden.
_ai_sanity_state = {
    "running": False,
    "total": 0,
    "processed": 0,
    "findings": 0,
    "started_at": None,
    "error": None,
}
_ai_sanity_lock = threading.Lock()


def _openai_config_for_audit() -> Optional[Dict[str, Any]]:
    """Holt OpenAI-Cfg aus der App-Config. Returnt None wenn kein Key da
    oder maskiert (User hat nicht gespeichert)."""
    cfg = get_config()
    oa = (cfg.get("ai", default={}) or {}).get("openai") or {}
    api_key = (oa.get("api_key") or "").strip()
    if not api_key or set(api_key) <= {"*", "•"}:
        return None
    return {
        "api_key": api_key,
        "model": (oa.get("model") or "gpt-4o-mini").strip(),
        "base_url": (oa.get("base_url") or "").strip() or "https://api.openai.com/v1",
        "timeout": int(oa.get("timeout") or 30),
    }


@router.get("")
def get_audit(
    with_ai: bool = Query(False, description="OpenAI-Namensvorschläge anfordern"),
    similarity: float = Query(0.85, ge=0.5, le=0.99,
                              description="Schwelle für Ähnlichkeits-Cluster"),
) -> Dict[str, Any]:
    """Vollständiger Audit-Lauf. Synchron, blockiert bei großen Beständen
    + with_ai mehrere Sekunden — Frontend zeigt Spinner.

    Liefert zusätzlich FS-Konflikte (UNIQUE-Crashes vom letzten Sync) und
    bestehende KI-Sanity-Findings (vom letzten POST /audit/ai-sanity-Lauf)."""
    db = get_db()
    openai_cfg = _openai_config_for_audit() if with_ai else None
    result = run_audit(db, similarity_threshold=similarity, openai_cfg=openai_cfg)

    # FS-Konflikte (kommen vom letzten sync_filesystem-Run)
    sync_errors = db.sync_errors_list()
    result["sync_errors"] = sync_errors

    # KI-Sanity-Findings (persistent, vom letzten ai-sanity-Run)
    cat_findings = db.audit_ai_findings_list(finding_type="category_mismatch", only_open=True)
    name_findings = db.audit_ai_findings_list(finding_type="name_mismatch", only_open=True)
    result["ai_category_findings"] = cat_findings
    result["ai_name_findings"] = name_findings

    # Summary erweitert um die drei neuen Kategorien
    result["summary"] = {
        "exact_count": sum(len(g["items"]) for g in result["exact_duplicates"]),
        "exact_groups": len(result["exact_duplicates"]),
        "url_count": sum(len(g["items"]) for g in result["url_duplicates"]),
        "url_groups": len(result["url_duplicates"]),
        "folder_count": sum(len(g["items"]) for g in result["folder_duplicates"]),
        "similar_count": sum(
            len(c.get("items") or []) for c in result["similar_clusters"]
            if "warning" not in c
        ),
        "similar_clusters": len([c for c in result["similar_clusters"] if "warning" not in c]),
        "bad_count": len(result["bad_names"]),
        "with_ai_suggestions": len(result["ai_suggestions"]),
        "sync_error_count": len(sync_errors),
        "ai_category_count": len(cat_findings),
        "ai_name_count": len(name_findings),
    }
    return result


@router.post("/ai-sanity")
def start_ai_sanity_check() -> Dict[str, Any]:
    """Startet einen Background-Job der KI-Konsistenz für ALLE Rezepte prüft.
    Nicht blockierend — Progress via GET /ai-sanity/status pollen.

    Pro Rezept ein KI-Call (~$0.001) der Category-Match UND Name-Match prüft.
    Bei 100 Rezepten ~$0.10 + ~2-3 Minuten Laufzeit."""
    with _ai_sanity_lock:
        if _ai_sanity_state["running"]:
            raise HTTPException(409, "KI-Sanity-Check läuft bereits")
        openai_cfg = _openai_config_for_audit()
        if not openai_cfg:
            raise HTTPException(400, "Kein gültiger OpenAI-Key konfiguriert")

        # Total-Count vorab — alle Rezepte mit description >= 20 chars
        db = get_db()
        with db.conn() as c:
            total = int(c.execute(
                "SELECT COUNT(*) FROM recipes "
                "WHERE description IS NOT NULL AND length(description) >= 20"
            ).fetchone()[0])

        _ai_sanity_state.update({
            "running": True, "total": total, "processed": 0, "findings": 0,
            "started_at": time.time(), "error": None,
        })
        threading.Thread(
            target=_ai_sanity_worker, args=(openai_cfg,),
            name="audit-ai-sanity", daemon=True,
        ).start()
    return {"ok": True, "total": total}


@router.get("/ai-sanity/status")
def ai_sanity_status() -> Dict[str, Any]:
    """Progress-Polling. Frontend ruft alle 2-3s während running=true."""
    with _ai_sanity_lock:
        return dict(_ai_sanity_state)


def _ai_sanity_worker(openai_cfg: Dict[str, Any]) -> None:
    """Background-Worker — iteriert alle Rezepte mit description, ruft KI,
    schreibt Findings in DB."""
    db = get_db()
    try:
        analyzer = build_analyzer({"openai": openai_cfg})
    except Exception as e:
        with _ai_sanity_lock:
            _ai_sanity_state.update({"running": False, "error": f"Analyzer init: {e}"})
        return

    try:
        with db.conn() as c:
            rows = c.execute(
                "SELECT id, name, type, category, description FROM recipes "
                "WHERE description IS NOT NULL AND length(description) >= 20"
            ).fetchall()
            recipes = [dict(r) for r in rows]

        # Alte Findings löschen — nur die NICHT-resolved, die werden neu gesetzt.
        # Resolved-Findings (User hat schon entschieden) bleiben als Audit-Trail.
        with db.conn() as c:
            c.execute("DELETE FROM audit_ai_findings WHERE resolved=0")

        findings_count = 0
        for i, r in enumerate(recipes):
            try:
                check = analyzer.audit_recipe_consistency(
                    r["name"], r["description"], r["type"], r["category"]
                )
                if not check["category_ok"] and check["category_suggestion"]:
                    db.audit_ai_finding_set(
                        r["id"], "category_mismatch",
                        f"{r['type'] or ''}/{r['category'] or ''}",
                        check["category_suggestion"],
                        check["category_reason"] or "",
                    )
                    findings_count += 1
                if not check["name_ok"] and check["name_suggestion"]:
                    db.audit_ai_finding_set(
                        r["id"], "name_mismatch",
                        r["name"] or "",
                        check["name_suggestion"],
                        check["name_reason"] or "",
                    )
                    findings_count += 1
            except Exception as e:
                logger.warning(f"ai-sanity Rezept #{r['id']}: {e}")
            with _ai_sanity_lock:
                _ai_sanity_state["processed"] = i + 1
                _ai_sanity_state["findings"] = findings_count

        with _ai_sanity_lock:
            _ai_sanity_state["running"] = False
        logger.info(
            f"ai-sanity fertig: {len(recipes)} Rezepte geprüft, {findings_count} Findings"
        )
    except Exception as e:
        logger.exception("ai-sanity worker crash")
        with _ai_sanity_lock:
            _ai_sanity_state.update({"running": False, "error": str(e)})


@router.post("/finding/{finding_id}/resolve")
def resolve_finding(finding_id: int) -> Dict[str, Any]:
    """Markiert ein KI-Finding als 'erledigt' (ignoriert vom User).
    Wird im UI vom 'Ignorieren'-Button aufgerufen."""
    db = get_db()
    db.audit_ai_finding_resolve(finding_id)
    return {"ok": True}


class DeleteByPathPayload(BaseModel):
    folder_path: str


@router.post("/recipe/delete-by-path")
def delete_folder_by_path(payload: DeleteByPathPayload) -> Dict[str, Any]:
    """Löscht einen Folder physisch — für FS-Konflikt-Folder die NIE in der DB
    angekommen sind (UNIQUE-Crash beim Sync). Über recipe_id-DELETE geht das
    nicht weil es keinen DB-Eintrag gibt.

    Sicherheit: Pfad muss unterhalb von cfg.paths.recipe_dir liegen
    (sonst Path-Traversal-Angriff via '../../etc'). Plus: sync_errors-Eintrag
    wird mit weggeräumt."""
    import shutil
    from pathlib import Path
    cfg = get_config()
    recipe_root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    target = Path(payload.folder_path).resolve()
    try:
        target.relative_to(recipe_root)
    except ValueError:
        raise HTTPException(400, f"Pfad nicht im Recipe-Root: {target}")
    if not target.exists():
        # Schon weg — sync_errors-Eintrag trotzdem löschen
        with get_db().conn() as c:
            c.execute("DELETE FROM sync_errors WHERE folder_path=?", (str(target),))
        return {"ok": True, "note": "Folder war schon weg"}
    if not target.is_dir():
        raise HTTPException(400, f"Kein Folder: {target}")
    try:
        shutil.rmtree(target)
    except Exception as e:
        raise HTTPException(500, f"Löschen fehlgeschlagen: {e}")
    # sync_errors-Entry weg
    with get_db().conn() as c:
        c.execute("DELETE FROM sync_errors WHERE folder_path=?", (str(target),))
    logger.info(f"FS-Konflikt-Folder gelöscht: {target}")
    return {"ok": True}
