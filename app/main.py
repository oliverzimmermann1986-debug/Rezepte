"""
Scrapper Web-Server.
Startet mit:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import os
import html
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .auth import (SESSION_COOKIE, SESSION_MAX_AGE, auth_disabled, check_credentials,
                    create_session, migrate_security, migrate_users_to_db,
                    request_user, require_auth, verify_session)
from .config_store import get_config, migrate_pdf_quality_defaults
from .db import get_db
from .routes import (api_admin, api_audit, api_auth, api_browse, api_config, api_einkauf, api_events, api_hdd,
                     api_history, api_jobs, api_master, api_metrics, api_pending, api_recipes,
                     api_meal_plan, api_schedule, api_share, api_shopping, api_stats, api_test,
                     api_users, sharing)
from .security import (SameOriginMiddleware, SecurityHeadersMiddleware,
                       UploadSizeLimitMiddleware, client_ip, login_limiter)

# -------- Logging --------
# Strukturiertes Logging: rotation via RotatingFileHandler (10MB pro Datei,
# 5 Generationen behalten = max 60MB Logs auf Disk). JSON für File-Output
# damit Tools wie jq/grep -P darauf operieren können. Console bleibt menschen-
# lesbar.
import json
from logging.handlers import RotatingFileHandler

class JSONFormatter(logging.Formatter):
    """JSON-Format für File-Output. Inkludiert exception-Traces strukturiert."""
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Extra-Felder (z.B. logger.info("...", extra={"recipe_id": 42})) mitnehmen
        for key, val in record.__dict__.items():
            if key not in {"name","msg","args","levelname","levelno","pathname",
                           "filename","module","exc_info","exc_text","stack_info",
                           "lineno","funcName","created","msecs","relativeCreated",
                           "thread","threadName","processName","process","getMessage"}:
                try:
                    json.dumps(val)
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = str(val)
        return json.dumps(payload, ensure_ascii=False)


def _create_file_log_handler(target_dir: Path):
    """Erzeugt den rotierenden File-Handler, ohne den App-Start zu gefährden.

    Container und systemd erfassen stderr/stdout bereits zuverlässig. Ein
    vorübergehend nicht beschreibbares Volume darf deshalb nicht verhindern,
    dass die API überhaupt startet; in diesem Fall bleibt der Console-Handler
    aktiv und der Fehler wird dort sichtbar protokolliert.
    """
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target_dir / "web.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    except OSError as exc:
        return None, exc
    handler.setFormatter(JSONFormatter())
    return handler, None


log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
_file_handler, _file_log_error = _create_file_log_handler(log_dir)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
_logging_handlers = [_stream_handler]
if _file_handler is not None:
    _logging_handlers.insert(0, _file_handler)
logging.basicConfig(level=logging.INFO, handlers=_logging_handlers)
logger = logging.getLogger(__name__)
if _file_log_error is not None:
    logger.warning(
        "File-Logging unter %s nicht verfügbar; verwende Console-Logging: %s",
        log_dir,
        _file_log_error,
    )

# -------- Startup-Initialisierung --------
# Absichtlich NICHT beim Modulimport: Imports bleiben dadurch nebenwirkungsarm,
# CLI/Tests können das App-Modul laden ohne sofort Migrationen/Cleanup auszulösen.
_db = None


def _initialize_runtime_state():
    global _db
    migrate_security()
    if migrate_pdf_quality_defaults():
        logger.info("PDF-Qualitätsprofil auf v1.2.1 migriert")

    _db = get_db()
    stale = _db.reset_stale_running()
    if stale:
        logger.warning("%s Job(s) nach Crash/Restart auf 'error' gesetzt", stale)
    stale_maintenance = _db.reset_stale_maintenance()
    if stale_maintenance:
        logger.warning(
            "%s Wartungslauf/-läufe nach Crash/Restart beendet", stale_maintenance,
        )
    migrate_users_to_db()

    jobs_purged = _db.cleanup_old_jobs(days=90)
    if jobs_purged:
        logger.info("DB-Cleanup: %s alte Job-Einträge gelöscht", jobs_purged)
    pending_skipped = _db.auto_skip_old_pending(days=30)
    if pending_skipped:
        logger.info("DB-Cleanup: %s alte Pending-Items übersprungen", pending_skipped)
    from .core.temp_cleanup import cleanup_temp_files
    temp_root = Path(get_config().get("paths", "temp_dir", default="/opt/scrapper/temp"))
    temp_result = cleanup_temp_files(temp_root, _db.pending_file_paths())
    if temp_result["removed"]:
        logger.info(
            "Temp-Cleanup: %s veraltete Einträge (%s Bytes) entfernt",
            temp_result["removed"], temp_result["bytes_removed"],
        )
    if temp_result["errors"]:
        logger.warning("Temp-Cleanup unvollständig: %s", temp_result["errors"])
    return _db


def _sd_notify(state: str) -> None:
    """Sendet eine Statusnachricht an systemd, wenn unter Type=notify gestartet.
    Ohne externe Dependency - direkter Socket-Write zum NOTIFY_SOCKET.
    No-op wenn $NOTIFY_SOCKET nicht gesetzt ist."""
    sock_path = os.getenv("NOTIFY_SOCKET")
    if not sock_path:
        return
    try:
        import socket
        # Abstract socket (Linux): startet mit \0
        addr = b"\0" + sock_path[1:].encode() if sock_path.startswith("@") else sock_path
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode(), addr)
    except Exception as e:
        logger.warning(f"sd_notify failed: {e}")


async def _watchdog_loop() -> None:
    """Bedient systemds Watchdog nur solange der asyncio-Loop reaktionsfähig ist."""
    import asyncio

    try:
        watchdog_usec = int(os.getenv("WATCHDOG_USEC", "0"))
    except ValueError:
        watchdog_usec = 0
    if watchdog_usec <= 0:
        return
    interval = max(1.0, watchdog_usec / 2_000_000)
    while True:
        await asyncio.sleep(interval)
        _sd_notify("WATCHDOG=1")


from contextlib import asynccontextmanager


_trash_cleanup_thread_started = False
_trash_cleanup_thread = None
_trash_cleanup_stop = None


def _start_trash_cleanup_thread():
    """Spawnt einen Daemon-Thread der einmal pro Tag den Papierkorb auf
    Items > 30 Tage prüft und sie endgültig löscht. Idempotent — wird
    bei Re-Start des FastAPI-Lifespans nicht doppelt gestartet."""
    global _trash_cleanup_thread_started, _trash_cleanup_thread, _trash_cleanup_stop
    if _trash_cleanup_thread_started:
        return
    _trash_cleanup_thread_started = True
    import threading
    _trash_cleanup_stop = threading.Event()
    def _loop():
        # Erste Iteration nach 60s, dann alle 24h. So sieht der Job auch
        # Items die durch laufende Tests/Sessions reingekommen sind ohne
        # gleich beim Boot auf DB-Locks zu kollidieren.
        if _trash_cleanup_stop.wait(60):
            return
        while not _trash_cleanup_stop.is_set():
            try:
                _purge_old_trash_items()
            except Exception as e:
                logger.exception(f"trash cleanup loop fail: {e}")
            if _trash_cleanup_stop.wait(24 * 3600):
                return
    _trash_cleanup_thread = threading.Thread(target=_loop, name="trash-cleanup", daemon=True)
    _trash_cleanup_thread.start()
    logger.info("Trash-cleanup-thread started (24h interval, >30d purge)")


def _stop_trash_cleanup_thread(timeout: float = 5.0) -> bool:
    global _trash_cleanup_thread_started, _trash_cleanup_thread, _trash_cleanup_stop
    if _trash_cleanup_stop is not None:
        _trash_cleanup_stop.set()
    thread = _trash_cleanup_thread
    if thread and thread.is_alive():
        thread.join(timeout=max(0.0, timeout))
    stopped = not bool(thread and thread.is_alive())
    if stopped:
        _trash_cleanup_thread = None
        _trash_cleanup_stop = None
        _trash_cleanup_thread_started = False
    return stopped


def _purge_old_trash_items(days: int = 30):
    """Endgültig löschen aller Trash-Items deren deleted_at > days Tage her ist."""
    from .recipes.manage import safe_delete_recipe
    db = _db or get_db()
    items = db.recipe_list_trash_expired(days=days)
    if not items:
        return
    logger.info(f"trash-cleanup: {len(items)} items >{days}d found")
    for it in items:
        try:
            # delete_files=True nur wenn die Files noch da sind (files_deleted=0).
            # Falls files_deleted=1, nur DB-Eintrag noch.
            delete_files = not it.get("files_deleted")
            safe_delete_recipe(db, it["id"], delete_files=delete_files, hard=True)
        except Exception as e:
            logger.warning(f"trash-purge #{it['id']} '{it.get('name')}' fail: {e}")


@asynccontextmanager
async def _lifespan(app):
    import asyncio
    from contextlib import suppress

    db = _initialize_runtime_state()
    from .routes.api_admin import reset_pdf_runtime
    reset_pdf_runtime()
    # Trash-Cleanup-Background-Thread starten: einmal pro Tag prüft er ob
    # Rezepte im Papierkorb älter als 30 Tage sind und purged sie endgültig.
    _start_trash_cleanup_thread()
    from .jobs.task_queue import start_worker
    from .recipes.sync_manager import request_sync
    start_worker()
    # Ein potenziell langsamer HDD/NAS-Scan wird beim Start nur eingeplant,
    # niemals innerhalb eines Rezeptlisten-Requests ausgeführt.
    request_sync(reason="app-start", min_interval=300.0, db=db)
    # READY erst nachdem die persistenten Worker gestartet wurden. So meldet
    # systemd keinen betriebsbereiten Prozess mit bereits toter Task-Queue.
    _sd_notify("READY=1")
    logger.info("App ready (workers running, sd_notify READY=1 sent)")
    watchdog_task = asyncio.create_task(_watchdog_loop(), name="systemd-watchdog")
    try:
        yield
    finally:
        watchdog_task.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog_task
        _sd_notify("STOPPING=1")
        from .jobs.task_queue import stop_worker
        from .recipes.sync_manager import wait_for_sync
        from .routes.api_audit import stop_ai_sanity_thread
        from .routes.api_admin import stop_pdf_executor
        from .routes.api_history import stop_history_reanalysis
        from .routes.api_jobs import stop_scraper_thread
        from .routes.api_pending import stop_pending_reanalysis
        _stop_trash_cleanup_thread(timeout=2.0)
        queue_stopped = stop_worker(timeout=8.0)
        sync_stopped = not wait_for_sync(timeout=5.0).get("running", False)
        if not sync_stopped:
            logger.warning("Dateisystem-Sync nach 5s noch aktiv")
        scraper_stopped = stop_scraper_thread(timeout=8.0)
        if not scraper_stopped:
            logger.warning("Scraper-Thread nach 8s noch aktiv")
        audit_stopped = stop_ai_sanity_thread(timeout=8.0)
        if not audit_stopped:
            logger.warning("Audit-KI-Thread nach 8s noch aktiv")
        pending_stopped = stop_pending_reanalysis(timeout=8.0)
        if not pending_stopped:
            logger.warning("Pending-Reanalyse nach 8s noch aktiv")
        history_stopped = stop_history_reanalysis(timeout=8.0)
        if not history_stopped:
            logger.warning("History-Reanalyse nach 8s noch aktiv")
        pdf_stopped = stop_pdf_executor(timeout=8.0)
        if not pdf_stopped:
            logger.warning("PDF-Worker nach 8s noch aktiv")
        if not queue_stopped:
            logger.warning("Background-Task-Worker nach 8s noch aktiv")
        other_workers_stopped = all((
            queue_stopped, scraper_stopped, audit_stopped,
            pending_stopped, history_stopped, pdf_stopped, sync_stopped,
        ))
        # Sauberes Shutdown: Worker-Thread stoppen damit keine FTS-Transaktion
        # mitten im Schreiben abreißt (SQLite-Korruption-Risiko bei SIGKILL).
        # Wir warten max 10s — zusammen mit den anderen Workern bleibt der
        # gesamte Shutdown unter systemd TimeoutStopSec=90s. Bei einem noch
        # länger laufenden Einzelaufruf bleibt WAL-Mode der letzte Crash-Schutz.
        try:
            from .recipes.indexer import stop_extraction, is_extraction_running
            stop_extraction()
            import asyncio as _aio, time as _t
            deadline = _t.time() + 10
            while is_extraction_running() and _t.time() < deadline:
                await _aio.sleep(0.5)
            if is_extraction_running():
                logger.warning("Worker nach 10s noch aktiv — wird gekillt")
            else:
                logger.info("Worker sauber beendet")

            # PRAGMA optimize beim Shutdown — SQLite-Doc empfiehlt das vor
            # längeren Shutdowns. Plus WAL-Checkpoint(TRUNCATE) damit die
            # -wal-Datei nicht beim nächsten Start gross ist. Beide günstig
            # (~ms) und ohne Risiko bei WAL-Mode.
            if other_workers_stopped and not is_extraction_running():
                try:
                    with db.conn() as c:
                        c.execute("PRAGMA optimize")
                        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    logger.info("DB: optimize + wal_checkpoint(TRUNCATE) ok")
                except Exception as e:
                    logger.warning(f"DB-Cleanup beim Shutdown failed: {e}")
            else:
                logger.warning("DB-Checkpoint wegen noch aktiver Worker übersprungen")
        except Exception as e:
            logger.warning(f"Worker-Shutdown failed: {e}")


# -------- FastAPI --------
APP_VERSION = __version__
APP_CAPABILITIES = [
    "admin-center",
    "ai-shopping-optimization",
    "shopping-categories",
    "native-admin-roles",
    "native-admin-config-v1",
    "guest-read-only",
    "pdf-processing",
    "pdf-background-jobs",
    "pdf-preflight",
    "pdf-recipe-extraction",
    "einkauf-proxy",
    "recurring-shopping",
    "ingredient-tristate-filter",
    "weekly-meal-plan",
    "weekly-meal-plan-pdf",
    "recipe-pdf-export",
]

# Docs nur aktiv wenn explizit angefragt (Default: aus für Production).
_enable_docs = os.getenv("SCRAPPER_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Rezepte",
    version=APP_VERSION,
    docs_url="/api/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if _enable_docs else None,
    lifespan=_lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SameOriginMiddleware)
app.add_middleware(UploadSizeLimitMiddleware)

# gzip-Compression für API-Responses + HTML. Spart ~70% Transfer-Bytes auf
# JSON-Listen, ~50% auf HTML. Schwelle 500 Bytes — kleinere Responses bleiben
# unkomprimiert (Overhead lohnt sich nicht). Bilder (JPEG/PNG) werden nicht
# komprimiert weil sie schon komprimiert sind.
from fastapi.middleware.gzip import GZipMiddleware


class SelectiveGZipMiddleware:
    """Komprimiert normale Antworten, lässt Live-SSE ungepuffert passieren."""

    def __init__(self, app, minimum_size: int = 500, compresslevel: int = 5):
        self.app = app
        self.gzip = GZipMiddleware(
            app,
            minimum_size=minimum_size,
            compresslevel=compresslevel,
        )

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/api/events":
            await self.app(scope, receive, send)
            return
        await self.gzip(scope, receive, send)


app.add_middleware(SelectiveGZipMiddleware, minimum_size=500, compresslevel=5)

# Statisch (Frontend)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Service-Worker + Manifest direkt aus root servieren mit korrekten Headers.
# SW braucht 'Service-Worker-Allowed: /' damit der scope auf root sein darf
# wenn die Datei aus /static/ kommt; einfacher: direkt aus root liefern.
@app.get("/sw.js", include_in_schema=False)
def serve_sw():
    from fastapi.responses import FileResponse
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.json", include_in_schema=False)
def serve_manifest():
    from fastapi.responses import FileResponse
    return FileResponse(STATIC_DIR / "manifest.json",
                        media_type="application/manifest+json")


# API-Routen
app.include_router(api_admin.session_router)
app.include_router(api_auth.router)
app.include_router(api_admin.router)
app.include_router(api_config.router)
app.include_router(api_jobs.router)
app.include_router(api_pending.router)
app.include_router(api_history.router)
app.include_router(api_test.router)
app.include_router(api_browse.router)
app.include_router(api_schedule.router)
app.include_router(api_metrics.router)
app.include_router(api_stats.router)
app.include_router(api_hdd.router)
app.include_router(api_events.router)
app.include_router(api_recipes.router)
app.include_router(api_einkauf.router)
app.include_router(api_shopping.router)
app.include_router(api_meal_plan.router)
app.include_router(api_audit.router)
app.include_router(api_master.router)
app.include_router(api_users.router)
app.include_router(api_share.router)
app.include_router(api_share.info_router)
# Sharing: Print-View (auth), Share-Token-API (auth), Public-Share (NO auth)
app.include_router(sharing.print_router)
app.include_router(sharing.share_api_router)
app.include_router(sharing.public_router)


# -------- Cookie-Helper --------
def _set_session_cookie(resp, token: str, request: Request) -> None:
    proto = request.headers.get("x-forwarded-proto", "").lower()
    is_https = proto == "https" or request.url.scheme == "https"
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


# -------- Login / Logout --------
LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="de"><head>
<meta charset="UTF-8"><title>Login · Rezepte</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/rezepte.css">
</head><body class="login-body">
<form method="post" action="/login" class="login-card">
  <div class="login-brand">
    <div class="brand-mark"><svg class="brand-chef-icon" viewBox="0 0 48 48" aria-hidden="true"><path d="M15 37h18v5H15z"/><path d="M14 34V23.5c-4.1-.5-7-3.6-7-7.4 0-4.2 3.5-7.6 7.8-7.6 1.2 0 2.4.3 3.4.8C19.7 6.1 22.9 4 26.5 4c4.8 0 8.8 3.7 9.2 8.4h.8c4.1 0 7.5 3.3 7.5 7.4 0 3.9-3.1 7.1-7 7.4V34H14z" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M19 21v10M29 21v10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></div>
    <div><h1>Rezepte</h1><p class="muted">Deine persönliche Rezeptbibliothek</p></div>
  </div>
  {error}
  <input type="hidden" name="next" value="{next}">
  <label>Benutzer<input name="username" autocomplete="username" required></label>
  <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Anmelden</button>
</form>
</body></html>
"""


def _safe_next(value: str) -> str:
    """Open-Redirect-Schutz: nur lokale Pfade erlauben."""
    if (
        not value
        or "\\" in value
        or not value.startswith("/")
        or value.startswith("//")
        or urlsplit(value).scheme
        or urlsplit(value).netloc
    ):
        return "/"
    return value


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/"):
    if auth_disabled():
        return RedirectResponse(url="/", status_code=303)
    return LOGIN_HTML.format(error="", next=html.escape(_safe_next(next), quote=True))


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_page():
    """Öffentliche Datenschutzhinweise für App Store und native App."""
    return HTMLResponse(
        """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Datenschutz – Rezepte</title>
<style>
body{margin:0;background:#fffaf0;color:#433427;font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:720px;margin:auto;padding:40px 20px 80px}h1{font-size:36px;line-height:1.1}
h2{margin-top:32px;font-size:22px}a{color:#433427}small{color:#7b6a5c}
</style></head><body><main>
<h1>Datenschutz</h1>
<p><strong>Rezepte</strong> ist eine private, selbst gehostete
Rezeptverwaltung. Verantwortlich ist der Betreiber des Servers, dessen
Adresse in der App eingetragen wurde.</p>
<h2>Verarbeitete Daten</h2>
<p>Die App verarbeitet die Server-Adresse, den Benutzernamen, ein
widerrufbares Sitzungstoken sowie die auf dem privaten Server gespeicherten
Rezepte, Einkaufslisten und Wochenpläne. Dazu können vom Nutzer geteilte
Quellenlinks sowie hochgeladene Bilder und PDF-Dokumente gehören. Das Passwort
wird nur zur Anmeldung verschlüsselt an den Rezepteserver übertragen, dort
geprüft und nicht von der App gespeichert.</p>
<h2>Speicherung und Übertragung</h2>
<p>Das Sitzungstoken und – falls Cloudflare Access verwendet wird – die vom
Nutzer eingegebenen Cloudflare-Gerätezugangsdaten werden im iOS-Schlüsselbund
gespeichert. Das eigentliche Passwort wird nicht gespeichert. Die Kommunikation
erfolgt über HTTPS direkt mit dem eingetragenen Rezepteserver. Die App enthält
keine Werbung, keine Telemetrie und keine Analyse-SDKs.</p>
<h2>KI-gestützte Verarbeitung</h2>
<p>Wenn eine KI-Funktion verwendet wird, kann der Rezepteserver die dafür
erforderlichen Rezepttexte, Bilder, PDF-Inhalte oder aus einer Quelle
extrahierten Audio- und Bildinformationen an OpenAI übermitteln. Die
Übermittlung dient ausschließlich dazu, Zutaten, Mengen, Zubereitungsschritte,
Kategorien oder ähnliche Rezeptinformationen zu erkennen und zu ordnen. Die
App nutzt diese Daten nicht für Werbung oder Tracking.</p>
<h2>Externe Quellen</h2>
<p>TikTok- und Instagram-Medien werden nicht heruntergeladen. Erst wenn
ein Nutzer den Quellenlink antippt, wird er an die jeweilige externe App oder
Website übergeben; dann gelten deren Datenschutzbestimmungen.</p>
<h2>Öffentliche Rezeptlinks</h2>
<p>Nur nach ausdrücklicher Bestätigung kann die App einen öffentlichen Link
erstellen. Jeder mit diesem Link kann das ausgewählte Rezept einschließlich
Cover sehen. In der iPhone-App sind neue Links sieben Tage gültig und enthalten
keinen Benutzernamen. Aktive Links können in der App eingesehen und jederzeit
sofort widerrufen werden.</p>
<h2>Löschung und Auskunft</h2>
<p>Rezepte und Kontodaten werden vom Betreiber des privaten Servers verwaltet.
Anfragen zu Auskunft oder Löschung sind an diesen Betreiber zu richten. Durch
Abmelden werden Sitzungstoken, Cloudflare-Zugangsdaten und private
Bildcaches vom iPhone entfernt; die Serversitzung wird widerrufen.</p>
<p><small>Stand: 24. August 2026</small></p>
</main></body></html>"""
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    ip = client_ip(request)
    normalized_username = username.strip().casefold()
    ip_key = f"ip:{ip}"
    actor_key = f"ip-user:{ip}:{normalized_username}"
    ip_blocked, ip_remaining = login_limiter.is_blocked(ip_key)
    actor_blocked, actor_remaining = login_limiter.is_blocked(actor_key)
    blocked = ip_blocked or actor_blocked
    remaining = max(ip_remaining, actor_remaining)
    if blocked:
        logger.warning(f"Login-Block für IP {ip}, noch {remaining}s")
        return HTMLResponse(
            LOGIN_HTML.format(
                error=f'<p class="error">⛔ Zu viele Fehlversuche. '
                      f'Erneut probieren in {remaining // 60 + 1} min.</p>',
                next=html.escape(_safe_next(next), quote=True),
            ),
            status_code=429,
        )

    if not check_credentials(username, password):
        login_limiter.record_fail(ip_key)
        login_limiter.record_fail(actor_key)
        logger.warning(f"Fehl-Login von {ip} (user={username!r})")
        return HTMLResponse(
            LOGIN_HTML.format(
                error='<p class="error">❌ Login fehlgeschlagen</p>',
                next=html.escape(_safe_next(next), quote=True),
            ),
            status_code=401,
        )

    # Ein erfolgreicher Low-Privilege-Login darf die IP-weiten Fehlversuche
    # gegen ein anderes (z.B. Admin-)Konto nicht zurücksetzen.
    login_limiter.record_success(actor_key)
    try:
        token = create_session(username)
    except ValueError:
        # Konto kann zwischen Credential-Prüfung und Session-Erstellung
        # deaktiviert oder gelöscht worden sein.
        logger.warning("Session-Erstellung für %r nach erfolgreichem Login abgelehnt", username)
        return HTMLResponse(
            LOGIN_HTML.format(
                error='<p class="error">❌ Konto ist nicht mehr aktiv</p>',
                next=html.escape(_safe_next(next), quote=True),
            ),
            status_code=401,
        )
    resp = RedirectResponse(url=_safe_next(next), status_code=303)
    _set_session_cookie(resp, token, request)
    return resp


def _logout_target() -> str:
    """Ziel passend zur aktiven Authentifizierungsgrenze wählen."""
    if not auth_disabled():
        return "/login"

    configured = str(
        get_config().get("web", "external_logout_url", default="") or ""
    ).strip()
    if configured:
        parsed = urlsplit(configured)
        is_local_path = configured.startswith("/") and not configured.startswith("//")
        is_https_url = parsed.scheme == "https" and bool(parsed.netloc)
        if is_local_path or is_https_url:
            return configured
        logger.warning("Unsichere web.external_logout_url ignoriert: %r", configured)

    # Offizieller Logout-Endpunkt für Cloudflare Access. Ein relativer Pfad
    # funktioniert unabhängig vom öffentlichen Hostnamen der Installation.
    return "/cdn-cgi/access/logout"


@app.post("/logout")
def logout(request: Request):
    if not auth_disabled():
        username = request_user(request)
        if username:
            try:
                get_db().user_revoke_sessions(username)
            except Exception:
                # Cookie lokal trotzdem entfernen. Ein DB-Ausfall darf den
                # Nutzer nicht in einer scheinbar unlösbaren Sitzung halten.
                logger.exception("Serversitzung beim Browser-Logout nicht widerrufen")
    resp = RedirectResponse(url=_logout_target(), status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    # Löscht insbesondere Cache Storage alter Service-Worker-Versionen. Private
    # Rezeptdaten dürfen auf gemeinsam genutzten Geräten nicht nach Logout
    # offline verfügbar bleiben.
    resp.headers["Clear-Site-Data"] = '"cache", "storage"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


# -------- Home (geschützt) --------
def _static_version() -> str:
    """Cache-Buster für /static/app.js und /static/rezepte.css.

    Max-mtime von app.js UND rezepte.css als Token. Vorher nur app.js —
    reine CSS-Deploys änderten die URL nicht und Browser/SW lieferten
    altes CSS aus dem Cache."""
    try:
        m = max(
            int((STATIC_DIR / "app.js").stat().st_mtime),
            int((STATIC_DIR / "rezepte.css").stat().st_mtime),
            int((STATIC_DIR / "runtime.js").stat().st_mtime),
        )
        return str(m)
    except Exception:
        return "0"


def _render_spa(request: Request, *, initial_page: str = "recipes",
                initial_admin_tab: str = "home"):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not auth_disabled() and (not token or not verify_session(token)):
        next_path = request.url.path or "/"
        return RedirectResponse(url=f"/login?next={next_path}", status_code=303)

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{VERSION}", _static_version())
    # Kein Inline-Bootstrap und keine Query-Abhängigkeit: die echte Route setzt
    # den Startzustand über data-Attribute. Das funktioniert auch bei PWA-Start,
    # Browser-Reload und direktem Aufruf vom Handy.
    body_attrs = (
        f'<body data-initial-page="{initial_page}" '
        f'data-initial-admin-tab="{initial_admin_tab}">'
    )
    html = html.replace("<body>", body_attrs, 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/admin", response_class=HTMLResponse)
def admin_shortcut(request: Request):
    """Echte Admin-Einstiegsseite; kein Redirect und keine Query-Navigation."""
    return _render_spa(request, initial_page="admin", initial_admin_tab="home")


@app.get("/admin/pdf", response_class=HTMLResponse)
def admin_pdf_shortcut(request: Request):
    return _render_spa(request, initial_page="admin", initial_admin_tab="pdf")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render_spa(request)


# -------- Exception-Handler --------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == status.HTTP_303_SEE_OTHER and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.get("/healthz")
def healthz():
    """Liveness + DB-Ping. CF-Tunnel/Reverse-Proxy nutzt das als Health-Check."""
    try:
        with get_db().conn() as c:
            c.execute("SELECT 1").fetchone()
        return {"ok": True, "version": APP_VERSION, "capabilities": APP_CAPABILITIES}
    except Exception as e:
        logger.error(f"healthz failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/readyz")
def readyz():
    """Readiness: DB und persistenter Queue-Worker müssen verfügbar sein."""
    from .jobs.task_queue import worker_status

    try:
        with get_db().conn() as c:
            c.execute("SELECT 1").fetchone()
        queue = worker_status()
        if not queue["running"]:
            return JSONResponse(
                {"ok": False, "db": True, "task_queue": queue},
                status_code=503,
            )
        return {"ok": True, "db": True, "task_queue": queue,
                "version": APP_VERSION}
    except Exception as exc:
        logger.error("readyz failed: %s", exc)
        return JSONResponse(
            {"ok": False, "db": False, "error": str(exc)},
            status_code=503,
        )


@app.get("/api/system/info")
def system_info():
    """Nicht-sensible Build-Information für Update- und UI-Kompatibilitätschecks."""
    return {"name": "Rezepte", "version": APP_VERSION,
            "capabilities": APP_CAPABILITIES}


@app.get("/healthz/deep", dependencies=[Depends(require_auth)])
def healthz_deep():
    """Tiefer Check: DB + OpenAI + IMAP + Disk-Space.
    Status-Code immer 200, Details im Body. Wir wollen nicht dass eine
    kaputte IMAP-Config den ganzen Container als 'unhealthy' markiert."""
    import shutil
    from .config_store import get_config, migrate_pdf_quality_defaults

    checks = {}
    cfg = get_config()

    # DB
    try:
        with get_db().conn() as c:
            c.execute("SELECT 1").fetchone()
        checks["db"] = {"ok": True}
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)}

    # OpenAI
    try:
        from .core.analyzer import build_analyzer
        ai_cfg = cfg.get("ai", default={}) or {}
        analyzer = build_analyzer(ai_cfg)
        # Health-Check mit kurzem Timeout damit /healthz nicht hängt
        analyzer.timeout = 5
        checks["openai"] = {"ok": analyzer.health(), "model": analyzer.model}
    except Exception as e:
        checks["openai"] = {"ok": False, "error": str(e)}

    # Disk-Space (recipe_dir + temp_dir)
    for key in ("recipe_dir", "wedding_dir", "temp_dir"):
        p = cfg.get("paths", key, default=None)
        if not p:
            continue
        # Whitespace im YAML-Value abräumen - sehr leichter Tippfehler beim
        # manuellen Editieren, und führt sonst zu 'path does not exist'.
        p = p.strip() if isinstance(p, str) else p
        try:
            usage = shutil.disk_usage(p)
            free_gb = usage.free / (1024 ** 3)
            checks[f"disk_{key}"] = {
                "ok": free_gb > 1.0,
                "path": p,
                "free_gb": round(free_gb, 2),
                "warning": "Less than 1 GB free!" if free_gb < 1.0 else None,
            }
        except FileNotFoundError:
            checks[f"disk_{key}"] = {"ok": False, "path": p, "error": "path does not exist"}
        except Exception as e:
            checks[f"disk_{key}"] = {"ok": False, "path": p, "error": str(e)}

    overall = all(v.get("ok", False) for v in checks.values())
    return {"ok": overall, "checks": checks}
