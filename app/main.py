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

from .auth import (SESSION_COOKIE, SESSION_MAX_AGE, check_credentials,
                    create_session, migrate_security, verify_session)
from .config_store import get_config
from .db import get_db
from .routes import (api_browse, api_config, api_history, api_jobs,
                     api_metrics, api_pending, api_schedule, api_test)
from .security import SecurityHeadersMiddleware, client_ip, login_limiter

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

# DB initialisieren + Stale-Running-Jobs vom letzten Crash/Restart aufräumen
_db = get_db()
_stale = _db.reset_stale_running()
if _stale:
    logger.warning(f"{_stale} Job(s) waren als 'running' markiert - auf 'error' gesetzt (Crash/Restart-Recovery)")

# DB-Hygiene: alte Jobs raus, uralte Pending-Items automatisch skippen.
# Idempotent + günstig - läuft bei jedem Restart einmal.
_jobs_purged = _db.cleanup_old_jobs(days=90)
if _jobs_purged:
    logger.info(f"DB-Cleanup: {_jobs_purged} Job-Einträge älter 90 Tage gelöscht")
_pending_skipped = _db.auto_skip_old_pending(days=30)
if _pending_skipped:
    logger.info(f"DB-Cleanup: {_pending_skipped} Pending-Items älter 30 Tage auf 'auto_skipped'")

# -------- FastAPI --------
# Docs nur aktiv wenn explizit angefragt (Default: aus für Production).
_enable_docs = os.getenv("SCRAPPER_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Scrapper Manager",
    version="1.0.0",
    docs_url="/api/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if _enable_docs else None,
)

app.add_middleware(SecurityHeadersMiddleware)

# Statisch (Frontend)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# API-Routen
app.include_router(api_config.router)
app.include_router(api_jobs.router)
app.include_router(api_pending.router)
app.include_router(api_history.router)
app.include_router(api_test.router)
app.include_router(api_browse.router)
app.include_router(api_schedule.router)
app.include_router(api_metrics.router)


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
<meta charset="UTF-8"><title>Login · Scrapper</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/style.css">
</head><body class="login-body">
<form method="post" action="/login" class="login-card">
  <h1>Scrapper</h1>
  <p class="muted">Bitte anmelden</p>
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
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/healthz/deep")
def healthz_deep():
    """Tiefer Check: DB + Ollama + IMAP + Disk-Space + rclone-Config.
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

    # rclone-Config lesbar
    try:
        import subprocess
        r = subprocess.run(["rclone", "listremotes"],
                            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            remotes = [x.strip(":") for x in r.stdout.split() if x.strip()]
            checks["rclone"] = {"ok": True, "remotes": remotes}
        else:
            checks["rclone"] = {"ok": False, "error": r.stderr.strip()[:200]}
    except FileNotFoundError:
        checks["rclone"] = {"ok": False, "error": "rclone binary not found"}
    except Exception as e:
        checks["rclone"] = {"ok": False, "error": str(e)}

    overall = all(v.get("ok", False) for v in checks.values())
    return {"ok": overall, "checks": checks}
