"""Zentraler Admin-Bereich: Import, Versionen, PDF, Suche und Wartung."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..auth import SESSION_COOKIE, auth_disabled, require_admin, require_auth, session_user
from ..config_store import get_config
from ..core.analyzer import build_analyzer
from ..core.pdf_processing import (
    analyze_pdf_bytes, backup_original_pdf, find_recipe_pdfs, process_pdf_bytes, process_pdf_path,
)
from ..core.safety import atomic_write_bytes
from ..db import get_db
from ..recipes.pdf_recipe_extract import (
    apply_extracted_recipe_data, existing_hints, extract_pdf_text, extract_recipe_data,
)

logger = logging.getLogger(__name__)

session_router = APIRouter(prefix="/api/session", tags=["session"], dependencies=[Depends(require_auth)])
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# PDF-Bestandsläufe dürfen nicht an einem HTTP-/Reverse-Proxy-Timeout hängen.
# Ein einzelner Worker hält Speicher- und CPU-Verbrauch kontrollierbar; Fortschritt
# und Ergebnis werden in maintenance_runs persistiert.
_PDF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-admin")
_PDF_JOB_LOCK = threading.Lock()
_PDF_ACTIVE_RUN_ID: Optional[int] = None


def _username(request: Request) -> str:
    if auth_disabled():
        return "local"
    return session_user(request.cookies.get(SESSION_COOKIE, "")) or "unknown"


@session_router.get("")
def current_session(request: Request) -> Dict[str, Any]:
    """Sitzungsdaten für die Oberfläche.

    Alle aktiven, angemeldeten Benutzer besitzen denselben Vollzugriff.
    ``is_admin`` bleibt als Frontend-Kompatibilitätsfeld immer ``True``.
    """
    username = _username(request)
    user = None if auth_disabled() else get_db().user_get_by_name(username)
    return {
        "username": (user or {}).get("username") or username,
        "is_admin": True,
        "full_access": True,
    }


@router.get("/overview")
def overview() -> Dict[str, Any]:
    db = get_db()
    with db.conn() as c:
        counts = {
            "recipes": int(c.execute("SELECT COUNT(*) FROM recipes WHERE deleted_at IS NULL").fetchone()[0]),
            "pending": int(c.execute("SELECT COUNT(*) FROM pending WHERE status='pending'").fetchone()[0]),
            "failed_downloads": int(c.execute("SELECT COUNT(*) FROM download_failures").fetchone()[0]),
            "open_findings": int(c.execute("SELECT COUNT(*) FROM audit_ai_findings WHERE resolved=0").fetchone()[0]),
            "versions": int(c.execute("SELECT COUNT(*) FROM recipe_versions").fetchone()[0]),
            "trash": int(c.execute("SELECT COUNT(*) FROM recipes WHERE deleted_at IS NOT NULL").fetchone()[0]),
        }
    cfg = get_config()
    root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte"))
    pdf_count = sum(1 for _ in find_recipe_pdfs(root)) if root.exists() else 0
    return {
        "counts": counts,
        "pdf_count": pdf_count,
        "db_size_bytes": db.path.stat().st_size if db.path.exists() else 0,
        "recipe_root": str(root),
        "maintenance": db.maintenance_list(limit=5),
    }


@router.get("/import-center")
def import_center(limit: int = Query(100, ge=10, le=500)) -> Dict[str, Any]:
    db = get_db()
    pending = db.pending_list(status="pending", sort="oldest")[:limit]
    failed = db.download_failures_list(limit=limit)
    jobs = db.job_list(limit=min(limit, 100))
    history = db.history_list(limit=min(limit, 100))
    stages = {
        "needs_review": len(pending),
        "failed": len(failed),
        "running": sum(1 for j in jobs if j.get("status") == "running"),
        "completed_recent": sum(1 for j in jobs if j.get("status") == "ok"),
    }
    return {"stages": stages, "pending": pending, "failed": failed,
            "jobs": jobs, "history": history}


@router.get("/versions")
def versions(recipe_id: Optional[int] = None,
             limit: int = Query(200, ge=1, le=1000)) -> Dict[str, Any]:
    return {"items": get_db().recipe_versions_list(recipe_id=recipe_id, limit=limit)}


def _snapshot_diff(snapshot: Dict[str, Any], current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    current = current or {"recipe": {}, "ingredients": [], "steps": [], "tags": []}
    before_recipe = snapshot.get("recipe") or {}
    after_recipe = current.get("recipe") or {}
    fields = ("name", "type", "category", "description", "servings",
              "calories_per_serving", "protein_g", "carbs_g", "fat_g",
              "ingredients_status", "user_verified")
    changed_fields = []
    for field in fields:
        if before_recipe.get(field) != after_recipe.get(field):
            changed_fields.append({"field": field, "before": before_recipe.get(field),
                                   "current": after_recipe.get(field)})

    def ingredient_key(item: Dict[str, Any]) -> str:
        return str(item.get("canonical_name") or item.get("name") or "").strip().casefold()

    before_ingredients = {ingredient_key(i): i for i in snapshot.get("ingredients") or [] if ingredient_key(i)}
    after_ingredients = {ingredient_key(i): i for i in current.get("ingredients") or [] if ingredient_key(i)}
    before_tags = {str(t.get("name") or "").strip().casefold() for t in snapshot.get("tags") or []}
    after_tags = {str(t.get("name") or "").strip().casefold() for t in current.get("tags") or []}
    before_steps = [str(v.get("instruction") or "").strip() for v in snapshot.get("steps") or []]
    after_steps = [str(v.get("instruction") or "").strip() for v in current.get("steps") or []]
    return {
        "fields": changed_fields,
        "ingredients_added_since": sorted(set(after_ingredients) - set(before_ingredients)),
        "ingredients_removed_since": sorted(set(before_ingredients) - set(after_ingredients)),
        "tags_added_since": sorted(after_tags - before_tags),
        "tags_removed_since": sorted(before_tags - after_tags),
        "steps_changed": before_steps != after_steps,
        "before_counts": {"ingredients": len(before_ingredients), "steps": len(before_steps), "tags": len(before_tags)},
        "current_counts": {"ingredients": len(after_ingredients), "steps": len(after_steps), "tags": len(after_tags)},
    }


@router.get("/versions/{version_id}")
def version_detail(version_id: int) -> Dict[str, Any]:
    db = get_db()
    item = db.recipe_version_get(version_id)
    if not item:
        raise HTTPException(404, "Version nicht gefunden")
    item["diff"] = _snapshot_diff(item.get("snapshot") or {}, db.recipe_snapshot(int(item["recipe_id"])))
    return item


@router.post("/versions/{version_id}/restore")
def restore_version(version_id: int, request: Request) -> Dict[str, Any]:
    result = get_db().recipe_version_restore(version_id, restored_by=_username(request))
    if not result.get("ok"):
        raise HTTPException(409, result.get("error") or "Wiederherstellung fehlgeschlagen")
    return result


class SynonymPayload(BaseModel):
    term: str = Field(min_length=2, max_length=80)
    synonyms: List[str] = Field(default_factory=list, max_length=30)


@router.get("/search/synonyms")
def list_synonyms() -> Dict[str, Any]:
    return {"items": get_db().search_synonyms_list()}


@router.post("/search/synonyms")
def save_synonym(payload: SynonymPayload, request: Request) -> Dict[str, Any]:
    try:
        item_id = get_db().search_synonym_upsert(
            payload.term, payload.synonyms, updated_by=_username(request)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "id": item_id, "items": get_db().search_synonyms_list()}


@router.delete("/search/synonyms/{synonym_id}")
def delete_synonym(synonym_id: int) -> Dict[str, Any]:
    get_db().search_synonym_delete(synonym_id)
    return {"ok": True}


@router.post("/search/rebuild")
def rebuild_search(request: Request) -> Dict[str, Any]:
    db = get_db(); run_id = db.maintenance_start("rebuild_fts", _username(request))
    try:
        with db.conn() as c:
            c.execute("INSERT INTO recipes_fts(recipes_fts) VALUES('rebuild')")
            count = int(c.execute("SELECT COUNT(*) FROM recipes_fts").fetchone()[0])
        result = {"ok": True, "indexed": count}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    db.maintenance_finish(run_id, ok=result["ok"], result=result)
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return result


class PdfBatchPayload(BaseModel):
    recipe_id: Optional[int] = None
    process_all: bool = False
    dry_run: bool = True
    limit: int = Field(500, ge=1, le=2000)
    auto_rotate: bool = True
    remove_blank_pages: bool = True
    auto_crop: bool = True
    deskew_scans: bool = True
    ocr_scans: bool = True
    improve_contrast: bool = True
    sharpen_scans: bool = True
    scan_dpi: int = Field(300, ge=180, le=400)
    ocr_language: str = Field("deu+eng", min_length=3, max_length=80)
    keep_original: bool = True
    extract_recipe_data: bool = True
    overwrite_recipe_data: bool = False
    background: bool = False


def _pdf_targets(payload: PdfBatchPayload) -> List[Path]:
    db = get_db(); cfg = get_config()
    root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    if payload.recipe_id is not None:
        rec = db.recipe_get(payload.recipe_id)
        if not rec:
            raise HTTPException(404, "Rezept nicht gefunden")
        folder = Path(rec["folder_path"]).resolve()
        try: folder.relative_to(root)
        except ValueError: raise HTTPException(400, "Rezeptpfad liegt außerhalb des Rezeptstamms")
        return [p for p in sorted(folder.glob("*.pdf")) if p.is_file() and not p.is_symlink()]
    if not payload.process_all:
        raise HTTPException(400, "recipe_id oder process_all=true erforderlich")
    return list(find_recipe_pdfs(root))[:payload.limit]


def _tesseract_languages() -> List[str]:
    if not shutil.which("tesseract"):
        return []
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True,
            timeout=10, check=False,
        )
        lines = (proc.stdout or "").splitlines()[1:]
        return sorted({line.strip() for line in lines if line.strip()})
    except Exception:
        return []


def _pdf_preflight(*, require_backup: bool = False, require_recipe_write: bool = False,
                   ocr_language: str = "deu+eng") -> Dict[str, Any]:
    cfg = get_config()
    root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte"))
    data_dir = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data"))
    backup_root = data_dir / "pdf-originals"
    issues: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    try:
        import pymupdf  # noqa: F401
    except Exception as exc:
        issues.append({"code": "pymupdf_missing", "message": f"PyMuPDF fehlt: {exc}"})
    try:
        from PIL import Image  # noqa: F401
    except Exception as exc:
        issues.append({"code": "pillow_missing", "message": f"Pillow fehlt: {exc}"})

    tesseract = shutil.which("tesseract")
    languages = _tesseract_languages()
    if not tesseract:
        warnings.append({
            "code": "tesseract_missing",
            "message": "Tesseract fehlt. Drehen über Text-Layer funktioniert, Scan-OCR und OCR-Voting werden übersprungen.",
        })
    else:
        requested_languages = {part.strip() for part in str(ocr_language or "").split("+") if part.strip()}
        requested_languages.add("osd")
        for lang in sorted(requested_languages):
            if lang not in languages:
                warnings.append({
                    "code": f"tesseract_lang_{lang}_missing",
                    "message": f"Tesseract-Sprachpaket '{lang}' fehlt. Betroffene OCR-Funktionen werden übersprungen.",
                })

    if not root.exists():
        issues.append({"code": "recipe_root_missing", "message": f"Rezeptverzeichnis existiert nicht: {root}"})
    elif not root.is_dir():
        issues.append({"code": "recipe_root_not_dir", "message": f"Rezeptpfad ist kein Verzeichnis: {root}"})
    elif not os.access(root, os.R_OK | os.X_OK):
        issues.append({"code": "recipe_root_unreadable", "message": f"Rezeptverzeichnis ist nicht lesbar: {root}"})
    elif not os.access(root, os.W_OK):
        target = issues if require_recipe_write else warnings
        target.append({
            "code": "recipe_root_read_only",
            "message": f"Rezeptstamm ist nicht beschreibbar. Analyse funktioniert, Aufbereitung kann scheitern: {root}",
        })

    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        probe = backup_root / f".write-test-{os.getpid()}-{time.time_ns()}"
        probe.write_bytes(b"ok")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        target = issues if require_backup else warnings
        target.append({
            "code": "backup_unwritable",
            "message": f"Original-Backupverzeichnis ist nicht beschreibbar ({backup_root}): {exc}",
        })

    free_bytes = None
    try:
        free_bytes = shutil.disk_usage(root if root.exists() else data_dir).free
        if free_bytes < 512 * 1024 * 1024:
            warnings.append({
                "code": "low_disk_space",
                "message": f"Wenig freier Speicher: {free_bytes // (1024 * 1024)} MiB.",
            })
    except Exception:
        pass

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "recipe_root": str(root),
        "backup_root": str(backup_root),
        "tesseract": tesseract,
        "tesseract_languages": languages,
        "free_bytes": free_bytes,
    }


@router.get("/pdf/preflight")
def pdf_preflight() -> Dict[str, Any]:
    return _pdf_preflight()


def _pdf_result_base(payload: PdfBatchPayload, targets: List[Path]) -> Dict[str, Any]:
    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "status": "running",
        "scanned": len(targets),
        "processed": 0,
        "changed": 0,
        "errors": 0,
        "warnings": 0,
        "ingredients_found": 0,
        "steps_found": 0,
        "recipes_updated": 0,
        "recipe_data_skipped": 0,
        "current_file": None,
        "files": [],
    }


def _process_pdf_targets(
    payload: PdfBatchPayload,
    targets: List[Path],
    *,
    run_id: Optional[int] = None,
    actor: str = "system",
) -> Dict[str, Any]:
    db = get_db(); cfg = get_config()
    pdf_cfg = cfg.get("pdf", default={}) or {}
    backup_root = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data")) / "pdf-originals"
    result = _pdf_result_base(payload, targets)
    analyzer = None
    existing_tags: List[str] = []
    existing_canonical: List[str] = []
    if payload.extract_recipe_data:
        existing_tags, existing_canonical = existing_hints(db)
        try:
            analyzer = build_analyzer(cfg.get("ai", default={}) or {})
        except Exception as exc:
            logger.warning("PDF-Rezeptauswertung läuft ohne KI, lokaler Parser aktiv: %s", exc)
            result["warnings"] += 1
            result.setdefault("general_warnings", []).append(
                "OpenAI nicht verfügbar; klassische Zutatenlisten werden lokal gelesen, Schritte und Portionen eventuell nicht."
            )

    for index, path in enumerate(targets, start=1):
        result["current_file"] = str(path)
        if run_id is not None:
            db.maintenance_progress(run_id, result)
        try:
            kwargs = dict(
                auto_rotate=payload.auto_rotate,
                use_tesseract_osd=bool(pdf_cfg.get("use_tesseract_osd", True)),
                use_ocr_vote=bool(pdf_cfg.get("use_ocr_vote", True)),
                remove_blank_pages=payload.remove_blank_pages,
                auto_crop=payload.auto_crop,
                deskew_scans=payload.deskew_scans,
                ocr_scans=payload.ocr_scans,
                improve_contrast=payload.improve_contrast,
                sharpen_scans=payload.sharpen_scans,
                scan_dpi=payload.scan_dpi,
                ocr_language=payload.ocr_language,
                min_text_chars=int(pdf_cfg.get("min_text_chars", 20) or 20),
                text_dominance=float(pdf_cfg.get("text_dominance", 0.60) or 0.60),
                osd_min_confidence=float(pdf_cfg.get("osd_min_confidence", 1.0) or 1.0),
                max_osd_pages=int(pdf_cfg.get("max_osd_pages", 100) or 100),
            )
            processed_pdf: bytes | Path
            if payload.dry_run:
                preview_bytes, report = process_pdf_bytes(path.read_bytes(), **kwargs)
                processed_pdf = preview_bytes
            else:
                report = process_pdf_path(
                    path, backup_root=backup_root,
                    keep_original=payload.keep_original, **kwargs,
                )
                processed_pdf = path
            file_result = report.as_dict(); file_result["path"] = str(path)

            if payload.extract_recipe_data:
                pdf_text = extract_pdf_text(processed_pdf)
                extracted = extract_recipe_data(
                    pdf_text, analyzer=analyzer, existing_tags=existing_tags,
                    existing_canonical=existing_canonical,
                )
                file_result.update({
                    "recipe_text_chars": len(pdf_text),
                    "ingredients_found": len(extracted.ingredients),
                    "steps_found": len(extracted.steps),
                    "servings_found": extracted.servings,
                    "recipe_extraction_method": extracted.method,
                    "ingredient_preview": [
                        {"name": item.get("name"), "amount": item.get("amount"), "unit": item.get("unit")}
                        for item in extracted.ingredients[:12]
                    ],
                    "recipe_extraction_warnings": extracted.warnings,
                })
                result["ingredients_found"] += len(extracted.ingredients)
                result["steps_found"] += len(extracted.steps)

                recipe = db.recipe_get_by_folder(str(path.parent))
                if recipe and not payload.dry_run:
                    applied = apply_extracted_recipe_data(
                        db, int(recipe["id"]), extracted, actor=actor,
                        overwrite=payload.overwrite_recipe_data, create_version=True,
                        update_description=True,
                    )
                    file_result["recipe_id"] = int(recipe["id"])
                    file_result["recipe_update"] = applied
                    if applied.get("changed"):
                        result["recipes_updated"] += 1
                    elif extracted.ingredients or extracted.steps:
                        result["recipe_data_skipped"] += 1
                elif not recipe:
                    file_result["recipe_update_warning"] = "PDF-Ordner ist noch keinem Rezeptdatensatz zugeordnet"

            result["files"].append(file_result)
            if report.changed:
                result["changed"] += 1
            if not report.ok:
                result["errors"] += 1
            result["warnings"] += len(report.warnings or []) + len(file_result.get("recipe_extraction_warnings") or [])
        except Exception as exc:
            logger.exception("PDF-Datei konnte nicht verarbeitet werden: %s", path)
            result["errors"] += 1
            result["files"].append({
                "path": str(path), "ok": False,
                "reason": "unhandled_error", "error": str(exc),
            })
        result["processed"] = index
        # Die Detailhistorie kann bei großen Beständen sehr groß werden. Für den
        # Live-Fortschritt reichen die letzten 100 Dateien; im finalen Ergebnis
        # bleibt die Liste ebenfalls begrenzt, damit SQLite/UI stabil bleiben.
        if len(result["files"]) > 100:
            result["files"] = result["files"][-100:]
        if run_id is not None:
            db.maintenance_progress(run_id, result)

    result["current_file"] = None
    result["status"] = "ok" if result["errors"] == 0 else "error"
    result["ok"] = result["errors"] == 0
    return result


def _run_pdf_background(payload_data: Dict[str, Any], targets: List[Path], run_id: int, actor: str) -> None:
    global _PDF_ACTIVE_RUN_ID
    db = get_db()
    try:
        payload = PdfBatchPayload.model_validate(payload_data)
        result = _process_pdf_targets(payload, targets, run_id=run_id, actor=actor)
        db.maintenance_finish(run_id, ok=result["ok"], result=result)
    except Exception as exc:
        logger.exception("PDF-Hintergrundlauf #%s abgebrochen", run_id)
        result = {
            "ok": False, "status": "error", "processed": 0,
            "scanned": len(targets), "changed": 0, "errors": 1,
            "current_file": None, "files": [], "error": str(exc),
        }
        db.maintenance_finish(run_id, ok=False, result=result)
    finally:
        with _PDF_JOB_LOCK:
            if _PDF_ACTIVE_RUN_ID == run_id:
                _PDF_ACTIVE_RUN_ID = None


@router.post("/pdf/process")
def process_pdfs(payload: PdfBatchPayload, request: Request, response: Response) -> Dict[str, Any]:
    global _PDF_ACTIVE_RUN_ID
    db = get_db(); actor = _username(request)
    preflight = _pdf_preflight(
        require_backup=bool(not payload.dry_run and payload.keep_original),
        require_recipe_write=bool(not payload.dry_run),
        ocr_language=payload.ocr_language,
    )
    if not preflight["ok"]:
        message = "; ".join(item["message"] for item in preflight["issues"])
        raise HTTPException(409, message or "PDF-Systemprüfung fehlgeschlagen")

    targets = _pdf_targets(payload)
    if not targets:
        raise HTTPException(404, "Keine PDF-Dateien im gewählten Umfang gefunden")

    if not payload.background:
        run_id = db.maintenance_start("pdf_dry_run" if payload.dry_run else "pdf_process", actor)
        result = _process_pdf_targets(payload, targets, actor=actor)
        result["preflight"] = preflight
        db.maintenance_finish(run_id, ok=result["ok"], result=result)
        return result

    with _PDF_JOB_LOCK:
        if _PDF_ACTIVE_RUN_ID is not None:
            active = db.maintenance_get(_PDF_ACTIVE_RUN_ID)
            if active and active.get("status") == "running":
                raise HTTPException(409, f"PDF-Lauf #{_PDF_ACTIVE_RUN_ID} läuft bereits")
            _PDF_ACTIVE_RUN_ID = None
        run_id = db.maintenance_start("pdf_dry_run" if payload.dry_run else "pdf_process", actor)
        _PDF_ACTIVE_RUN_ID = run_id

    initial = _pdf_result_base(payload, targets)
    initial["preflight"] = preflight
    db.maintenance_progress(run_id, initial)
    _PDF_EXECUTOR.submit(_run_pdf_background, payload.model_dump(), targets, run_id, actor)
    response.status_code = 202
    return {
        "ok": True, "accepted": True, "run_id": run_id,
        "status": "running", "scanned": len(targets), "result": initial,
    }


@router.get("/pdf/jobs/active")
def active_pdf_job() -> Dict[str, Any]:
    with _PDF_JOB_LOCK:
        run_id = _PDF_ACTIVE_RUN_ID
    if run_id is None:
        return {"active": False}
    item = get_db().maintenance_get(run_id)
    if not item or item.get("status") != "running":
        return {"active": False}
    return {"active": True, "job": item}


@router.get("/pdf/jobs/{run_id}")
def pdf_job_status(run_id: int) -> Dict[str, Any]:
    item = get_db().maintenance_get(run_id)
    if not item or item.get("kind") not in {"pdf_dry_run", "pdf_process"}:
        raise HTTPException(404, "PDF-Lauf nicht gefunden")
    return item


def _recipe_pdf(recipe_id: int) -> tuple[Dict[str, Any], Path]:
    db = get_db(); cfg = get_config()
    rec = db.recipe_get(recipe_id)
    if not rec:
        raise HTTPException(404, "Rezept nicht gefunden")
    root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    folder = Path(rec.get("folder_path") or "").resolve()
    try:
        folder.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Rezeptpfad liegt außerhalb des Rezeptstamms")
    pdfs = [p for p in sorted(folder.glob("*.pdf")) if p.is_file() and not p.is_symlink()]
    if not pdfs:
        raise HTTPException(404, "Kein PDF vorhanden")
    return rec, pdfs[0]


@router.get("/pdf/{recipe_id}/pages")
def pdf_pages(recipe_id: int) -> Dict[str, Any]:
    import pymupdf
    _rec, path = _recipe_pdf(recipe_id)
    report = analyze_pdf_bytes(path.read_bytes(), detect_skew=True, max_pages=150)
    if not report.ok:
        raise HTTPException(409, report.error or report.reason or "PDF nicht lesbar")
    doc = pymupdf.open(str(path))
    try:
        rotations = [int(doc[i].rotation or 0) for i in range(len(doc))]
    finally:
        doc.close()
    return {
        "recipe_id": recipe_id, "filename": path.name,
        "pages": [
            {**page.__dict__, "rotation": rotations[page.page - 1],
             "rotation_delta": 0, "deleted": False}
            for page in report.pages
        ],
    }


@router.get("/pdf/{recipe_id}/pages/{page_no}/preview")
def pdf_page_preview(recipe_id: int, page_no: int,
                     width: int = Query(420, ge=180, le=1200)) -> Response:
    import pymupdf
    _rec, path = _recipe_pdf(recipe_id)
    doc = pymupdf.open(str(path))
    try:
        if page_no < 1 or page_no > len(doc):
            raise HTTPException(404, "Seite nicht gefunden")
        page = doc[page_no - 1]
        scale = min(3.0, max(0.35, width / max(1.0, page.rect.width)))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB,
                              alpha=False, annots=False)
        return Response(pix.tobytes("jpeg", jpg_quality=82), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=60"})
    finally:
        doc.close()


class PdfPageEditPayload(BaseModel):
    order: List[int] = Field(default_factory=list, max_length=150)
    rotations: Dict[str, int] = Field(default_factory=dict)
    keep_original: bool = True


@router.post("/pdf/{recipe_id}/pages/apply")
def apply_pdf_page_edits(recipe_id: int, payload: PdfPageEditPayload,
                         request: Request) -> Dict[str, Any]:
    import pymupdf
    rec, path = _recipe_pdf(recipe_id)
    original = path.read_bytes()
    doc = pymupdf.open(stream=original, filetype="pdf")
    pages_before = len(doc)
    try:
        if getattr(doc, "needs_pass", False):
            raise HTTPException(409, "Verschlüsselte PDFs werden nicht verändert")
        try:
            if int(doc.get_sigflags() or 0) > 0:
                raise HTTPException(409, "Digital signierte PDFs werden nicht verändert")
        except HTTPException:
            raise
        except Exception:
            pass
        order = payload.order or list(range(1, len(doc) + 1))
        if not order or len(order) > len(doc) or len(set(order)) != len(order):
            raise HTTPException(400, "Ungültige Seitenreihenfolge")
        if any(page_no < 1 or page_no > len(doc) for page_no in order):
            raise HTTPException(400, "Unbekannte Seitennummer")

        rebuilt = pymupdf.open()
        try:
            for original_page_no in order:
                rebuilt.insert_pdf(doc, from_page=original_page_no - 1, to_page=original_page_no - 1)
                raw_delta = payload.rotations.get(str(original_page_no), 0)
                try:
                    delta = int(raw_delta)
                except (TypeError, ValueError):
                    delta = 0
                if delta % 90:
                    raise HTTPException(400, "Drehungen sind nur in 90°-Schritten erlaubt")
                page = rebuilt[-1]
                page.set_rotation((int(page.rotation or 0) + delta) % 360)
            output = rebuilt.tobytes(garbage=4, deflate=True, clean=False)
            check = pymupdf.open(stream=output, filetype="pdf")
            try:
                if len(check) != len(order):
                    raise HTTPException(500, "PDF-Seitenprüfung fehlgeschlagen")
            finally:
                check.close()
        finally:
            rebuilt.close()
    finally:
        doc.close()

    cfg = get_config()
    backup_root = Path(cfg.get("paths", "data_dir", default=str(get_db().path.parent))) / "pdf-originals"
    backup = str(backup_original_pdf(path, backup_root, original)) if payload.keep_original else None
    atomic_write_bytes(path, output)

    # PDF-generierte Vorschau nach Seitenänderung erneuern, ohne fremde
    # benutzerdefinierte Thumbnails anzutasten.
    thumb_name = rec.get("thumb_filename")
    if not thumb_name or thumb_name in {"thumb.jpg", "pdf-page1.jpg"}:
        try:
            check = pymupdf.open(stream=output, filetype="pdf")
            try:
                pix = check[0].get_pixmap(dpi=140, colorspace=pymupdf.csRGB, alpha=False, annots=False)
                thumb_path = path.parent / "thumb.jpg"
                atomic_write_bytes(thumb_path, pix.tobytes("jpeg", jpg_quality=86))
                with get_db().conn() as c:
                    c.execute("UPDATE recipes SET thumb_filename='thumb.jpg' WHERE id=?", (recipe_id,))
            finally:
                check.close()
        except Exception:
            # Die PDF-Änderung ist wichtiger als der abgeleitete Cache.
            pass

    run_id = get_db().maintenance_start("pdf_page_edit", _username(request))
    result = {"ok": True, "recipe_id": recipe_id, "filename": path.name,
              "pages_before": pages_before, "pages_after": len(order), "backup": backup}
    get_db().maintenance_finish(run_id, ok=True, result=result)
    return result


@router.get("/maintenance/runs")
def maintenance_runs(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    return {"items": get_db().maintenance_list(limit=limit)}


def _media_scan() -> Dict[str, Any]:
    db = get_db(); cfg = get_config()
    root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte")).resolve()
    recipes = db.recipe_list(include_deleted=True, limit=100000)
    missing_folders = []; missing_media = []; unsafe_paths = []
    indexed_folders = set()
    for rec in recipes:
        folder = Path(rec.get("folder_path") or "")
        try:
            resolved = folder.resolve(); resolved.relative_to(root); indexed_folders.add(str(resolved))
        except Exception:
            unsafe_paths.append({"id": rec.get("id"), "name": rec.get("name"), "path": str(folder)})
            continue
        if not resolved.is_dir():
            missing_folders.append({"id": rec.get("id"), "name": rec.get("name"), "path": str(resolved)})
            continue
        known = [rec.get("thumb_filename"), rec.get("video_filename")]
        if not any(name and (resolved / name).is_file() for name in known) and not list(resolved.glob("*.pdf")):
            missing_media.append({"id": rec.get("id"), "name": rec.get("name"), "path": str(resolved)})
    orphan_folders = []
    if root.is_dir():
        for info in root.rglob("info.json"):
            folder = str(info.parent.resolve())
            if folder not in indexed_folders:
                orphan_folders.append(folder)
    return {"ok": not unsafe_paths, "missing_folders": missing_folders,
            "missing_media": missing_media, "unsafe_paths": unsafe_paths,
            "orphan_folders": orphan_folders[:500], "recipes_checked": len(recipes)}


@router.post("/maintenance/run/{kind}")
def run_maintenance(kind: str, request: Request) -> Dict[str, Any]:
    allowed = {"integrity", "backup_verify", "media_scan", "cleanup_temp", "rebuild_fts", "vacuum"}
    if kind not in allowed:
        raise HTTPException(400, "Unbekannte Wartungsaktion")
    db = get_db(); cfg = get_config(); run_id = db.maintenance_start(kind, _username(request))
    try:
        if kind == "integrity":
            with db.conn() as c:
                integrity = [r[0] for r in c.execute("PRAGMA integrity_check").fetchall()]
                fk = [dict(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]
            result = {"ok": integrity == ["ok"] and not fk, "integrity": integrity, "foreign_keys": fk}
        elif kind == "backup_verify":
            backup_dir = Path(cfg.get("paths", "data_dir", default=str(db.path.parent))) / "backups"
            target = backup_dir / f"admin-verify-{time.strftime('%Y%m%d-%H%M%S')}.db"
            result = db.backup_to(target, verify=True)
            # Admin-Prüfbackups sind kurzlebige Testartefakte. Die fünf neuesten
            # bleiben für Diagnosezwecke erhalten, ältere werden entfernt.
            verify_backups = sorted(
                backup_dir.glob("admin-verify-*.db"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            removed = 0
            for old_backup in verify_backups[5:]:
                try:
                    old_backup.unlink()
                    removed += 1
                except OSError:
                    continue
            result["retained"] = min(5, len(verify_backups))
            result["old_verify_backups_removed"] = removed
        elif kind == "media_scan":
            result = _media_scan()
        elif kind == "cleanup_temp":
            temp_root = Path(cfg.get("paths", "temp_dir", default="/opt/scrapper/temp")).resolve()
            removed = 0; bytes_removed = 0; cutoff = time.time() - 7 * 86400
            if temp_root.is_dir():
                for child in temp_root.iterdir():
                    try:
                        if child.name == "pending" or child.stat().st_mtime >= cutoff:
                            continue
                        size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file()) if child.is_dir() else child.stat().st_size
                        if child.is_dir(): shutil.rmtree(child)
                        else: child.unlink()
                        removed += 1; bytes_removed += size
                    except Exception:
                        continue
            result = {"ok": True, "removed": removed, "bytes_removed": bytes_removed}
        elif kind == "rebuild_fts":
            with db.conn() as c:
                c.execute("INSERT INTO recipes_fts(recipes_fts) VALUES('rebuild')")
                count = int(c.execute("SELECT COUNT(*) FROM recipes_fts").fetchone()[0])
            result = {"ok": True, "indexed": count}
        else:
            result = db.vacuum()
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    db.maintenance_finish(run_id, ok=bool(result.get("ok")), result=result)
    if not result.get("ok") and kind not in {"media_scan", "integrity"}:
        raise HTTPException(500, result.get("error") or "Wartung fehlgeschlagen")
    return result
