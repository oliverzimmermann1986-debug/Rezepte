"""
Scrapper Web-Server.
Startet mit:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (SESSION_COOKIE, SESSION_MAX_AGE, check_credentials,
                    create_session, verify_session)
from .config_store import get_config
from .db import get_db
from .routes import api_config, api_history, api_jobs, api_pending, api_test

# Logging Setup
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

# DB initialisieren
get_db()

app = FastAPI(
    title="Scrapper Manager",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Statisch (Frontend)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# API Routen
app.include_router(api_config.router)
app.include_router(api_jobs.router)
app.include_router(api_pending.router)
app.include_router(api_history.router)
app.include_router(api_test.router)


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


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/"):
    return LOGIN_HTML.format(error="", next=next)


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if not check_credentials(username, password):
        return HTMLResponse(
            LOGIN_HTML.format(
                error='<p class="error">❌ Login fehlgeschlagen</p>',
                next=next,
            ),
            status_code=401,
        )
    token = create_session(username)
    resp = RedirectResponse(url=next or "/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# -------- Home (geschützt) --------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not verify_session(token):
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


# -------- Exception Handler für 303 (Redirect aus require_auth) --------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == status.HTTP_303_SEE_OTHER and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    # Default JSON
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
