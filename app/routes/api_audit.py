"""Audit-Dashboard-API.

Endpoints:
  GET /api/audit                 — alle Findings als JSON (read-only)
  GET /api/audit?with_ai=true    — zusätzlich KI-Namensvorschläge

Nutzt die gleiche Library wie das CLI-Tool (app.recipes.audit), nur dass
hier JSON statt Markdown raus geht. Damit bleiben CLI-Output und UI-Output
inhaltlich identisch.

Bewusst KEINE Mutations-Endpoints in dieser Phase. Inline-Rename / Delete /
Merge brauchen FS-Cleanup-Logik und sollen erst in Phase 2 kommen. User
fixt findings über das bestehende Recipe-Detail-Modal manuell.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..config_store import get_config
from ..db import get_db
from ..recipes.audit import run_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_auth)])


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

    Mit Bestands-Cap: für n > 2000 Rezepte wird der similar-name-search
    übersprungen (siehe audit.SIMILAR_SEARCH_MAX_N). Restliche Suchen
    laufen O(n) und sind unkritisch."""
    db = get_db()
    openai_cfg = _openai_config_for_audit() if with_ai else None
    result = run_audit(db, similarity_threshold=similarity, openai_cfg=openai_cfg)
    # Counts als Summary mitliefern damit das Frontend Header-Badges rendern kann
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
    }
    return result
