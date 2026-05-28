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
    folder_findings = db.audit_ai_findings_list(finding_type="folder_mismatch", only_open=True)
    result["ai_category_findings"] = cat_findings
    result["ai_name_findings"] = name_findings
    result["ai_folder_findings"] = folder_findings

    # Verdächtig leere Rezepte: status='ok' aber 0 Zutaten obwohl Description da ist.
    # Signal für: KI hat in einem früheren Lauf nichts gefunden (oft alter Prompt-
    # Bug oder zu restriktive Klassifikation). User kann per Bulk-Button alle
    # auf 'pending' zurücksetzen.
    with db.conn() as c:
        empty_rows = c.execute("""
            SELECT r.id, r.name, length(r.description) as desc_len, r.folder_path
            FROM recipes r
            LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id
            WHERE r.ingredients_status = 'ok'
              AND r.description IS NOT NULL
              AND length(r.description) >= 20
            GROUP BY r.id
            HAVING COUNT(ri.id) = 0
            ORDER BY length(r.description) DESC
            LIMIT 50
        """).fetchall()
    empty_recipes = [dict(r) for r in empty_rows]
    result["empty_recipes"] = empty_recipes

    # Daten-Lücken-Detections (kein Bild / keine Schritte / keine URL /
    # nur 1-2 Zutaten / keine Description). EIN Query mit allen Flags pro
    # Rezept — Python sortiert dann in die Buckets. Limit pro Bucket auf
    # 100 damit das UI nicht überflutet.
    with db.conn() as c:
        all_rows = c.execute("""
            SELECT r.id, r.name, r.folder_path, r.url, r.thumb_filename,
                   r.ingredients_status, r.calories_per_serving,
                   COALESCE(length(r.description), 0) as desc_len,
                   (SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id=r.id) as ing_count,
                   (SELECT COUNT(*) FROM recipe_steps WHERE recipe_id=r.id) as step_count
            FROM recipes r
        """).fetchall()

    no_image, no_steps, no_url, few_ingredients, no_description, no_nutrition = [], [], [], [], [], []
    for row in all_rows:
        d = dict(row)
        if not d.get("thumb_filename"):
            no_image.append(d)
        # 'no_steps' nur sinnvoll wenn Zutaten da sind (sonst ist's das gleiche
        # wie 'empty_recipes' und doppelt-counted)
        if d["ing_count"] > 0 and d["step_count"] == 0:
            no_steps.append(d)
        if not d.get("url"):
            no_url.append(d)
        # 'few_ingredients': 1-2 Zutaten ist verdächtig — meist KI-Halbextrakt
        if 0 < d["ing_count"] < 3:
            few_ingredients.append(d)
        if d["desc_len"] < 20:
            no_description.append(d)
        # 'no_nutrition': KI-Schätzung fehlt obwohl >=3 Zutaten da
        if d["ing_count"] >= 3 and not d.get("calories_per_serving"):
            no_nutrition.append(d)

    data_gaps = {
        "no_image": no_image[:100],
        "no_steps": no_steps[:100],
        "no_url": no_url[:100],
        "few_ingredients": few_ingredients[:100],
        "no_description": no_description[:100],
        "no_nutrition": no_nutrition[:100],
    }
    result["data_gaps"] = data_gaps

    # Summary erweitert um die drei neuen Kategorien + Daten-Lücken
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
        "ai_folder_count": len(folder_findings),
        "empty_recipe_count": len(empty_recipes),
        "no_image_count": len(no_image),
        "no_steps_count": len(no_steps),
        "no_url_count": len(no_url),
        "few_ingredients_count": len(few_ingredients),
        "no_description_count": len(no_description),
        "no_nutrition_count": len(no_nutrition),
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
                "SELECT id, name, type, category, description, folder_path FROM recipes "
                "WHERE description IS NOT NULL AND length(description) >= 20"
            ).fetchall()
            recipes = [dict(r) for r in rows]

        # Alte Findings löschen — nur die NICHT-resolved, die werden neu gesetzt.
        # Resolved-Findings (User hat schon entschieden) bleiben als Audit-Trail.
        with db.conn() as c:
            c.execute("DELETE FROM audit_ai_findings WHERE resolved=0")

        # Parallele KI-Calls (3 gleichzeitig). DB-Schreibvorgänge sind klein
        # und thread-safe (sqlite check_same_thread=False), counter wird mit
        # Lock geschützt. Speed-up: ~3× bei 100 Rezepten (5min → ~1.5min).
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from pathlib import Path as _P

        def _check_one(r: dict) -> int:
            """Returnt Anzahl neuer Findings für dieses Rezept (0-3)."""
            folder_name = _P(r["folder_path"]).name if r.get("folder_path") else None
            local = 0
            try:
                check = analyzer.audit_recipe_consistency(
                    r["name"], r["description"], r["type"], r["category"],
                    folder_name=folder_name,
                )
                if not check["category_ok"] and check["category_suggestion"]:
                    db.audit_ai_finding_set(
                        r["id"], "category_mismatch",
                        f"{r['type'] or ''}/{r['category'] or ''}",
                        check["category_suggestion"], check["category_reason"] or "",
                    )
                    local += 1
                if not check["name_ok"] and check["name_suggestion"]:
                    db.audit_ai_finding_set(
                        r["id"], "name_mismatch",
                        r["name"] or "", check["name_suggestion"],
                        check["name_reason"] or "",
                    )
                    local += 1
                if not check["folder_ok"] and check["folder_suggestion"]:
                    db.audit_ai_finding_set(
                        r["id"], "folder_mismatch",
                        folder_name or "", check["folder_suggestion"],
                        check["folder_reason"] or "",
                    )
                    local += 1
            except Exception as e:
                logger.warning(f"ai-sanity Rezept #{r['id']}: {e}")
            return local

        findings_count = 0
        processed = 0
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sanity") as ex:
            futures = {ex.submit(_check_one, r): r for r in recipes}
            for f in as_completed(futures):
                processed += 1
                try:
                    findings_count += f.result()
                except Exception as e:
                    logger.exception(f"ai-sanity future failed: {e}")
                with _ai_sanity_lock:
                    _ai_sanity_state["processed"] = processed
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


def _sanitize_folder_name(name: str) -> str:
    """Reduziert auf safe-Filenames: keine Slashes, Spaces → Underscore,
    keine Steuerzeichen, keine Windows-Reserved-Chars. Umlaute erlaubt."""
    import re
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name).strip()
    safe = re.sub(r'\s+', '_', safe)
    return safe or 'unnamed'


def _apply_finding_internal(finding_id: int) -> Dict[str, Any]:
    """Wendet einen KI-Vorschlag tatsächlich an. Wird vom HTTP-Endpoint
    /finding/{id}/apply UND vom Bulk-Endpoint /findings/apply-all genutzt.
    Raised HTTPException bei Fehler (HTTP-Routes propagieren; Bulk fängt ab)."""
    import json as _json
    import shutil as _sh
    from pathlib import Path
    db = get_db()

    with db.conn() as c:
        row = c.execute(
            "SELECT f.*, r.folder_path as r_folder_path, r.name as r_name, "
            "       r.type as r_type, r.category as r_category "
            "FROM audit_ai_findings f JOIN recipes r ON r.id=f.recipe_id "
            "WHERE f.id=?",
            (finding_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Finding nicht gefunden")
    finding = dict(row)

    cfg = get_config()
    recipe_root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    old_path = Path(finding["r_folder_path"]).resolve()
    try:
        old_path.relative_to(recipe_root)
    except ValueError:
        raise HTTPException(400, f"Folder-Pfad nicht im Recipe-Root: {old_path}")
    if not old_path.exists():
        raise HTTPException(404, f"Folder nicht da: {old_path}")

    ftype = finding["finding_type"]
    suggested = (finding["suggested_value"] or "").strip()
    if not suggested:
        raise HTTPException(400, "Vorschlag leer — nichts anzuwenden")

    new_path: Path
    db_updates: Dict[str, Any] = {}

    if ftype == "name_mismatch":
        safe = _sanitize_folder_name(suggested)
        new_path = old_path.parent / safe
        if new_path.exists() and new_path != old_path:
            raise HTTPException(409, f"Ziel-Folder existiert: {new_path}")
        db_updates = {"name": suggested, "folder_path": str(new_path)}

    elif ftype == "folder_mismatch":
        safe = _sanitize_folder_name(suggested)
        new_path = old_path.parent / safe
        if new_path.exists() and new_path != old_path:
            raise HTTPException(409, f"Ziel-Folder existiert: {new_path}")
        db_updates = {"folder_path": str(new_path)}

    elif ftype == "category_mismatch":
        parts = suggested.split("/", 1)
        if len(parts) != 2:
            raise HTTPException(400, f"Vorschlag muss 'Typ/Kategorie' sein: {suggested}")
        new_type, new_category = parts[0].strip(), parts[1].strip()
        if not new_type or not new_category:
            raise HTTPException(400, "Typ und Kategorie pflichtig")
        new_path = recipe_root / new_type / new_category / old_path.name
        if new_path.exists() and new_path != old_path:
            raise HTTPException(409, f"Ziel-Folder existiert: {new_path}")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        db_updates = {
            "type": new_type, "category": new_category, "folder_path": str(new_path),
        }
    else:
        raise HTTPException(400, f"Unbekannter finding_type: {ftype}")

    # FS-Move
    if new_path != old_path:
        try:
            _sh.move(str(old_path), str(new_path))
        except Exception as e:
            raise HTTPException(500, f"FS-Move failed: {e}")

    set_clause = ", ".join(f"{k}=?" for k in db_updates.keys())
    params = list(db_updates.values()) + [finding["recipe_id"]]
    with db.conn() as c:
        c.execute(f"UPDATE recipes SET {set_clause} WHERE id=?", params)

    if ftype == "name_mismatch":
        info_file = new_path / "info.json"
        if info_file.exists():
            try:
                info = _json.loads(info_file.read_text(encoding="utf-8"))
                info["name"] = suggested
                info_file.write_text(
                    _json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"info.json-Update failed für #{finding['recipe_id']}: {e}")

    db.audit_ai_finding_resolve(finding_id)
    logger.info(
        f"Apply #{finding_id} ({ftype}) für Rezept #{finding['recipe_id']}: "
        f"{old_path} → {new_path}"
    )
    return {"ok": True, "new_path": str(new_path), "type": ftype, "recipe_id": finding["recipe_id"]}


@router.post("/finding/{finding_id}/apply")
def apply_finding(finding_id: int) -> Dict[str, Any]:
    """Einzel-Apply via UI-Button. Delegiert an _apply_finding_internal."""
    return _apply_finding_internal(finding_id)


@router.post("/findings/apply-all")
def apply_all_findings(finding_type: str) -> Dict[str, Any]:
    """Bulk-Apply: alle offenen Findings eines Typs auf einmal anwenden.
    Wird vom UI-Bulk-Button 'Alle X anwenden' aufgerufen.

    Bei Fehler in einem Finding (z.B. Ziel-Folder existiert schon) wird
    nicht abgebrochen — die übrigen werden trotzdem versucht. Returnt
    {applied, failed[]} damit das UI eine Sammelmeldung zeigen kann.

    Akzeptiert nur die 3 known finding_types — alles andere → 400."""
    if finding_type not in ("category_mismatch", "name_mismatch", "folder_mismatch"):
        raise HTTPException(400, f"Unbekannter finding_type: {finding_type}")
    db = get_db()
    findings = db.audit_ai_findings_list(finding_type=finding_type, only_open=True)
    applied = 0
    failed = []
    for f in findings:
        try:
            _apply_finding_internal(f["id"])
            applied += 1
        except HTTPException as e:
            failed.append({
                "finding_id": f["id"],
                "recipe_name": f.get("recipe_name"),
                "status": e.status_code,
                "error": e.detail,
            })
        except Exception as e:
            failed.append({
                "finding_id": f["id"],
                "recipe_name": f.get("recipe_name"),
                "error": str(e)[:200],
            })
    logger.info(
        f"apply-all {finding_type}: {applied}/{len(findings)} angewendet, "
        f"{len(failed)} Fehler"
    )
    return {
        "ok": True,
        "applied": applied,
        "failed": failed,
        "total": len(findings),
        "finding_type": finding_type,
    }


class DeleteByPathPayload(BaseModel):
    folder_path: str


@router.get("/folder-preview")
def folder_preview(path: str) -> Dict[str, Any]:
    """Liefert den Inhalt eines FS-Konflikt-Folders (info.json, description,
    Media-File-Listing). Damit kann der User im UI Side-by-Side vergleichen:
    den in-DB Eintrag (über das normale Modal) UND den auf-FS Konflikt-Folder
    (über diesen Endpoint).

    Liest direkt vom FS, nicht aus DB (Folder ist ja nicht in DB drin —
    deswegen entstand der Konflikt). Path-Traversal-Schutz wie bei delete."""
    import json as _json
    from pathlib import Path
    cfg = get_config()
    recipe_root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    target = Path(path).resolve()
    try:
        target.relative_to(recipe_root)
    except ValueError:
        raise HTTPException(400, f"Pfad nicht im Recipe-Root: {target}")
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, f"Folder existiert nicht: {target}")

    # info.json
    info = {}
    info_file = target / "info.json"
    if info_file.exists():
        try:
            info = _json.loads(info_file.read_text(encoding="utf-8"))
        except Exception as e:
            info = {"_parse_error": str(e)}

    # description.txt — fallback auf größte .txt
    description = ""
    desc_file = target / "description.txt"
    if not desc_file.exists():
        txt_candidates = sorted(
            (f for f in target.iterdir() if f.is_file() and f.suffix.lower() == ".txt"),
            key=lambda f: f.stat().st_size, reverse=True,
        )
        if txt_candidates:
            desc_file = txt_candidates[0]
    if desc_file.exists():
        try:
            description = desc_file.read_text(encoding="utf-8")[:3000]
        except Exception:
            pass

    # Medien-Files mit Größe
    media = []
    for f in sorted(target.iterdir()):
        if not f.is_file():
            continue
        media.append({
            "name": f.name,
            "size": f.stat().st_size,
            "ext": f.suffix.lower().lstrip("."),
        })

    return {
        "ok": True,
        "folder_path": str(target),
        "folder_name": target.name,
        "info": info,
        "description": description,
        "media": media,
    }


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
