"""
Scrapper Web-Server.
Startet mit:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import html
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (SESSION_COOKIE, SESSION_MAX_AGE, check_credentials,
                    create_session, migrate_security, require_auth, verify_session)
from .config_store import get_config
from .db import get_db
from .routes import (api_browse, api_config, api_events, api_hdd, api_history,
                     api_jobs, api_metrics, api_pending, api_recipes, api_schedule,
                     api_stats, api_test)
from .security import (SameOriginMiddleware, SecurityHeadersMiddleware, client_ip,
                       login_limiter)

# -------- Logging --------
log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "web.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# -------- Security-Migration (Erststart) --------
# Hasht Klartext-Pwd, generiert Secret, blockt admin/changeme.
migrate_security()

# DB initialisieren + verwaiste Running-Jobs aufräumen. Separate systemd-
# Separate Scraper-Prozesse können einen Web-Restart überleben; ihr File-Lock
# schützen jeweils den neuesten passenden DB-Job vor einer Falschmarkierung.
_db = get_db()
_protected_job_ids = set()
try:
    from .jobs.locks import is_locked

    _running = _db.running_jobs()
    if is_locked("scraper"):
        _active = next((j for j in _running if j.get("kind") == "scraper"), None)
        if _active:
            _protected_job_ids.add(int(_active["id"]))
except (OSError, ValueError):
    logger.exception("Aktive Job-Locks konnten beim Startup nicht geprüft werden")

_stale = _db.reset_stale_running(_protected_job_ids)
if _stale:
    logger.warning(
        "%s verwaiste Job(s) wurden auf 'error' gesetzt (Crash/Restart-Recovery)",
        _stale,
    )

# DB-Hygiene: alte Jobs raus, uralte Pending-Items automatisch skippen.
# Idempotent + günstig - läuft bei jedem Restart einmal.
_jobs_purged = _db.cleanup_old_jobs(days=90)
if _jobs_purged:
    logger.info(f"DB-Cleanup: {_jobs_purged} Job-Einträge älter 90 Tage gelöscht")
# Vor dem Auto-Skip zugehörige Stash-Dateien sicher entfernen.
_old_pending = _db.pending_older_than(days=30, status="pending")
for _item in _old_pending:
    _p = _item.get("video_path")
    if not _p:
        continue
    try:
        _resolved = Path(_p).resolve()
        _temp_root = Path(get_config().get("paths", "temp_dir", default="/opt/scrapper/temp")).resolve()
        _resolved.relative_to(_temp_root)
        _resolved.unlink(missing_ok=True)
    except (OSError, ValueError):
        logger.warning("Alte Pending-Datei außerhalb temp_dir oder nicht löschbar: %s", _p)
_pending_skipped = _db.auto_skip_old_pending(days=30)
if _pending_skipped:
    logger.info(f"DB-Cleanup: {_pending_skipped} Pending-Items älter 30 Tage auf 'auto_skipped'")
_pending_purged = _db.cleanup_old_pending(days=180)
if _pending_purged:
    logger.info(f"DB-Cleanup: {_pending_purged} erledigte Pending-Einträge älter 180 Tage gelöscht")

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


@asynccontextmanager
async def _lifespan(app):
    # READY=1 sobald der App-Startup durch ist (DB-Pings, Routes registriert).
    # systemd wartet dann auf dieses Signal bevor 'systemctl start' returnt -
    # damit ist ein 'restart' ohne 502-Lücke am Reverse-Proxy möglich.
    _sd_notify("READY=1")
    logger.info("App ready (sd_notify READY=1 sent)")
    try:
        yield
    finally:
        _sd_notify("STOPPING=1")


# -------- FastAPI --------
# Docs nur aktiv wenn explizit angefragt (Default: aus für Production).
_enable_docs = os.getenv("SCRAPPER_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Rezeptliebe",
    version="1.2.0",
    docs_url="/api/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if _enable_docs else None,
    lifespan=_lifespan,
)

app.add_middleware(SameOriginMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Statisch (Frontend)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# API-Routen
app.include_router(api_config.router)
app.include_router(api_jobs.router)
app.include_router(api_pending.router)
app.include_router(api_recipes.router)
app.include_router(api_history.router)
app.include_router(api_test.router)
app.include_router(api_browse.router)
app.include_router(api_schedule.router)
app.include_router(api_metrics.router)
app.include_router(api_stats.router)
app.include_router(api_hdd.router)
app.include_router(api_events.router)


# -------- Cookie-Helper --------
def _set_session_cookie(resp, token: str, request: Request) -> None:
    is_https = request.url.scheme == "https"
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
<meta charset="UTF-8"><title>Login · Rezeptliebe</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f7cf63">
<meta name="color-scheme" content="light">
<link rel="stylesheet" href="/static/rezeptliebe.css?v=2026-07-11-2">
</head><body class="login-body">
<form method="post" action="/login" class="login-card" aria-labelledby="login-title">
  <div class="login-brand"><div class="login-brand-mark"><svg class="brand-chef-icon" viewBox="0 0 48 48" aria-hidden="true"><path d="M14.5 22.5a9 9 0 0 1 3.8-17.2A10.5 10.5 0 0 1 37 11.9a8 8 0 0 1-2.4 15.4V39a3 3 0 0 1-3 3H16.4a3 3 0 0 1-3-3V27.3a8 8 0 0 1 1.1-4.8Zm3.7 3.1V37h11.6V25.6l2-.7a3.4 3.4 0 0 0-1.1-6.6h-1.8l-.2-1.8a5.9 5.9 0 0 0-11.4-1.2l-.7 2-2.1-.3a4.4 4.4 0 0 0-1.2 8.7l2 .3v6h2.9v-6.4Zm0 13.5h11.6v-2.8H18.2v2.8Z"/></svg></div><div class="login-brand-copy"><strong>Rezeptliebe</strong><span>Meine Rezeptbibliothek</span></div></div>
  <h1 id="login-title">Willkommen zurück</h1>
  <p class="muted">Anmelden, um Rezepte zu suchen und neue Inhalte zu importieren.</p>
  {error}
  <input type="hidden" name="next" value="{next}">
  <label>Benutzername<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
  <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Anmelden</button>
</form>
</body></html>
"""


def _safe_next(value: str) -> str:
    """Open-Redirect- und Header-Injection-Schutz: nur lokale Pfade."""
    value = str(value or "")[:2048]
    if any(ord(ch) < 32 for ch in value):
        return "/"
    # Backslashes verbieten: Browser normalisieren '\' zu '/' - aus '/\evil.com'
    # würde sonst '//evil.com' (scheme-relative Redirect auf fremde Domain).
    if "\\" in value:
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _login_html(*, error: str = "", next_path: str = "/") -> str:
    return LOGIN_HTML.format(error=error, next=html.escape(_safe_next(next_path), quote=True))


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/"):
    return _login_html(next_path=next)


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
            _login_html(
                error=f'<p class="error">⛔ Zu viele Fehlversuche. '
                      f'Erneut probieren in {remaining // 60 + 1} min.</p>',
                next_path=next,
            ),
            status_code=429,
        )

    if not check_credentials(username, password):
        login_limiter.record_fail(ip)
        logger.warning(f"Fehl-Login von {ip} (user={username!r})")
        return HTMLResponse(
            _login_html(
                error='<p class="error">❌ Login fehlgeschlagen</p>',
                next_path=next,
            ),
            status_code=401,
        )

    login_limiter.record_success(ip)
    token = create_session(username)
    resp = RedirectResponse(url=_safe_next(next), status_code=303)
    _set_session_cookie(resp, token, request)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# -------- Home (geschützt) --------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not verify_session(token):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


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
        return {"ok": True}
    except Exception as e:
        logger.error(f"healthz failed: {e}")
        return JSONResponse({"ok": False}, status_code=503)


@app.get("/healthz/deep", dependencies=[Depends(require_auth)])
def healthz_deep():
    """Tiefer Check: DB + Ollama + IMAP + freier Speicher.
    Status-Code immer 200, Details im Body. Wir wollen nicht dass eine
    kaputte IMAP-Config den ganzen Container als 'unhealthy' markiert."""
    import shutil
    from .config_store import get_config

    checks = {}
    cfg = get_config()

    # DB
    try:
        with get_db().conn() as c:
            c.execute("SELECT 1").fetchone()
        checks["db"] = {"ok": True}
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)}

    # Ollama
    try:
        from .core.analyzer import OllamaAnalyzer
        ollama_cfg = cfg.get("ai", "ollama", default={}) or {}
        if ollama_cfg.get("enabled", True):
            o = OllamaAnalyzer(
                ollama_cfg.get("url", ""),
                ollama_cfg.get("model", ""),
                timeout=5,
            )
            checks["ollama"] = {"ok": o.health(), "model": ollama_cfg.get("model")}
        else:
            checks["ollama"] = {"ok": True, "disabled": True}
    except Exception as e:
        checks["ollama"] = {"ok": False, "error": str(e)}

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
