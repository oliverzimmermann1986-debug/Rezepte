"""Zentraler Admin-Bereich: Import, Versionen, PDF, Suche und Wartung."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..auth import SESSION_COOKIE, auth_disabled, require_admin, require_auth, session_user
from ..config_store import get_config
from ..core.pdf_processing import (
    analyze_pdf_bytes, backup_original_pdf, find_recipe_pdfs, process_pdf_path,
)
from ..core.safety import atomic_write_bytes
from ..db import get_db

session_router = APIRouter(prefix="/api/session", tags=["session"], dependencies=[Depends(require_auth)])
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _username(request: Request) -> str:
    if auth_disabled():
        return "local"
    return session_user(request.cookies.get(SESSION_COOKIE, "")) or "unknown"


@session_router.get("")
def current_session(request: Request) -> Dict[str, Any]:
    username = _username(request)
    if auth_disabled():
        return {"username": username, "role": "admin", "is_admin": True}
    user = get_db().user_get_by_name(username) or {"username": username, "role": "user"}
    return {
        "username": user.get("username") or username,
        "role": user.get("role") or "user",
        "is_admin": user.get("role") == "admin" and not bool(user.get("disabled")),
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
    limit: int = Field(50, ge=1, le=300)
    auto_rotate: bool = True
    remove_blank_pages: bool = True
    auto_crop: bool = True
    deskew_scans: bool = False
    ocr_scans: bool = False
    improve_contrast: bool = False
    ocr_language: str = Field("deu+eng", min_length=3, max_length=80)
    keep_original: bool = True


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


@router.post("/pdf/process")
def process_pdfs(payload: PdfBatchPayload, request: Request) -> Dict[str, Any]:
    db = get_db(); cfg = get_config(); actor = _username(request)
    targets = _pdf_targets(payload)
    run_id = db.maintenance_start("pdf_dry_run" if payload.dry_run else "pdf_process", actor)
    pdf_cfg = cfg.get("pdf", default={}) or {}
    backup_root = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data")) / "pdf-originals"
    files = []
    changed = errors = 0
    for path in targets:
        try:
            if payload.dry_run:
                report = analyze_pdf_bytes(path.read_bytes(), detect_skew=payload.deskew_scans)
            else:
                report = process_pdf_path(
                    path, backup_root=backup_root, keep_original=payload.keep_original,
                    auto_rotate=payload.auto_rotate,
                    use_tesseract_osd=bool(pdf_cfg.get("use_tesseract_osd", True)),
                    remove_blank_pages=payload.remove_blank_pages,
                    auto_crop=payload.auto_crop, deskew_scans=payload.deskew_scans,
                    ocr_scans=payload.ocr_scans,
                    improve_contrast=payload.improve_contrast,
                    ocr_language=payload.ocr_language,
                    min_text_chars=int(pdf_cfg.get("min_text_chars", 20) or 20),
                    text_dominance=float(pdf_cfg.get("text_dominance", 0.65) or 0.65),
                    osd_min_confidence=float(pdf_cfg.get("osd_min_confidence", 3.0) or 3.0),
                    max_osd_pages=int(pdf_cfg.get("max_osd_pages", 12) or 12),
                )
            data = report.as_dict(); data["path"] = str(path)
            files.append(data)
            if report.changed: changed += 1
            if not report.ok: errors += 1
        except Exception as exc:
            errors += 1; files.append({"path": str(path), "ok": False, "error": str(exc)})
    result = {"ok": errors == 0, "dry_run": payload.dry_run, "scanned": len(targets),
              "changed": changed, "errors": errors, "files": files}
    db.maintenance_finish(run_id, ok=result["ok"], result=result)
    return result


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
