"""Server-side Apple/Google identity verification and single-use login handoffs.

Provider credentials stay in the server environment. Email is display metadata,
never an identity key and never grounds for linking an existing account.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from .config_store import get_config

NATIVE_CALLBACK = "de.mausbaeren.rezepte://auth/callback"
FLOW_TTL = 600
EXCHANGE_TTL = 60
PROVIDERS = {
    "apple": {"name": "Apple", "authorize": "https://appleid.apple.com/auth/authorize",
              "token": "https://appleid.apple.com/auth/token", "jwks": "https://appleid.apple.com/auth/keys",
              "revoke": "https://appleid.apple.com/auth/revoke", "issuers": ["https://appleid.apple.com"]},
    "google": {"name": "Google", "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
               "token": "https://oauth2.googleapis.com/token", "jwks": "https://www.googleapis.com/oauth2/v3/certs",
               "revoke": "https://oauth2.googleapis.com/revoke", "issuers": ["https://accounts.google.com", "accounts.google.com"]},
}
_keys: dict = {}
_key_lock = threading.Lock()


def migrate_oidc(c) -> None:
    if c.execute("SELECT 1 FROM schema_migrations WHERE version=268").fetchone():
        return
    c.execute("""CREATE TABLE IF NOT EXISTS oidc_identities (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider TEXT NOT NULL CHECK(provider IN ('apple','google')), subject TEXT NOT NULL,
        email TEXT, token_secret TEXT, linked_at REAL NOT NULL,
        UNIQUE(provider,subject), UNIQUE(user_id,provider))""")
    c.execute("CREATE TABLE IF NOT EXISTS oidc_flows (state_hash TEXT PRIMARY KEY,data_secret TEXT NOT NULL,expires_at REAL NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS oidc_exchanges (code_hash TEXT PRIMARY KEY,data_secret TEXT NOT NULL,challenge TEXT NOT NULL,expires_at REAL NOT NULL)")
    c.execute("""CREATE TABLE IF NOT EXISTS oidc_revocations (
        id INTEGER PRIMARY KEY,provider TEXT NOT NULL,token_secret TEXT NOT NULL,
        created_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL)""")
    c.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(268,?,?)",
              ("provider_identities_and_single_use_auth_flows", time.time()))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _cipher() -> Fernet:
    secret = str(get_config().get("web", "secret_key", default="") or "")
    if len(secret) < 32:
        raise HTTPException(503, "Die Server-Anmeldung ist noch nicht eingerichtet")
    key = hashlib.sha256(("rezepte-oidc-v1:" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(data: dict) -> str:
    return _cipher().encrypt(json.dumps(data).encode()).decode()


def _unseal(secret: str) -> dict:
    try:
        return json.loads(_cipher().decrypt(secret.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(400, "Diese Anmeldung ist abgelaufen. Bitte erneut beginnen") from None


def public_url() -> str:
    value = os.environ.get("REZEPTE_PUBLIC_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "Die externe Anmeldung ist noch nicht eingerichtet")
    return value


def configuration(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unbekannter Anmeldeanbieter")
    prefix = "REZEPTE_" + provider.upper() + "_"
    values = {key: os.environ.get(prefix + key.upper(), "").strip()
              for key in ("client_id", "client_secret", "team_id", "key_id", "private_key_path")}
    values["public_url"] = public_url()
    required = ["client_id", "client_secret"] if provider == "google" else [
        "client_id", "team_id", "key_id", "private_key_path"]
    if not all(values[key] for key in required):
        raise HTTPException(503, "Dieser Anmeldeanbieter ist noch nicht eingerichtet")
    if provider == "apple" and not Path(values["private_key_path"]).is_file():
        raise HTTPException(503, "Dieser Anmeldeanbieter ist noch nicht eingerichtet")
    values["redirect_uri"] = values["public_url"] + f"/api/auth/{provider}/callback"
    return values


def available_providers() -> list[dict]:
    result = []
    for provider, metadata in PROVIDERS.items():
        try:
            configuration(provider)
            enabled = True
        except HTTPException:
            enabled = False
        result.append({"id": provider, "name": metadata["name"], "enabled": enabled})
    return result


def _client_secret(provider: str, config: dict) -> str:
    if provider == "google":
        return config["client_secret"]
    try:
        private_key = Path(config["private_key_path"]).read_text(encoding="utf-8")
        now = int(time.time())
        return jwt.encode({"iss": config["team_id"], "iat": now, "exp": now + 300,
                           "aud": "https://appleid.apple.com", "sub": config["client_id"]},
                          private_key, algorithm="ES256", headers={"kid": config["key_id"]})
    except (OSError, ValueError, TypeError, jwt.PyJWTError):
        raise HTTPException(503, "Der Anmeldeanbieter ist nicht korrekt eingerichtet") from None


def _json_request(method: str, url: str, **kwargs) -> dict:
    try:
        response = requests.request(method, url, timeout=10, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise HTTPException(400, "Die Anmeldung beim Anbieter konnte nicht bestätigt werden")
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (requests.RequestException, ValueError):
        raise HTTPException(503, "Der Anmeldeanbieter ist gerade nicht erreichbar. Bitte erneut versuchen") from None


def verify_identity(provider: str, encoded: str, config: dict, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(encoded)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError()
        kid = header["kid"]
        with _key_lock:
            cached = _keys.get(provider)
            if not cached or cached[0] < time.time() or not any(k.get("kid") == kid for k in cached[1]):
                keys = _json_request("GET", PROVIDERS[provider]["jwks"]).get("keys")
                if not isinstance(keys, list) or len(keys) > 32 or not all(isinstance(k, dict) for k in keys):
                    raise ValueError()
                cached = (time.time() + 3600, keys)
                _keys[provider] = cached
        key = next(k for k in cached[1] if k.get("kid") == kid and k.get("kty") == "RSA"
                   and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256")
        claims = jwt.decode(encoded, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                            audience=config["client_id"], issuer=PROVIDERS[provider]["issuers"],
                            leeway=30, options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]})
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise ValueError()
        if not isinstance(claims["nonce"], str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise ValueError()
        if claims.get("azp", config["client_id"]) != config["client_id"]:
            raise ValueError()
        # An email address is optional and is not trusted unless verified.
        verified = claims.get("email_verified") in (True, "true")
        email = claims.get("email")
        claims["email"] = email if verified and isinstance(email, str) and len(email) <= 320 else None
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, StopIteration):
        raise HTTPException(400, "Die Identität konnte nicht sicher bestätigt werden") from None


def begin(db, provider: str, *, platform: str, intent: str, code_challenge: str = "",
          user: dict | None = None, session: dict | None = None,
          existing_identity: dict | None = None,
          invitation_token: str = "", browser_binding: str = "") -> dict:
    config = configuration(provider)
    if platform not in {"native", "web"} or intent not in {"login", "link"}:
        raise HTTPException(400, "Ungültiger Anmeldevorgang")
    if platform == "native" and not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge):
        raise HTTPException(400, "Ungültiger Anmeldenachweis")
    if intent == "link" and (not user or user.get("disabled") or not session):
        raise HTTPException(401, "Bitte zuerst am bestehenden Konto anmelden")
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    flow = {"provider": provider, "platform": platform, "intent": intent, "nonce": nonce,
            "verifier": verifier, "challenge": code_challenge, "binding": _digest(browser_binding),
            "client_id": config["client_id"], "redirect_uri": config["redirect_uri"],
            "user_id": user["id"] if user else None, "version": user["session_version"] if user else None,
            "session_id": session["id"] if session else None,
            "existing_identity": existing_identity,
            "invitation": invitation_token if intent == "login" else ""}
    with db.conn() as c:
        c.execute("DELETE FROM oidc_flows WHERE expires_at<=?", (time.time(),))
        c.execute("DELETE FROM oidc_exchanges WHERE expires_at<=?", (time.time(),))
        c.execute("INSERT INTO oidc_flows VALUES(?,?,?)", (_digest(state), _seal(flow), time.time() + FLOW_TTL))
    query = {"client_id": config["client_id"], "redirect_uri": config["redirect_uri"],
             "response_type": "code", "scope": "openid email" if provider == "google" else "email",
             "state": state, "nonce": nonce}
    if provider == "google":
        query.update(code_challenge=challenge(verifier), code_challenge_method="S256",
                     prompt="select_account", access_type="offline")
    else:
        query["response_mode"] = "form_post"
    return {"flow_id": state, "authorization_url": PROVIDERS[provider]["authorize"] + "?" + urlencode(query)}


def consume_flow(db, provider: str, state: str, browser_binding: str) -> dict:
    if provider not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        raise HTTPException(400, "Ungültige oder abgelaufene Anmeldung")
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM oidc_flows WHERE state_hash=? AND expires_at>?",
                        (_digest(state), time.time())).fetchone()
        if not row:
            raise HTTPException(400, "Diese Anmeldung wurde bereits verwendet oder ist abgelaufen")
        flow = _unseal(row["data_secret"])
        if flow["provider"] != provider or (flow["platform"] == "web" and
                not hmac.compare_digest(flow["binding"], _digest(browser_binding))):
            raise HTTPException(400, "Die Anmeldung gehört nicht zu diesem Browser")
        c.execute("DELETE FROM oidc_flows WHERE state_hash=?", (_digest(state),))
    return flow


def finish(db, provider: str, flow: dict, code: str) -> dict:
    config = configuration(provider)
    if config["client_id"] != flow["client_id"] or config["redirect_uri"] != flow["redirect_uri"]:
        raise HTTPException(400, "Die Anmeldekonfiguration wurde geändert. Bitte erneut beginnen")
    data = {"client_id": config["client_id"], "client_secret": _client_secret(provider, config),
            "code": code, "grant_type": "authorization_code", "redirect_uri": config["redirect_uri"]}
    if provider == "google":
        data["code_verifier"] = flow["verifier"]
    tokens = _json_request("POST", PROVIDERS[provider]["token"], data=data)
    claims = verify_identity(provider, str(tokens.get("id_token", "")), config, flow["nonce"])
    token = tokens.get("refresh_token") or tokens.get("access_token")
    token_secret = _seal({"token": token, "refresh": bool(tokens.get("refresh_token"))}) if token else None
    if flow["platform"] == "native":
        # Do not link an identity or create an account until the initiating app
        # proves possession of its verifier. A callback alone is not that proof.
        exchange_code = secrets.token_urlsafe(32)
        pending = {"provider": provider, "flow": flow,
                   "claims": {"sub": claims["sub"], "email": claims.get("email")},
                   "token_secret": token_secret}
        with db.conn() as c:
            c.execute("INSERT INTO oidc_exchanges VALUES(?,?,?,?)",
                      (_digest(exchange_code), _seal(pending), flow["challenge"], time.time() + EXCHANGE_TTL))
        return {"code": exchange_code}
    return _bind_identity(db, provider, flow, claims, token_secret)


def _bind_identity(db, provider: str, flow: dict, claims: dict, token_secret: str | None) -> dict:
    from .accounts import _account, _invitation, _join
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        identity = c.execute("SELECT * FROM oidc_identities WHERE provider=? AND subject=?",
                             (provider, claims["sub"])).fetchone()
        if flow["intent"] == "link":
            user = c.execute("SELECT * FROM users WHERE id=?", (flow["user_id"],)).fetchone()
            session = c.execute("SELECT 1 FROM user_sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?",
                                (flow["session_id"], flow["user_id"], time.time())).fetchone()
            if not user or user["disabled"] or user["session_version"] != flow["version"] or not session:
                raise HTTPException(401, "Deine Sitzung hat sich geändert. Bitte erneut anmelden")
            previous = flow.get("existing_identity")
            if previous and (not identity or identity["id"] != previous["id"] or claims["sub"] != previous["subject"]):
                raise HTTPException(409, "Die Verknüpfung hat sich geändert. Bitte erneut beginnen")
            if identity and identity["user_id"] != user["id"]:
                raise HTTPException(409, "Dieses Anbieterkonto ist bereits mit einem anderen Konto verknüpft")
            other = c.execute("SELECT subject FROM oidc_identities WHERE user_id=? AND provider=?",
                              (user["id"], provider)).fetchone()
            if other and other["subject"] != claims["sub"]:
                raise HTTPException(409, "Für diesen Anbieter ist bereits ein anderes Konto verknüpft")
        elif identity:
            user = c.execute("SELECT * FROM users WHERE id=?", (identity["user_id"],)).fetchone()
            if not user or user["disabled"]:
                raise HTTPException(403, "Dieses Benutzerkonto ist deaktiviert")
        else:
            if not c.execute("SELECT 1 FROM users WHERE role='admin' AND disabled=0 LIMIT 1").fetchone():
                raise HTTPException(503, "Der Rezeptserver muss zuerst eingerichtet werden")
            invitation = _invitation(c, flow["invitation"]) if flow["invitation"] else None
            # The random suffix avoids accidentally claiming an existing username.
            prefix = re.sub(r"[^a-z0-9_.-]", "", (claims.get("email") or provider).split("@")[0].lower())[:20]
            username = (prefix or provider) + "_" + secrets.token_hex(5)
            user_id = c.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,'','user',?)",
                                (username, time.time())).lastrowid
            if invitation:
                _join(c, invitation, user_id)
            else:
                _account(c, user_id)
            user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        # Google commonly omits a refresh token on later logins. Preserve the
        # existing revocable grant instead of replacing it with an access token
        # which may already be expired when the account is eventually deleted.
        if identity and identity["token_secret"] and token_secret:
            saved = _unseal(identity["token_secret"])
            incoming = _unseal(token_secret)
            if saved.get("refresh") is True and incoming.get("refresh") is not True:
                token_secret = identity["token_secret"]
        c.execute("""INSERT INTO oidc_identities(user_id,provider,subject,email,token_secret,linked_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(provider,subject) DO UPDATE SET
            email=COALESCE(excluded.email,oidc_identities.email),
            token_secret=COALESCE(excluded.token_secret,oidc_identities.token_secret)""",
                  (user["id"], provider, claims["sub"], claims.get("email"), token_secret, time.time()))
        c.execute("UPDATE users SET last_login_at=? WHERE id=?", (time.time(), user["id"]))
        result = {"user_id": user["id"], "version": user["session_version"],
                  "provider": provider, "subject": claims["sub"], "username": user["username"]}
    return result


def exchange(db, code: str, verifier: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise HTTPException(400, "Ungültiger Anmeldenachweis")
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM oidc_exchanges WHERE code_hash=? AND expires_at>?",
                        (_digest(code), time.time())).fetchone()
        if not row or not hmac.compare_digest(row["challenge"], challenge(verifier)):
            raise HTTPException(400, "Die Anmeldung ist abgelaufen oder gehört zu einem anderen Gerät")
        pending = _unseal(row["data_secret"])
        c.execute("DELETE FROM oidc_exchanges WHERE code_hash=?", (_digest(code),))
    return _bind_identity(db, pending["provider"], pending["flow"], pending["claims"], pending["token_secret"])


def identity_list(db, user_id: int) -> list[dict]:
    with db.conn() as c:
        return [dict(row) for row in c.execute(
            "SELECT provider,email,linked_at FROM oidc_identities WHERE user_id=? ORDER BY provider", (user_id,))]


def _queue_revocation(c, identity) -> None:
    if identity["token_secret"]:
        now = time.time()
        c.execute("INSERT INTO oidc_revocations(provider,token_secret,created_at,next_attempt) VALUES(?,?,?,?)",
                  (identity["provider"], identity["token_secret"], now, now))


def disconnect(db, user_id: int, provider: str, *, expected_version: int | None = None) -> None:
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["disabled"]:
            raise HTTPException(401, "Bitte erneut anmelden")
        if expected_version is not None and user["session_version"] != expected_version:
            raise HTTPException(409, "Dein Konto wurde inzwischen geändert. Bitte erneut anmelden und bestätigen.")
        rows = c.execute("SELECT * FROM oidc_identities WHERE user_id=?", (user_id,)).fetchall()
        target = next((row for row in rows if row["provider"] == provider), None)
        if not target:
            raise HTTPException(404, "Diese Verknüpfung besteht nicht")
        if not user["password_hash"] and len(rows) <= 1:
            raise HTTPException(409, "Lege zuerst ein Passwort oder einen weiteren Anmeldeweg an")
        _queue_revocation(c, target)
        c.execute("DELETE FROM oidc_identities WHERE id=?", (target["id"],))
        # A removed provider's app sessions must stop working immediately.
        c.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND auth_method=? AND revoked_at IS NULL",
                  (time.time(), user_id, provider))


def queue_deleted_user(c, user_id: int) -> None:
    """Call inside the successful user deletion transaction, before FK cascade."""
    for row in c.execute("SELECT * FROM oidc_identities WHERE user_id=?", (user_id,)).fetchall():
        _queue_revocation(c, row)


def retry_revocations(db, limit: int = 10) -> None:
    with db.conn() as c:
        rows = [dict(row) for row in c.execute(
            "SELECT * FROM oidc_revocations WHERE next_attempt<=? ORDER BY id LIMIT ?", (time.time(), limit))]
    for row in rows:
        try:
            config = configuration(row["provider"])
            saved = _unseal(row["token_secret"])
            data = {"token": saved["token"]}
            if row["provider"] == "apple":
                data.update(client_id=config["client_id"], client_secret=_client_secret("apple", config),
                            token_type_hint="refresh_token" if saved["refresh"] else "access_token")
            response = requests.post(PROVIDERS[row["provider"]]["revoke"], data=data, timeout=10, allow_redirects=False)
            already_invalid = False
            if row["provider"] == "google" and response.status_code == 400:
                try:
                    error = response.json()
                    already_invalid = isinstance(error, dict) and error.get("error") == "invalid_token"
                except ValueError:
                    pass
            if response.status_code != 200 and not already_invalid:
                raise ValueError("Provider revocation pending")
        except (HTTPException, requests.RequestException, ValueError, KeyError):
            with db.conn() as c:
                c.execute("UPDATE oidc_revocations SET attempts=attempts+1,next_attempt=? WHERE id=?",
                          (time.time() + min(3600, 60 * 2 ** min(row["attempts"], 6)), row["id"]))
        else:
            with db.conn() as c:
                c.execute("DELETE FROM oidc_revocations WHERE id=?", (row["id"],))
