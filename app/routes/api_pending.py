"""API für Pending-Items: Auflisten, Vorschau, Auflösen."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

from ..auth import require_admin, require_auth
from ..config_store import get_config
from ..db import get_db
from ..jobs.scraper import get_scraper_job
from ..jobs.locks import file_lock_or_none
from ..core.pdf_processing import (
    PdfResourceLimitError,
    PdfValidationError,
    validate_pdf_resource_budget,
)
from ..recipes.image_cache import assert_safe_image_dimensions

router = APIRouter(prefix="/api/pending", tags=["pending"], dependencies=[Depends(require_auth)])


def _is_under_temp(path_str: str) -> bool:
    """Defense-in-depth: nur Pfade unter temp_dir erlauben."""
    if not path_str:
        return False
    try:
        p = Path(path_str).resolve()
        temp_root = Path(
            get_config().get("paths", "temp_dir", default="/opt/scrapper/temp")
        ).resolve()
        p.relative_to(temp_root)
        return True
    except (ValueError, OSError):
        return False


@router.get("", dependencies=[Depends(require_admin)])
def list_pending(status: str = "pending", sort: str = "newest") -> List[Dict[str, Any]]:
    return get_db().pending_list(status=status, sort=sort)


class ImportUrlBody(BaseModel):
    url: str
    type: str = "recipe"          # 'recipe' | 'wedding'


_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
_UPLOAD_LIMIT = 25 * 1024 * 1024
_UPLOAD_FREE_RESERVE = 512 * 1024 * 1024
_UPLOAD_MULTIPART_OVERHEAD = 2 * 1024 * 1024
_UPLOAD_PDF_MAX_PAGES = 100
_RECENT_UPLOAD_TTL_SECONDS = 10 * 60
_recent_upload_lock = threading.Lock()
_recent_uploads: Dict[str, tuple[float, str]] = {}
_RESERVED_FILENAME_CHARS = frozenset('<>:"/\\|?*')


def _safe_upload_filename(raw_filename: Optional[str]) -> str:
    """Erzeugt einen plattformneutral sicheren, stabilen Upload-Dateinamen."""
    normalized = unicodedata.normalize("NFKC", str(raw_filename or "upload"))
    normalized = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = "".join(
        "_"
        if char in _RESERVED_FILENAME_CHARS
        or unicodedata.category(char).startswith("C")
        else char
        for char in normalized
    )
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        normalized = "upload"
    suffix = Path(normalized).suffix.lower()
    stem = normalized[:-len(suffix)] if suffix else normalized
    stem = stem.strip(" .") or "upload"
    # Extension und etwas Platz für nachgelagerte Suffixe bleiben erhalten.
    stem = stem[: max(1, 160 - len(suffix))]
    return f"{stem}{suffix}"


def _upload_filename_identity(filename: str, detected_extension: str) -> str:
    """Kanonischer Name für Idempotenz, unabhängig von Großschreibung/JPEG-Alias."""
    safe_name = _safe_upload_filename(filename)
    suffix = Path(safe_name).suffix
    stem = safe_name[:-len(suffix)] if suffix else safe_name
    return f"{unicodedata.normalize('NFKC', stem).casefold()}{detected_extension}"


def _detected_upload_type(data: bytes) -> Optional[str]:
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    return None


def _validate_upload_payload(data: bytes, detected: str) -> None:
    if detected == ".pdf":
        try:
            validate_pdf_resource_budget(
                data,
                max_bytes=_UPLOAD_LIMIT,
                max_pages=_UPLOAD_PDF_MAX_PAGES,
            )
        except PdfResourceLimitError as exc:
            raise HTTPException(413, str(exc)) from exc
        except PdfValidationError as exc:
            raise HTTPException(415, str(exc)) from exc
        return

    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(data)) as image:
            expected_format = "JPEG" if detected == ".jpg" else "PNG"
            if str(image.format or "").upper() != expected_format:
                raise HTTPException(415, "Bildformat und Dateiinhalt stimmen nicht überein")
            assert_safe_image_dimensions(image)
            image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(415, f"Bilddatei ist nicht sicher lesbar: {exc}") from exc


def _normalized_request_id(form_value: Optional[str], header_value: Optional[str]) -> Optional[str]:
    form_id = (form_value or "").strip()
    header_id = (header_value or "").strip()
    if form_id and header_id and form_id != header_id:
        raise HTTPException(400, "client_request_id und Idempotency-Key widersprechen sich")
    value = form_id or header_id
    if not value:
        return None
    if len(value) > 200:
        raise HTTPException(400, "client_request_id ist zu lang")
    return value


def _stored_import_result(db: Any, synth_url: str) -> Optional[Dict[str, Any]]:
    pending = db.pending_get(synth_url)
    if pending:
        suggestion = pending.get("ai_suggestion") or {}
        status = pending.get("status") or "pending"
        return {
            "ok": True,
            "status": status if status == "pending" else "duplicate",
            "url": synth_url,
            "name": suggestion.get("name") or "Unbekannt",
            "duplicate": True,
            "idempotent_replay": True,
            "message": "Dieser Dateiimport wurde bereits übernommen.",
        }
    history = db.history_get(synth_url)
    if history:
        return {
            "ok": True,
            "status": "duplicate",
            "url": synth_url,
            "name": history.get("name") or "Unbekannt",
            "target": history.get("target_dir"),
            "duplicate": True,
            "idempotent_replay": True,
            "message": "Dieser Dateiimport wurde bereits verarbeitet.",
        }
    return None


def _request_urls(db: Any, prefix: str) -> List[str]:
    with db.conn() as connection:
        rows = connection.execute(
            "SELECT url FROM pending WHERE url LIKE ? "
            "UNION SELECT url FROM history WHERE url LIKE ?",
            (f"{prefix}%", f"{prefix}%"),
        ).fetchall()
    return [str(row["url"]) for row in rows]


def _recent_content_url(key: str) -> Optional[str]:
    now = time.monotonic()
    with _recent_upload_lock:
        expired = [
            item_key
            for item_key, (created_at, _url) in _recent_uploads.items()
            if now - created_at > _RECENT_UPLOAD_TTL_SECONDS
        ]
        for item_key in expired:
            _recent_uploads.pop(item_key, None)
        item = _recent_uploads.get(key)
        return item[1] if item else None


def _remember_content_url(key: str, synth_url: str) -> None:
    with _recent_upload_lock:
        _recent_uploads[key] = (time.monotonic(), synth_url)


def _assert_upload_capacity(payload_size: int) -> None:
    """Reserviert Platz für OCR-Raster, PDF-Kopien und atomare Zieldateien."""
    cfg = get_config()
    temp_root = Path(cfg.get("paths", "temp_dir", default="/opt/scrapper/temp"))
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(temp_root).free
    except OSError as exc:
        logger.error("Freien Temp-Speicher nicht ermittelbar: %s", exc)
        raise HTTPException(503, "Temporärer Upload-Speicher ist nicht verfügbar") from exc
    reserve_mb = int(cfg.get("paths", "upload_free_reserve_mb", default=512) or 512)
    required = payload_size + max(_UPLOAD_FREE_RESERVE, reserve_mb * 1024 * 1024)
    if free_bytes < required:
        raise HTTPException(
            507,
            "Zu wenig freier Speicher für die sichere Verarbeitung. "
            "Bitte Speicher freigeben und den Upload erneut versuchen.",
        )


@router.post("/import-url")
def import_url(body: ImportUrlBody) -> Dict[str, Any]:
    """Speichert einen einzelnen Social-Post als externe Rezeptquelle.

    Medien werden bewusst nicht heruntergeladen. Der Link landet zur manuellen
    Pflege bei den unvollständigen Importen und wird später extern geöffnet.
    """
    from ..core.email_processor import normalize_content_url

    raw_url = (body.url or "").strip()
    if not raw_url:
        raise HTTPException(400, "URL fehlt")
    if body.type not in ("recipe", "wedding"):
        raise HTTPException(400, "type muss 'recipe' oder 'wedding' sein")
    url = normalize_content_url(raw_url)
    if not url:
        raise HTTPException(
            400,
            "Das ist kein gültiger einzelner TikTok-/Instagram-Post. "
            "Bitte den Link zu einem konkreten Post einfügen.",
        )

    db = get_db()
    if db.history_has(url):
        return {"ok": True, "status": "duplicate", "url": url,
                "message": "URL wurde bereits importiert"}

    with file_lock_or_none("scraper") as lock:
        if lock is None:
            raise HTTPException(409, "Ein Import läuft bereits. Bitte gleich erneut versuchen.")
        result = get_scraper_job().process_url({"url": url, "type": body.type})
    return {"ok": result.get("status") in ("already_processed", "pending"), **result}


@router.post("/import-file")
async def import_file(
    request: Request,
    file: UploadFile = File(...),
    type: Optional[str] = Query(None),
    form_type: Optional[str] = Form(None, alias="type"),
    client_request_id: Optional[str] = Form(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    force: bool = Query(False),
) -> Dict[str, Any]:
    """Importiert ein Foto oder PDF über dieselbe Analyse wie Mail-Anhänge."""
    if type and form_type and type != form_type:
        raise HTTPException(400, "Query-type und Formular-type widersprechen sich")
    import_type = type or form_type or "recipe"
    if import_type not in ("recipe", "wedding"):
        raise HTTPException(400, "type muss 'recipe' oder 'wedding' sein")
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError:
        content_length = 0
    if content_length > _UPLOAD_LIMIT + _UPLOAD_MULTIPART_OVERHEAD:
        raise HTTPException(413, "Der Upload ist größer als 25 MB")
    filename = _safe_upload_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if ext not in _UPLOAD_EXTENSIONS:
        raise HTTPException(415, "Erlaubt sind PDF, JPG, JPEG und PNG")
    data = await file.read(_UPLOAD_LIMIT + 1)
    if not data:
        raise HTTPException(400, "Die Datei ist leer")
    if len(data) > _UPLOAD_LIMIT:
        raise HTTPException(413, "Die Datei ist größer als 25 MB")
    detected = _detected_upload_type(data)
    if not detected:
        raise HTTPException(415, "Dateiinhalt ist kein gültiges PDF-, JPEG- oder PNG-Format")
    expected = ".jpg" if ext in {".jpg", ".jpeg"} else ext
    if detected != expected:
        raise HTTPException(415, "Dateiendung und tatsächliches Dateiformat stimmen nicht überein")
    _validate_upload_payload(data, detected)
    _assert_upload_capacity(len(data))

    content_hash = hashlib.sha256(data).hexdigest()
    filename_identity = _upload_filename_identity(filename, detected)
    semantic_hash = hashlib.sha256(
        f"{import_type}\0{filename_identity}\0{content_hash}".encode("utf-8")
    ).hexdigest()
    stable_request_id = _normalized_request_id(client_request_id, idempotency_key)
    db = get_db()
    if stable_request_id:
        request_hash = hashlib.sha256(stable_request_id.encode("utf-8")).hexdigest()[:32]
        request_prefix = f"manual-upload://request/{request_hash}/"
        synth_url = f"{request_prefix}{semantic_hash}{detected}"
        known_urls = _request_urls(db, request_prefix)
        if known_urls and synth_url not in known_urls:
            raise HTTPException(
                409,
                "Diese client_request_id wurde bereits für eine andere Anfrage verwendet",
            )
        replay = _stored_import_result(db, synth_url)
        if replay:
            replay["content_sha256"] = content_hash
            return replay
    else:
        retry_key = semantic_hash
        recent_url = None if force else _recent_content_url(retry_key)
        if recent_url:
            replay = _stored_import_result(db, recent_url)
            if replay:
                replay["content_sha256"] = content_hash
                replay["message"] += " Für einen bewussten Neuimport bitte force=true verwenden."
                return replay
        synth_url = f"manual-upload://content/{content_hash}/{uuid.uuid4().hex}{detected}"

    attachment = {
        "filename": filename,
        "ext": ext,
        "type": import_type,
        "data": data,
        "subject": Path(filename).stem.replace("_", " "),
        "body_excerpt": "",
        "default_category": "Allgemein",
    }
    with file_lock_or_none("scraper") as lock:
        if lock is None:
            raise HTTPException(409, "Ein Import läuft bereits. Bitte gleich erneut versuchen.")
        # Die schnelle Prüfung oben vermeidet unnötiges Warten. Nach Erwerb
        # des prozessübergreifenden Scraper-Locks muss sie wiederholt werden:
        # Zwei identische Requests können beide vor dem ersten DB-Write durch
        # die Vorprüfung gelangen und danach nacheinander in diesen Block.
        if stable_request_id:
            known_urls = _request_urls(db, request_prefix)
            if known_urls and synth_url not in known_urls:
                raise HTTPException(
                    409,
                    "Diese client_request_id wurde bereits für eine andere Anfrage verwendet",
                )
            replay = _stored_import_result(db, synth_url)
            if replay:
                replay["content_sha256"] = content_hash
                return replay
        elif not force:
            recent_url = _recent_content_url(retry_key)
            if recent_url:
                replay = _stored_import_result(db, recent_url)
                if replay:
                    replay["content_sha256"] = content_hash
                    replay["message"] += (
                        " Für einen bewussten Neuimport bitte force=true verwenden."
                    )
                    return replay
        # PDF-Parsing, OCR und Vision sind synchron/blockierend. Im Threadpool
        # bleibt der FastAPI-Event-Loop für Login, Status und andere Nutzer frei.
        result = await run_in_threadpool(
            get_scraper_job().process_attachment,
            attachment,
            synth_url,
        )
        # Noch unter demselben Lock publizieren, damit der nächste Request die
        # abgeschlossene Verarbeitung zwingend als Replay erkennt.
        if not stable_request_id:
            _remember_content_url(retry_key, synth_url)
    result.setdefault("url", synth_url)
    if result.get("status") == "error":
        raise HTTPException(422, result.get("error") or "Datei konnte nicht verarbeitet werden")
    result["ok"] = result.get("status") in {"auto", "pending"}
    result["content_sha256"] = content_hash
    result["idempotent_replay"] = False
    result["message"] = (
        "Datei wurde importiert."
        if result.get("status") == "auto"
        else "Datei wurde übernommen und wartet auf manuelle Prüfung."
    )
    return result


class BulkSkipBody(BaseModel):
    urls: List[str]


@router.post("/bulk-skip", dependencies=[Depends(require_admin)])
def bulk_skip(body: BulkSkipBody) -> Dict[str, Any]:
    """Mehrere Pending-Items in einem Rutsch überspringen.
    Schreibt sie in die History als '(skipped)', löscht das stash-Video,
    und markiert sie als status='skipped'.
    """
    db = get_db()
    job = get_scraper_job()
    skipped = 0
    errors = []
    for url in body.urls:
        try:
            r = job.resolve_pending(url, {"action": "skip"})
            if r.get("ok"):
                skipped += 1
            else:
                errors.append({"url": url, "error": r.get("error", "unknown")})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})
    return {"ok": True, "skipped": skipped, "errors": errors,
            "total_requested": len(body.urls)}


# Video-Frame-Vorschauen bleiben entfernt. Für manuell hochgeladene Rezepte
# liefern wir dagegen die Originaldatei authentifiziert aus, damit JPG/PNG/PDF
# vor der Freigabe tatsächlich kontrolliert werden können.


@router.get("/file", dependencies=[Depends(require_admin)])
def pending_file(url: str) -> FileResponse:
    entry = get_db().pending_get(url)
    if not entry:
        raise HTTPException(404, "Nicht gefunden")
    path_str = entry.get("video_path")
    if not path_str or not _is_under_temp(path_str):
        raise HTTPException(404, "Importdatei nicht verfügbar")
    path = Path(path_str)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Importdatei nicht verfügbar")
    try:
        if path.stat().st_size > _UPLOAD_LIMIT:
            raise HTTPException(413, "Importdatei überschreitet das sichere Größenlimit")
    except OSError as exc:
        raise HTTPException(404, "Importdatei nicht verfügbar") from exc
    ext = path.suffix.lower()
    if ext not in _UPLOAD_EXTENSIONS:
        raise HTTPException(404, "Für diesen Eingang gibt es keine Dateivorschau")
    try:
        with path.open("rb") as source:
            detected = _detected_upload_type(source.read(16))
    except OSError as exc:
        raise HTTPException(404, "Importdatei nicht verfügbar") from exc
    expected = ".jpg" if ext in {".jpg", ".jpeg"} else ext
    if detected != expected:
        raise HTTPException(409, "Importdatei ist beschädigt oder hat ein falsches Format")
    media_type = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }[ext]
    safe_name = Path(
        (entry.get("ai_suggestion") or {}).get("filename") or path.name
    ).name.replace('"', "").replace("\r", "").replace("\n", "")
    return FileResponse(
        path,
        media_type=media_type,
        filename=safe_name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class PendingIngredientIn(BaseModel):
    name: str
    amount: Optional[float] = None
    unit: Optional[str] = None
    raw: Optional[str] = None


class PendingStepIn(BaseModel):
    instruction: str
    timer_seconds: Optional[int] = Field(None, ge=1, le=86_400)


class ResolveBody(BaseModel):
    url: str
    action: str                   # 'save' | 'skip'
    name: Optional[str] = None
    type: Optional[str] = None    # für Rezept
    category: Optional[str] = None
    description: Optional[str] = None
    ingredients: Optional[List[PendingIngredientIn]] = None
    steps: Optional[List[PendingStepIn]] = None
    servings: Optional[int] = Field(None, ge=1, le=50)
    verified: bool = False


@router.post("", dependencies=[Depends(require_admin)])
def resolve(body: ResolveBody):
    if body.action not in ("save", "skip"):
        raise HTTPException(400, "action muss 'save' oder 'skip' sein")
    decision = {
        "action": body.action,
        "name": body.name,
        "type": body.type,
        "category": body.category,
        "description": body.description,
        "ingredients": (
            [item.model_dump() for item in body.ingredients]
            if body.ingredients is not None else None
        ),
        "steps": (
            [item.model_dump() for item in body.steps]
            if body.steps is not None else None
        ),
        "servings": body.servings,
        "verified": body.verified,
    }
    return get_scraper_job().resolve_pending(body.url, decision)


class ReanalyzeRequest(BaseModel):
    url: str


class FailedActionRequest(BaseModel):
    url: str


@router.post("/reanalyze", dependencies=[Depends(require_admin)])
def reanalyze(body: ReanalyzeRequest):
    """Lässt ein Pending-Item neu durch die KI-Cascade laufen."""
    return get_scraper_job().reanalyze_pending(body.url)


import logging as _logging
import threading as _threading
from datetime import datetime as _datetime

_logger = _logging.getLogger(__name__)
_reanalyze_lock = _threading.Lock()


def _reanalyze_all_thread(job_id: int):
    """Background-Worker. Schreibt Progress in eine Log-Datei + bei jedem
    Item updaten wir die summary in der jobs-Tabelle damit das Frontend
    Live-Progress sehen kann.
    
    WICHTIG: ein einziger try/finally umschließt ALLES inkl. Logger-Setup,
    sonst kann der Lock bei FileHandler-Fehler hängen bleiben.
    """
    from ..config_store import get_config as _gc
    db = get_db()
    fh = None
    summary = {
        "total": 0, "auto_saved": 0, "still_pending": 0, "errors": 0,
        "processed": 0, "current": None,
    }
    try:
        log_dir = Path(_gc().get("paths", "logs_dir", default="/opt/scrapper/logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"reanalyze-{_datetime.now():%Y%m%d-%H%M%S}-job{job_id}.log"
        fh = _logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(_logging.INFO)
        fh.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _logging.getLogger().addHandler(fh)
        db.job_set_log_file(job_id, str(log_file))

        job = get_scraper_job()
        try:
            items = db.pending_list("pending")
            summary["total"] = len(items)
            _logger.info(f"=== Pending-Reanalyze {job_id} startet: {summary['total']} Items ===")
            for item in items:
                url = item["url"]
                summary["current"] = url
                db.job_update_summary(job_id, summary)
                try:
                    r = job.reanalyze_pending(url)
                    if not r.get("ok"):
                        summary["errors"] += 1
                        _logger.warning(f"FEHLER {url}: {r.get('error')}")
                    elif r.get("action") == "auto_saved":
                        summary["auto_saved"] += 1
                        _logger.info(f"AUTO-SAVE {url} → {r.get('target')}")
                    else:
                        summary["still_pending"] += 1
                        _logger.info(f"STILL-PENDING {url} (conf={(r.get('analysis') or {}).get('confidence')})")
                except Exception:
                    summary["errors"] += 1
                    _logger.exception(f"Exception {url}")
                summary["processed"] += 1
                db.job_update_summary(job_id, summary)

            summary["current"] = None
            db.job_finish(job_id, "ok", summary)
            _logger.info(f"=== Pending-Reanalyze {job_id} fertig: {summary} ===")
        except Exception as e:
            _logger.exception("Reanalyze-Job crashed")
            db.job_finish(job_id, "error", {"error": str(e), **summary})
    except Exception as e:
        try:
            db.job_finish(job_id, "error", {"error": f"setup failed: {e}", **summary})
        except Exception:
            pass
        _logger.exception(f"Reanalyze-Job {job_id}: Setup gescheitert")
    finally:
        if fh is not None:
            try:
                _logging.getLogger().removeHandler(fh)
                fh.close()
            except Exception:
                pass
        _reanalyze_lock.release()


@router.post("/reanalyze-all", dependencies=[Depends(require_admin)])
def reanalyze_all():
    """Startet Background-Job der alle Pending-Items neu analysiert."""
    if not _reanalyze_lock.acquire(blocking=False):
        raise HTTPException(409, "Reanalyze läuft bereits")
    job_id = get_db().job_start("reanalyze")
    t = _threading.Thread(target=_reanalyze_all_thread, args=(job_id,), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id}


@router.get("/reanalyze/progress", dependencies=[Depends(require_admin)])
def reanalyze_progress():
    db = get_db()
    running = db.job_running("reanalyze")
    if not running:
        last = db.job_list(kind="reanalyze", limit=1)
        return {"running": False, "last": last[0] if last else None}
    import time as _t
    return {
        "running": True,
        "job_id": running["id"],
        "started_at": float(running["started_at"]),
        "elapsed_sec": round(_t.time() - float(running["started_at"])),
        "summary": running.get("summary") or {},
    }


# ---------------- Failed Downloads (Email Recovery) ----------------

@router.get("/failed", dependencies=[Depends(require_admin)])
def list_failed_downloads(limit: int = 100) -> List[Dict[str, Any]]:
    """Liste aller URLs, deren Download mehrfach fehlgeschlagen ist.

    Werden vom Scraper nach MAX_DOWNLOAD_ATTEMPTS (default 3) übersprungen.
    Diese Liste zeigt sie, damit der User entscheiden kann was tun:
    - Retry-Counter zurücksetzen (URL wird beim nächsten Mail-Sync neu versucht)
    - Komplett aus dem Failed-Tracking löschen
    """
    return get_db().download_failures_list(limit=limit)


@router.post("/failed/retry", dependencies=[Depends(require_admin)])
def retry_failed_body(body: FailedActionRequest) -> Dict[str, Any]:
    get_db().download_failure_reset(body.url)
    return {"ok": True, "url": body.url, "reset": True}


@router.post("/failed/discard", dependencies=[Depends(require_admin)])
def discard_failed_body(body: FailedActionRequest) -> Dict[str, Any]:
    db = get_db()
    db.history_add(body.url, content_type="recipe", name="(verworfen)")
    db.download_failure_clear(body.url)
    return {"ok": True, "url": body.url, "discarded": True}


@router.post("/failed/{url:path}/retry", dependencies=[Depends(require_admin)])
def retry_failed(url: str) -> Dict[str, Any]:
    """Setzt den Failure-Counter zurück (Zeile bleibt erhalten).

    Der nächste Scraper-Lauf nimmt die URL als Retry-Kandidat direkt aus
    download_failures auf — die Quell-Mail wird NICHT mehr benötigt
    (verarbeitete Mails werden gelöscht, wenn delete_processed aktiv ist).
    """
    get_db().download_failure_reset(url)
    return {"ok": True, "url": url, "reset": True}


@router.post("/failed/{url:path}/discard", dependencies=[Depends(require_admin)])
def discard_failed(url: str) -> Dict[str, Any]:
    """Verwirft eine endgültig fehlgeschlagene URL dauerhaft.

    Schreibt sie als '(verworfen)' in die History (→ Mail-Sync überspringt
    sie ab jetzt, auch wenn die Mail im Postfach bleibt) und entfernt den
    Failure-Eintrag. Bewusste User-Entscheidung — das frühere automatische
    History-Schreiben nach MAX Versuchen wurde entfernt.
    """
    db = get_db()
    db.history_add(url, content_type="recipe", name="(verworfen)")
    db.download_failure_clear(url)
    return {"ok": True, "url": url, "discarded": True}


@router.post("/failed/clear-all", dependencies=[Depends(require_admin)])
def clear_all_failed() -> Dict[str, Any]:
    """Alle Failure-Counter löschen. Bei nächstem Mail-Sync werden alle
    noch in Mails enthaltenen URLs nochmal versucht."""
    count = get_db().download_failures_clear_all()
    return {"ok": True, "cleared": count}
