"""
Session-Auth mit Bcrypt-Passwort-Hashing.
Single-User-System: Username/Password aus Config, Session-Cookie signiert.

Migration: alte Klartext-Passwörter werden beim Erststart automatisch gehasht.
"""
from __future__ import annotations

import hmac
import logging
import secrets

import bcrypt
from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config_store import get_config

logger = logging.getLogger(__name__)

SESSION_COOKIE = "scrapper_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 Tage
BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
DEFAULT_SECRETS = (
    "",
    "please-change-me",
    "change-this-to-random-string-32chars-min",
)


# -------------------- Passwort-Hashing --------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def is_hashed(value: str) -> bool:
    return isinstance(value, str) and value.startswith(BCRYPT_PREFIXES)


def verify_password(plain: str, stored: str) -> bool:
    """Akzeptiert bcrypt-Hashes ODER (für Migration) Klartext."""
    if not plain or not stored:
        return False
    if is_hashed(stored):
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("ascii"))
        except (ValueError, TypeError):
            return False
    # Legacy-Pfad: Klartext, timing-safe. Wird via migrate_security() gehasht.
    return hmac.compare_digest(plain.encode("utf-8"), str(stored).encode("utf-8"))


def check_credentials(username: str, password: str) -> bool:
    cfg = get_config()
    cfg_u = str(cfg.get("web", "username", default="admin"))
    cfg_p = cfg.get("web", "password", default="") or ""
    user_ok = hmac.compare_digest(str(username).encode("utf-8"), cfg_u.encode("utf-8"))
    pass_ok = verify_password(password, cfg_p)
    # Beide checken (kein Early-Return), damit kein Timing-Leak zw. User-/Pwd-Fehler
    return user_ok and pass_ok


# -------------------- Startup-Migration --------------------
def migrate_security() -> None:
    """Beim App-Start aufrufen.

    1. Generiert/erneuert ``web.secret_key`` falls fehlend/default/zu kurz.
    2. Hasht Klartext-Passwort in der Config.
    3. Verweigert Start bei aktiver Default-Kombi 'admin/changeme'.
    """
    cfg = get_config()
    changed = False

    # 1) Secret-Key
    secret = cfg.get("web", "secret_key", default="") or ""
    if secret in DEFAULT_SECRETS or len(secret) < 32:
        cfg.set("web", "secret_key", secrets.token_urlsafe(48))
        changed = True
        logger.warning("web.secret_key war fehlend/default - neuer wurde generiert.")

    # 2) Passwort hashen
    pw = cfg.get("web", "password", default="") or ""
    if pw and not is_hashed(pw):
        cfg.set("web", "password", hash_password(pw))
        changed = True
        logger.warning("web.password war Klartext - wurde bcrypt-gehasht.")

    # 3) Default-Login verbieten
    user = str(cfg.get("web", "username", default="admin"))
    stored_pw = cfg.get("web", "password", default="") or ""
    if user == "admin" and verify_password("changeme", stored_pw):
        raise RuntimeError(
            "❌ Default-Login 'admin/changeme' ist aktiv. "
            "Setze ein eigenes Passwort, z.B. mit:  "
            "python -m app.cli set-password"
        )

    if changed:
        cfg.save()


# -------------------- Session --------------------
def _serializer() -> URLSafeTimedSerializer:
    secret = get_config().get("web", "secret_key", default="") or ""
    if not secret or len(secret) < 32:
        raise RuntimeError("web.secret_key fehlt - App neustarten für Auto-Migration.")
    return URLSafeTimedSerializer(secret, salt="scrapper-auth")


def create_session(username: str) -> str:
    return _serializer().dumps({"user": username})


def verify_session(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_auth(request: Request) -> None:
    is_api = request.url.path.startswith("/api/")
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not verify_session(token):
        if is_api:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={request.url.path}"},
        )
