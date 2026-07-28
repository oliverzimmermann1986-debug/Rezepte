"""
Scrapper Web-Server.
Startet mit:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (SESSION_COOKIE, SESSION_MAX_AGE, auth_disabled, check_credentials,
                    create_session, migrate_security, migrate_users_to_db,
                    verify_session)
from .config_store import get_config, migrate_pdf_quality_defaults
from .db import get_db
from .routes import (api_admin, api_audit, api_browse, api_config, api_einkauf, api_events, api_hdd,
                     api_history, api_jobs, api_master, api_metrics, api_pending, api_recipes,
                     api_schedule, api_share, api_shopping, api_stats, api_test, api_users, sharing)
from .security import SecurityHeadersMiddleware, client_ip, login_limiter

# -------- Logging --------
log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
log_dir.mkdir(parents=True, exist_ok=True)

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

_file_handler = RotatingFileHandler(
    log_dir / "web.log", maxBytes=10 * 1024 * 1024, backupCount=5,
    encoding="utf-8"
)
_file_handler.setFormatter(JSONFormatter())
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
logger = logging.getLogger(__name__)

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
    migrate_users_to_db()

    jobs_purged = _db.cleanup_old_jobs(days=90)
    if jobs_purged:
        logger.info("DB-Cleanup: %s alte Job-Einträge gelöscht", jobs_purged)
    pending_skipped = _db.auto_skip_old_pending(days=30)
    if pending_skipped:
        logger.info("DB-Cleanup: %s alte Pending-Items übersprungen", pending_skipped)
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


from contextlib import asynccontextmanager


_trash_cleanup_thread_started = False


def _start_trash_cleanup_thread():
    """Spawnt einen Daemon-Thread der einmal pro Tag den Papierkorb auf
    Items > 30 Tage prüft und sie endgültig löscht. Idempotent — wird
    bei Re-Start des FastAPI-Lifespans nicht doppelt gestartet."""
    global _trash_cleanup_thread_started
    if _trash_cleanup_thread_started:
        return
    _trash_cleanup_thread_started = True
    import threading, time as _t
    def _loop():
        # Erste Iteration nach 60s, dann alle 24h. So sieht der Job auch
        # Items die durch laufende Tests/Sessions reingekommen sind ohne
        # gleich beim Boot auf DB-Locks zu kollidieren.
        _t.sleep(60)
        while True:
            try:
                _purge_old_trash_items()
            except Exception as e:
                logger.exception(f"trash cleanup loop fail: {e}")
            _t.sleep(24 * 3600)
    threading.Thread(target=_loop, name="trash-cleanup", daemon=True).start()
    logger.info("Trash-cleanup-thread started (24h interval, >30d purge)")


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
    db = _initialize_runtime_state()
    # READY=1 sobald der App-Startup durch ist (DB-Pings, Routes registriert).
    # systemd wartet dann auf dieses Signal bevor 'systemctl start' returnt -
    # damit ist ein 'restart' ohne 502-Lücke am Reverse-Proxy möglich.
    _sd_notify("READY=1")
    logger.info("App ready (sd_notify READY=1 sent)")
    # Trash-Cleanup-Background-Thread starten: einmal pro Tag prüft er ob
    # Rezepte im Papierkorb älter als 30 Tage sind und purged sie endgültig.
    _start_trash_cleanup_thread()
    from .jobs.task_queue import start_worker
    from .recipes.sync_manager import request_sync
    start_worker()
    # Ein potenziell langsamer HDD/NAS-Scan wird beim Start nur eingeplant,
    # niemals innerhalb eines Rezeptlisten-Requests ausgeführt.
    request_sync(reason="app-start", min_interval=300.0, db=db)
    try:
        yield
    finally:
        _sd_notify("STOPPING=1")
        from .jobs.task_queue import stop_worker
        from .recipes.sync_manager import wait_for_sync
        stop_worker()
        wait_for_sync(timeout=10.0)
        # Sauberes Shutdown: Worker-Thread stoppen damit keine FTS-Transaktion
        # mitten im Schreiben abreißt (SQLite-Korruption-Risiko bei SIGKILL).
        # Wir warten max 25s — systemd-Default TimeoutStopSec ist 90s, lässt
        # also Puffer. Bei längerem worker-loop wird nach 25s zu SIGKILL eskaliert,
        # aber WAL-Mode macht das Crash-safe.
        try:
            from .recipes.indexer import stop_extraction, is_extraction_running
            stop_extraction()
            import asyncio as _aio, time as _t
            deadline = _t.time() + 25
            while is_extraction_running() and _t.time() < deadline:
                await _aio.sleep(0.5)
            if is_extraction_running():
                logger.warning("Worker nach 25s noch aktiv — wird gekillt")
            else:
                logger.info("Worker sauber beendet")

            # PRAGMA optimize beim Shutdown — SQLite-Doc empfiehlt das vor
            # längeren Shutdowns. Plus WAL-Checkpoint(TRUNCATE) damit die
            # -wal-Datei nicht beim nächsten Start gross ist. Beide günstig
            # (~ms) und ohne Risiko bei WAL-Mode.
            try:
                with db.conn() as c:
                    c.execute("PRAGMA optimize")
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                logger.info("DB: optimize + wal_checkpoint(TRUNCATE) ok")
            except Exception as e:
                logger.warning(f"DB-Cleanup beim Shutdown failed: {e}")
        except Exception as e:
            logger.warning(f"Worker-Shutdown failed: {e}")


# -------- FastAPI --------
APP_VERSION = "1.2.7"
APP_CAPABILITIES = [
    "admin-center",
    "pdf-processing",
    "pdf-background-jobs",
    "pdf-preflight",
    "pdf-recipe-extraction",
    "einkauf-proxy",
    "recurring-shopping",
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

# gzip-Compression für API-Responses + HTML. Spart ~70% Transfer-Bytes auf
# JSON-Listen, ~50% auf HTML. Schwelle 500 Bytes — kleinere Responses bleiben
# unkomprimiert (Overhead lohnt sich nicht). Bilder (JPEG/PNG) werden nicht
# komprimiert weil sie schon komprimiert sind.
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)

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
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/"):
    if auth_disabled():
        return RedirectResponse(url="/", status_code=303)
    return LOGIN_HTML.format(error="", next=_safe_next(next))


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    ip = client_ip(request)
    blocked, remaining = login_limiter.is_blocked(ip)
    if blocked:
        logger.warning(f"Login-Block für IP {ip}, noch {remaining}s")
        return HTMLResponse(
            LOGIN_HTML.format(
                error=f'<p class="error">⛔ Zu viele Fehlversuche. '
                      f'Erneut probieren in {remaining // 60 + 1} min.</p>',
                next=_safe_next(next),
            ),
            status_code=429,
        )

    if not check_credentials(username, password):
        login_limiter.record_fail(ip)
        logger.warning(f"Fehl-Login von {ip} (user={username!r})")
        return HTMLResponse(
            LOGIN_HTML.format(
                error='<p class="error">❌ Login fehlgeschlagen</p>',
                next=_safe_next(next),
            ),
            status_code=401,
        )

    login_limiter.record_success(ip)
    try:
        token = create_session(username)
    except ValueError:
        # Konto kann zwischen Credential-Prüfung und Session-Erstellung
        # deaktiviert oder gelöscht worden sein.
        logger.warning("Session-Erstellung für %r nach erfolgreichem Login abgelehnt", username)
        return HTMLResponse(
            LOGIN_HTML.format(
                error='<p class="error">❌ Konto ist nicht mehr aktiv</p>',
                next=_safe_next(next),
            ),
            status_code=401,
        )
    resp = RedirectResponse(url=_safe_next(next), status_code=303)
    _set_session_cookie(resp, token, request)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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


@app.get("/api/system/info")
def system_info():
    """Nicht-sensible Build-Information für Update- und UI-Kompatibilitätschecks."""
    return {"name": "Rezepte", "version": APP_VERSION,
            "capabilities": APP_CAPABILITIES}


@app.get("/healthz/deep")
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
