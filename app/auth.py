"""
Session-Auth mit Bcrypt-Passwort-Hashing.
Single-User-System: Username/Password aus Config, Session-Cookie signiert.

Migration: alte Klartext-Passwörter werden beim Erststart automatisch gehasht.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config_store import get_config

logger = logging.getLogger(__name__)

SESSION_COOKIE = "scrapper_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 Tage
BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
PREHASH_PREFIX = "$scrapper-bcrypt-sha256$"
DEFAULT_SECRETS = (
    "",
    "please-change-me",
    "change-this-to-random-string-32chars-min",
)


# -------------------- Passwort-Hashing --------------------
def _bcrypt_input(plain: str) -> tuple[bytes, bool]:
    """Bereitet Passwörter für bcrypt vor.

    bcrypt 5 lehnt Eingaben über 72 Byte ab. Für längere Passphrasen wird
    deshalb ein domänenseparierter SHA-256-Digest verwendet und das Format im
    gespeicherten Hash markiert. Bestehende normale bcrypt-Hashes bleiben
    vollständig kompatibel.
    """
    raw = str(plain).encode("utf-8")
    if len(raw) <= 72:
        return raw, False
    digest = hashlib.sha256(b"scrapper-password-v1\0" + raw).digest()
    return digest, True


def hash_password(plain: str) -> str:
    prepared, prehashed = _bcrypt_input(plain)
    encoded = bcrypt.hashpw(prepared, bcrypt.gensalt(rounds=12)).decode("ascii")
    return PREHASH_PREFIX + encoded if prehashed else encoded


def is_hashed(value: str) -> bool:
    return isinstance(value, str) and (
        value.startswith(BCRYPT_PREFIXES) or value.startswith(PREHASH_PREFIX)
    )


def verify_password(plain: str, stored: str) -> bool:
    """Akzeptiert bcrypt-Hashes, lange Passphrasen und Legacy-Klartext."""
    if not plain or not stored:
        return False
    if stored.startswith(PREHASH_PREFIX):
        try:
            prepared = hashlib.sha256(
                b"scrapper-password-v1\0" + plain.encode("utf-8")
            ).digest()
            encoded = stored[len(PREHASH_PREFIX):].encode("ascii")
            return bcrypt.checkpw(prepared, encoded)
        except (ValueError, TypeError, UnicodeError):
            return False
    if stored.startswith(BCRYPT_PREFIXES):
        try:
            # Alte bcrypt-Hashes wurden direkt aus dem Passwort erzeugt und
            # sind daher nur bis zur bcrypt-Grenze verifizierbar.
            raw = plain.encode("utf-8")
            if len(raw) > 72:
                return False
            return bcrypt.checkpw(raw, stored.encode("ascii"))
        except (ValueError, TypeError, UnicodeError):
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
    3. Generiert einen privaten Metrics-Token, falls nur der Platzhalter aktiv ist.
    4. Verweigert Start bei aktiver Default-Kombi 'admin/changeme'.
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

    # 3) Metrics-Token (bekannter Beispielwert darf nie produktiv bleiben)
    metrics_token = str(cfg.get("monitoring", "metrics_token", default="") or "")
    if metrics_token in {"", "change-this-metrics-token", "please-change-me"} or len(metrics_token) < 24:
        cfg.set("monitoring", "metrics_token", secrets.token_urlsafe(36))
        changed = True
        logger.warning("monitoring.metrics_token war fehlend/default - neuer wurde generiert.")

    # 4) Default-Login verbieten. Sicherheitsmigrationen werden vorher
    # persistiert, damit die Config auch bei absichtlich blockiertem Start
    # bereits gehasht, tokenisiert und chmod 0600 ist.
    user = str(cfg.get("web", "username", default="admin"))
    stored_pw = cfg.get("web", "password", default="") or ""
    default_login_active = user == "admin" and verify_password("changeme", stored_pw)

    if changed:
        cfg.save()

    if default_login_active:
        raise RuntimeError(
            "❌ Default-Login 'admin/changeme' ist aktiv. "
            "Setze ein eigenes Passwort, z.B. mit:  "
            "python -m app.cli set-password"
        )

    cleanup_initial_password_file()


def cleanup_initial_password_file() -> None:
    """Entfernt das Installer-Passwort, sobald es nicht mehr aktuell ist."""
    cfg = get_config()
    marker = Path(cfg.path).parent / ".initial-password"
    if not marker.is_file():
        return
    try:
        initial = marker.read_text(encoding="utf-8").strip()
        stored = str(cfg.get("web", "password", default="") or "")
        if not initial or not verify_password(initial, stored):
            marker.unlink(missing_ok=True)
            logger.info("Veraltete .initial-password-Datei wurde entfernt.")
    except OSError as exc:
        logger.warning(".initial-password konnte nicht geprüft/entfernt werden: %s", exc)


# -------------------- Session --------------------
def _serializer() -> URLSafeTimedSerializer:
    secret = get_config().get("web", "secret_key", default="") or ""
    if not secret or len(secret) < 32:
        raise RuntimeError("web.secret_key fehlt - App neustarten für Auto-Migration.")
    return URLSafeTimedSerializer(secret, salt="scrapper-auth")


def _credential_fingerprint() -> str:
    """Fingerprint der aktuellen Zugangsdaten.

    Ändert sich Benutzername oder Passwort-Hash, werden bestehende Sessions
    automatisch ungültig, ohne dass der globale Session-Secret rotiert werden muss.
    """
    cfg = get_config()
    username = str(cfg.get("web", "username", default="admin") or "")
    password = str(cfg.get("web", "password", default="") or "")
    return hashlib.sha256(f"{username}\0{password}".encode("utf-8")).hexdigest()


def create_session(username: str) -> str:
    return _serializer().dumps({
        "user": username,
        "cred": _credential_fingerprint(),
        "nonce": secrets.token_hex(8),
    })


def verify_session(token: str) -> bool:
    try:
        payload = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        if not isinstance(payload, dict):
            return False
        cfg_user = str(get_config().get("web", "username", default="admin") or "")
        user_ok = hmac.compare_digest(str(payload.get("user") or ""), cfg_user)
        cred_ok = hmac.compare_digest(
            str(payload.get("cred") or ""), _credential_fingerprint()
        )
        return user_ok and cred_ok
    except (BadSignature, SignatureExpired, TypeError, ValueError):
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
