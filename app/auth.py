"""
Session-Auth mit Bcrypt-Passwort-Hashing.
Single-User-System: Username/Password aus Config, Session-Cookie signiert.

Migration: alte Klartext-Passwörter werden beim Erststart automatisch gehasht.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from typing import Optional

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
    """Prüft Login. Ablauf:
      1. DB-User suchen (Multi-User-Pfad). Bei Match und !disabled → ok.
      2. Fallback auf config.web.{username,password} (Backwards-Compat für
         frische Installs ohne DB-Migration und für vor-Migrations-State).
    last_login_at wird beim erfolgreichen Match aktualisiert."""
    from .db import get_db
    db = get_db()
    user_row = db.user_get_by_name(username)
    if user_row:
        if user_row.get("disabled"):
            return False
        if verify_password(password, user_row["password_hash"]):
            try:
                db.user_update_last_login(int(user_row["id"]))
            except Exception:
                pass  # Login darf nicht failen weil last_login_at-update bricht
            return True
        return False

    # Config-Fallback: nur greift wenn DB komplett leer ist (typisch vor
    # erster Migration). Nach migrate_users_to_db() ist immer mindestens
    # ein admin in der DB — dann läuft alles über den DB-Pfad.
    cfg = get_config()
    cfg_u = str(cfg.get("web", "username", default="admin"))
    cfg_p = cfg.get("web", "password", default="") or ""
    user_ok = hmac.compare_digest(str(username).encode("utf-8"), cfg_u.encode("utf-8"))
    pass_ok = verify_password(password, cfg_p)
    return user_ok and pass_ok


def migrate_users_to_db() -> None:
    """Übernimmt den config-web-User als initialen Benutzer in die users-Tabelle.
    Idempotent: läuft nur wenn die users-Tabelle leer ist. Rollen werden nur
    noch aus Gründen der Datenbank-Kompatibilität gespeichert und nicht für
    Berechtigungen ausgewertet."""
    from .db import get_db
    db = get_db()
    with db.conn() as c:
        existing = int(c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    if existing > 0:
        return
    cfg = get_config()
    username = str(cfg.get("web", "username", default="admin"))
    pw_hash = cfg.get("web", "password", default="") or ""
    if not username or not pw_hash or not is_hashed(pw_hash):
        logger.warning(
            "migrate_users_to_db: config.web.password ist nicht gehasht oder "
            "leer — überspringe. Benutzer-Verwaltung wird erst nach manuellem "
            "Anlegen funktionieren."
        )
        return
    db.user_create(username, pw_hash, role="user")
    logger.info(f"Initialer Benutzer '{username}' aus Config in users-DB migriert.")



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


def session_user(token: str) -> Optional[str]:
    """Returnt username aus gültiger Session, sonst None.
    Eine Schicht über verify_session() — der Caller braucht oft den User-Namen,
    nicht nur den 'valid yes/no'-Status (z.B. für require_admin)."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        u = data.get("user")
        return str(u) if u else None
    except (BadSignature, SignatureExpired):
        return None


def verify_session(token: str) -> bool:
    return session_user(token) is not None


def auth_disabled() -> bool:
    """Login-Abfrage per Config abschaltbar (web.auth_disabled: true).

    SICHERHEIT: Damit ist die App für JEDEN erreichbar, der sie netzwerkseitig
    sieht — inkl. Löschen von Rezepten und Config-Zugriff. Nur vertretbar,
    wenn davor eine eigene Zugriffskontrolle liegt (z.B. Cloudflare Access
    auf dem öffentlichen Hostname) und das LAN vertrauenswürdig ist.
    """
    try:
        return bool((get_config().get("web") or {}).get("auth_disabled", False))
    except Exception:
        return False


async def require_auth(request: Request) -> None:
    if auth_disabled():
        return
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


async def require_admin(request: Request) -> dict:
    """Kompatibilitäts-Dependency für frühere Admin-Endpunkte.

    Seit v1.2.2 gibt es keine Admin-Rollen mehr: Jeder aktive, angemeldete
    Benutzer hat vollständigen Zugriff. Der Funktionsname bleibt nur erhalten,
    damit bestehende Router und Erweiterungen kompatibel bleiben.
    """
    if auth_disabled():
        return {"username": "local", "disabled": False, "full_access": True}

    token = request.cookies.get(SESSION_COOKIE, "")
    username = session_user(token)
    if not username:
        raise HTTPException(401, "Authentication required")

    from .db import get_db
    user = get_db().user_get_by_name(username)
    if user:
        if user.get("disabled"):
            raise HTTPException(403, "Benutzerkonto ist deaktiviert")
        return {**user, "full_access": True}

    # Legacy-Fallback für Installationen, die noch ausschließlich den
    # config.web-Benutzer verwenden.
    cfg = get_config()
    config_user = str(cfg.get("web", "username", default="admin"))
    if username == config_user:
        return {"username": username, "disabled": False,
                "legacy_config_user": True, "full_access": True}

    raise HTTPException(401, "Authentication required")
