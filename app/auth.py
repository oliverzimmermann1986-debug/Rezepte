"""Session-Auth mit Bcrypt-Passwort-Hashing und rollenbasiertem Zugriff.

Migration: alte Klartext-Passwörter werden beim Erststart automatisch gehasht.
Der frühere Config-Benutzer wird als initialer Administrator übernommen.
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
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_GUEST = "guest"
GUEST_USERNAME = "Gast"
VALID_ROLES = frozenset({ROLE_USER, ROLE_ADMIN})
SAFE_SESSION_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DUMMY_PASSWORD_HASH = "$2b$12$tHqkjQG/5uUOLxPxh766ku3u8CNZ6YprzbSzD8uyU7ZB04RLAt1m2"


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
            verify_password(password, _DUMMY_PASSWORD_HASH)
            return False
        if verify_password(password, user_row["password_hash"]):
            try:
                db.user_update_last_login(int(user_row["id"]))
            except Exception:
                pass  # Login darf nicht failen weil last_login_at-update bricht
            return True
        return False

    # Auch unbekannte Namen durchlaufen genau einen bcrypt-Check. Damit ist
    # die Existenz eines aktiven Kontos nicht über einen groben Timing-Sprung
    # am Login-Endpunkt erkennbar.
    verify_password(password, _DUMMY_PASSWORD_HASH)

    # Config-Fallback: nur greift wenn DB komplett leer ist (typisch vor
    # erster Migration). Nach migrate_users_to_db() ist immer mindestens
    # ein admin in der DB — dann läuft alles über den DB-Pfad.
    with db.conn() as c:
        if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return False
    cfg = get_config()
    cfg_u = str(cfg.get("web", "username", default="admin"))
    cfg_p = cfg.get("web", "password", default="") or ""
    user_ok = hmac.compare_digest(str(username).encode("utf-8"), cfg_u.encode("utf-8"))
    pass_ok = verify_password(password, cfg_p)
    return user_ok and pass_ok


def migrate_users_to_db() -> None:
    """Übernimmt den Config-Benutzer als initialen Administrator.

    Frühere Versionen speicherten zwar eine Rolle, werteten sie aber nicht aus
    und legten den ersten Benutzer als ``user`` an. Damit ein Upgrade den
    bestehenden Betreiber nicht aussperrt, wird bei Installationen ohne aktiven
    Administrator einmalig der Config-Benutzer (oder ersatzweise der älteste
    aktive Benutzer) zum Administrator hochgestuft. Der Ablauf ist idempotent.
    """
    from .db import get_db
    db = get_db()
    with db.conn() as c:
        existing = int(c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    cfg = get_config()
    config_username = str(cfg.get("web", "username", default="admin")).strip()

    if existing > 0:
        promoted = db.user_ensure_initial_admin(config_username)
        if promoted:
            logger.warning(
                "Keine aktive Admin-Rolle vorhanden; '%s' wurde für ein "
                "rückwärtskompatibles Upgrade zum Administrator hochgestuft.",
                promoted,
            )
        return
    username = config_username
    pw_hash = cfg.get("web", "password", default="") or ""
    if not username or not pw_hash or not is_hashed(pw_hash):
        logger.warning(
            "migrate_users_to_db: config.web.password ist nicht gehasht oder "
            "leer — überspringe. Benutzer-Verwaltung wird erst nach manuellem "
            "Anlegen funktionieren."
        )
        return
    db.user_create(username, pw_hash, role=ROLE_ADMIN)
    logger.info("Initialer Administrator '%s' aus Config in users-DB migriert.", username)



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
    """Erstellt eine widerrufbare Session.

    DB-Benutzer tragen ihre aktuelle ``session_version`` im Token. Ein
    Passwortwechsel oder eine Aktivstatusänderung erhöht die Version und macht
    damit alle vorherigen Cookies sofort ungültig. Der Legacy-Config-Benutzer
    bleibt für noch nicht migrierte Installationen kompatibel.
    """
    from .db import get_db

    user = get_db().user_get_by_name(username)
    if user:
        if user.get("disabled"):
            raise ValueError("Benutzerkonto ist deaktiviert")
        payload = {
            "user": str(user["username"]),
            "uid": int(user["id"]),
            "ver": int(user.get("session_version") or 0),
        }
    else:
        db = get_db()
        with db.conn() as c:
            if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Unbekannter Benutzer")
        cfg = get_config()
        cfg_user = str(cfg.get("web", "username", default="admin"))
        if not hmac.compare_digest(str(username), cfg_user):
            raise ValueError("Unbekannter Benutzer")
        payload = {
            "user": cfg_user,
            "legacy": True,
            "ver": int(cfg.get("web", "session_version", default=0) or 0),
        }
    return _serializer().dumps(payload)


def create_guest_session() -> str:
    """Erstellt eine zustandslose, strikt schreibgeschützte Gastsitzung.

    Gäste sind absichtlich keine DB-Benutzer. Das signierte ``guest``-Merkmal
    trennt sie eindeutig vom Legacy-Config-Fallback und kann daher niemals
    Administratorrechte erben.
    """
    return _serializer().dumps({"user": GUEST_USERNAME, "guest": True})


def _session_payload(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data if isinstance(data, dict) else None
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None


def session_is_guest(token: str) -> bool:
    """Prüft das signierte Gastmerkmal ohne einen DB-Benutzer anzulegen."""
    data = _session_payload(token)
    return bool(
        data
        and data.get("guest") is True
        and hmac.compare_digest(str(data.get("user") or ""), GUEST_USERNAME)
    )


def session_user(token: str) -> Optional[str]:
    """Returnt username aus gültiger Session, sonst None.
    Eine Schicht über verify_session() — der Caller braucht oft den User-Namen,
    nicht nur den 'valid yes/no'-Status (z.B. für require_admin).

    Signatur und Alter reichen nicht: Der aktuelle Benutzerzustand wird bei
    jedem Request aus der DB gelesen, damit Sperre, Löschung und Passwortwechsel
    bestehende Cookies unmittelbar invalidieren.
    """
    data = _session_payload(token)
    if data is None:
        return None
    try:
        u = data.get("user")
        if not u:
            return None

        username = str(u)
        if data.get("guest") is True:
            return GUEST_USERNAME if session_is_guest(token) else None

        from .db import get_db
        user = get_db().user_get_by_name(username)
        if user:
            if user.get("disabled"):
                return None
            token_version = data.get("ver")
            token_user_id = data.get("uid")
            if token_version is None or token_user_id is None:
                return None
            try:
                if int(token_version) != int(user.get("session_version") or 0):
                    return None
                if int(token_user_id) != int(user["id"]):
                    return None
            except (TypeError, ValueError):
                return None
            return str(user["username"])

        # Nur vor der ersten Benutzer-Migration zulassen. Sobald mindestens ein
        # DB-User existiert, darf ein gelöschter Benutzer nicht auf den
        # Config-Fallback zurückfallen.
        with get_db().conn() as c:
            has_db_users = bool(c.execute("SELECT 1 FROM users LIMIT 1").fetchone())
        if has_db_users or not data.get("legacy"):
            return None
        cfg = get_config()
        cfg_user = str(cfg.get("web", "username", default="admin"))
        try:
            version_ok = int(data.get("ver", -1)) == int(
                cfg.get("web", "session_version", default=0) or 0
            )
        except (TypeError, ValueError):
            version_ok = False
        return (
            cfg_user
            if version_ok and hmac.compare_digest(username, cfg_user)
            else None
        )
    except (TypeError, ValueError):
        return None
    except Exception:
        # Auth-Prüfungen fail-closed. Details nur serverseitig loggen.
        logger.exception("Session konnte nicht gegen Benutzerstatus geprüft werden")
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
    if request_is_guest(request) and request.method.upper() not in SAFE_SESSION_METHODS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Der Gastzugang ist schreibgeschützt.",
        )
    if auth_disabled():
        from .security import request_is_from_trusted_proxy
        if request_is_from_trusted_proxy(request):
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unsichere direkte Verbindung")
    is_api = request.url.path.startswith("/api/")
    if not request_user(request):
        if is_api:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={request.url.path}"},
        )


def request_user(request: Request) -> Optional[str]:
    """Liest eine Sitzung aus HttpOnly-Cookie oder Bearer-Header."""
    token = _request_token(request)
    if session_is_guest(token):
        return GUEST_USERNAME
    if auth_disabled():
        from .security import request_is_from_trusted_proxy
        return "local" if request_is_from_trusted_proxy(request) else None
    return session_user(token)


def _request_token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE, "")
    authorization = getattr(request, "headers", {}).get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return token


def request_is_guest(request: Request) -> bool:
    return session_is_guest(_request_token(request))


async def require_admin(request: Request) -> dict:
    """Verlangt eine aktive Sitzung mit der Rolle ``admin``.

    Im expliziten ``auth_disabled``-Betrieb bleibt der lokale/Cloudflare-
    geschützte Kompatibilitätsbenutzer Administrator. Eine noch nicht in die
    Datenbank migrierte Config-Sitzung wird ebenfalls als Legacy-Admin
    akzeptiert, damit ein Upgrade den Betreiber nicht aussperrt.
    """
    if request_is_guest(request):
        raise HTTPException(403, "Der Gastzugang ist schreibgeschützt.")

    if auth_disabled():
        from .security import request_is_from_trusted_proxy
        if not request_is_from_trusted_proxy(request):
            raise HTTPException(403, "Unsichere direkte Verbindung")
        return {
            "username": "local",
            "role": ROLE_ADMIN,
            "disabled": False,
            "full_access": True,
        }

    username = request_user(request)
    if not username:
        raise HTTPException(401, "Authentication required")

    from .db import get_db
    user = get_db().user_get_by_name(username)
    if user:
        if user.get("disabled"):
            raise HTTPException(403, "Benutzerkonto ist deaktiviert")
        if user.get("role") != ROLE_ADMIN:
            raise HTTPException(403, "Administratorrechte erforderlich")
        return {**user, "full_access": True}

    # Legacy-Fallback für Installationen, die noch ausschließlich den
    # config.web-Benutzer verwenden.
    cfg = get_config()
    config_user = str(cfg.get("web", "username", default="admin"))
    with get_db().conn() as c:
        has_db_users = bool(c.execute("SELECT 1 FROM users LIMIT 1").fetchone())
    if not has_db_users and hmac.compare_digest(username, config_user):
        return {
            "username": username,
            "role": ROLE_ADMIN,
            "disabled": False,
            "legacy_config_user": True,
            "full_access": True,
        }

    raise HTTPException(401, "Authentication required")
