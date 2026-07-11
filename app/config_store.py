"""
Konfigurationsverwaltung.
Lädt/Speichert config.yaml und stellt einen typisierten Accessor bereit.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List

import yaml

CONFIG_PATH = Path(os.getenv("SCRAPPER_CONFIG", "/opt/scrapper/data/config.yaml"))
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.example.yaml"
logger = logging.getLogger(__name__)


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            if DEFAULT_CONFIG_PATH.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                os.chmod(self.path, 0o600)
            else:
                self._data = {}
                return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError("Config root must be a mapping")
            self._data = loaded
            # Auch manuell angelegte Configs mit zu offenen Rechten härten.
            os.chmod(self.path, 0o600)
            return
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            backup = self.path.with_name(self.path.name + ".bak")
            logger.error("Config %s ist nicht lesbar/gültig: %s", self.path, exc)
            if not backup.is_file():
                raise RuntimeError(
                    f"Konfiguration {self.path} ist ungültig und es existiert kein Backup"
                ) from exc
            try:
                with open(backup, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if not isinstance(loaded, dict):
                    raise ValueError("Backup config root must be a mapping")
                shutil.copy2(backup, self.path)
                os.chmod(self.path, 0o600)
                self._data = loaded
                logger.warning("Config automatisch aus %s wiederhergestellt", backup)
            except (OSError, UnicodeError, yaml.YAMLError, ValueError) as backup_exc:
                raise RuntimeError(
                    f"Konfiguration und Backup sind ungültig: {self.path}, {backup}"
                ) from backup_exc

    def reload(self) -> None:
        with self._lock:
            self._load()

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return self._deepcopy(self._data)

    def get(self, *keys, default=None):
        with self._lock:
            cur: Any = self._data
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            # Explizites null im YAML wie 'ai.fallback_threshold:' (Key
            # existiert, Wert ist None) soll als 'nicht gesetzt' behandelt
            # werden, sonst kracht's bei float()/int()-Konvertierungen am
            # Verwendungsort. Default vorziehen.
            if cur is None:
                return default
            # Path-Werte automatisch von leading/trailing whitespace befreien.
            # Tippfehler beim manuellen YAML-Editieren ("/mnt/data/rezepte ")
            # haben sonst zu /healthz-Failures und 'path does not exist' geführt.
            # Wir greifen nur auf Pfad-ähnlichen Sub-Trees ein (paths.* + alles
            # was auf '_dir' / '_path' endet), um nicht versehentlich Werte wie
            # Passwörter zu mangeln.
            if (isinstance(cur, str)
                    and len(keys) >= 1
                    and (keys[0] == "paths"
                         or (keys[-1].endswith("_dir") or keys[-1].endswith("_path") or keys[-1].endswith("_file")))):
                return cur.strip()
            return cur

    def set(self, *keys_and_value) -> None:
        """set('mail', 'recipe', 'username', 'foo@bar')"""
        if len(keys_and_value) < 2:
            raise ValueError("set() braucht mind. 1 Key + 1 Value")
        *keys, value = keys_and_value
        with self._lock:
            cur = self._data
            for k in keys[:-1]:
                if k not in cur or not isinstance(cur[k], dict):
                    cur[k] = {}
                cur = cur[k]
            cur[keys[-1]] = value

    def replace(self, new_data: Dict[str, Any]) -> None:
        """Komplette Config ersetzen (für Web-Edit)."""
        with self._lock:
            self._data = new_data

    def save(self) -> None:
        """Atomisch speichern, vorherige Version als ``.bak`` erhalten.

        ``fsync`` verhindert, dass nach Stromausfall zwar der Rename erfolgt ist,
        die Daten aber noch nicht dauerhaft auf dem Datenträger lagen.
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            backup = self.path.with_name(self.path.name + ".bak")
            if self.path.exists():
                shutil.copy2(self.path, backup)
                os.chmod(backup, 0o600)
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self._data, f, allow_unicode=True, sort_keys=False, default_flow_style=False
                )
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            try:
                dir_fd = os.open(str(self.path.parent), os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):
                pass

    @staticmethod
    def _deepcopy(obj):
        import copy
        return copy.deepcopy(obj)


def deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Merged Dictionaries rekursiv; Listen/skalare Werte werden ersetzt.

    Dadurch gehen neue Server-Defaults nicht verloren, wenn ein älteres
    Frontend eine vollständige Config ohne die neuen Felder zurückschickt.
    """
    import copy
    out = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_config(data: Dict[str, Any]) -> List[str]:
    """Leichte, dependency-freie Plausibilitätsprüfung der Web-Konfiguration."""
    from pathlib import Path
    import re
    from .path_utils import ensure_within

    errors: List[str] = []
    web = data.get("web") or {}
    if not str(web.get("username") or "").strip():
        errors.append("web.username darf nicht leer sein")
    try:
        port = int(web.get("bind_port", 8000))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("web.bind_port muss zwischen 1 und 65535 liegen")
    bind_host = str(web.get("bind_host") or "127.0.0.1").strip()
    if (not bind_host or len(bind_host) > 253 or any(ord(ch) < 33 for ch in bind_host)
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", bind_host)):
        errors.append("web.bind_host enthält ungültige Zeichen")
    secret = str(web.get("secret_key") or "")
    if secret and secret != "********" and len(secret) < 32:
        errors.append("web.secret_key muss mindestens 32 Zeichen haben")

    metrics_token = str((data.get("monitoring") or {}).get("metrics_token") or "")
    if metrics_token and metrics_token != "********" and len(metrics_token) < 24:
        errors.append("monitoring.metrics_token muss leer oder mindestens 24 Zeichen lang sein")
    try:
        import ipaddress
        raw_proxies = web.get("trusted_proxies") or []
        if isinstance(raw_proxies, str):
            proxies = [item.strip() for item in raw_proxies.split(",") if item.strip()]
        elif isinstance(raw_proxies, list):
            proxies = raw_proxies
        else:
            raise TypeError("muss eine Liste oder kommaseparierter Text sein")
        for proxy in proxies:
            if str(proxy).strip() == "*":
                errors.append("web.trusted_proxies darf niemals '*' enthalten")
                continue
            ipaddress.ip_network(str(proxy).strip(), strict=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"web.trusted_proxies enthält ungültige IP/CIDR: {exc}")

    paths = data.get("paths") or {}
    for key in ("recipe_dir", "wedding_dir", "temp_dir", "logs_dir"):
        value = str(paths.get(key) or "").strip()
        if not value or not Path(value).is_absolute():
            errors.append(f"paths.{key} muss ein absoluter Pfad sein")
    try:
        retention = int(paths.get("log_retention_days", 30))
        if not 0 <= retention <= 3650:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("paths.log_retention_days muss zwischen 0 und 3650 liegen")

    ai = data.get("ai") or {}
    provider = str(ai.get("provider") or "ollama").strip().lower()
    if provider not in {"ollama", "openai"}:
        errors.append("ai.provider muss 'ollama' oder 'openai' sein")
    for key in ("confidence_threshold", "fallback_threshold"):
        try:
            value = float(ai.get(key, 0.5))
            if not 0 <= value <= 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"ai.{key} muss zwischen 0 und 1 liegen")
    try:
        min_desc = int(ai.get("description_min_length", 20))
        if not 1 <= min_desc <= 5000:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("ai.description_min_length muss zwischen 1 und 5000 liegen")
    from urllib.parse import urlsplit
    for name, default_timeout in (("ollama", 60), ("openai", 30)):
        provider_cfg = ai.get(name) or {}
        url_key = "url" if name == "ollama" else "base_url"
        raw_url = str(provider_cfg.get(url_key) or "").strip()
        if raw_url:
            parsed = urlsplit(raw_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                errors.append(f"ai.{name}.{url_key} muss eine gültige HTTP(S)-URL ohne Zugangsdaten sein")
        try:
            timeout = int(provider_cfg.get("timeout", default_timeout))
            if not 5 <= timeout <= 600:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"ai.{name}.timeout muss zwischen 5 und 600 liegen")

    try:
        max_mb = int((data.get("mail") or {}).get("max_attachment_mb", 20))
        if not 1 <= max_mb <= 200:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("mail.max_attachment_mb muss zwischen 1 und 200 liegen")
    try:
        max_mail_mb = int((data.get("mail") or {}).get("max_mail_mb", 50))
        if not 1 <= max_mail_mb <= 500:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("mail.max_mail_mb muss zwischen 1 und 500 liegen")
    try:
        max_count = int((data.get("mail") or {}).get("max_attachments_per_mail", 10))
        if not 1 <= max_count <= 100:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("mail.max_attachments_per_mail muss zwischen 1 und 100 liegen")

    ytdlp = data.get("ytdlp") or {}
    for key, minimum, maximum in (
        ("timeout_sec", 30, 7200),
        ("max_filesize_mb", 1, 5000),
        ("retries", 0, 20),
    ):
        try:
            value = int(ytdlp.get(key, minimum))
            if not minimum <= value <= maximum:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"ytdlp.{key} muss zwischen {minimum} und {maximum} liegen")
    binary = str(ytdlp.get("binary") or "").strip()
    if not binary or not Path(binary).is_absolute():
        errors.append("ytdlp.binary muss ein absoluter Pfad sein")
    cookies = str(ytdlp.get("cookies_file") or "").strip()
    if cookies and not Path(cookies).is_absolute():
        errors.append("ytdlp.cookies_file muss leer oder ein absoluter Pfad sein")

    for account_name in ("recipe", "wedding"):
        account = (data.get("mail") or {}).get(account_name) or {}
        try:
            port = int(account.get("imap_port", 993))
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"mail.{account_name}.imap_port muss zwischen 1 und 65535 liegen")
        try:
            max_mails = int(account.get("max_mails", 20))
            if not 1 <= max_mails <= 1000:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"mail.{account_name}.max_mails muss zwischen 1 und 1000 liegen")

    hdd = data.get("external_hdd") or {}
    mount_point = str(hdd.get("mount_point") or "").strip()
    if mount_point:
        mount_path = Path(mount_point)
        try:
            resolved = ensure_within(mount_path, Path("/mnt"))
            if not mount_path.is_absolute() or resolved == Path("/mnt").resolve(strict=False):
                raise ValueError
        except (OSError, ValueError):
            errors.append("external_hdd.mount_point muss ein sicherer Unterordner von /mnt sein")
    shelly_url = str(hdd.get("shelly_url") or "").strip()
    if shelly_url:
        parsed = urlsplit(shelly_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            errors.append("external_hdd.shelly_url muss eine gültige HTTP(S)-URL ohne Zugangsdaten sein")
    for key, minimum, maximum in (
        ("spinup_delay_sec", 0, 300),
        ("unmount_delay_sec", 0, 60),
        ("http_timeout_sec", 1, 120),
    ):
        try:
            value = int(hdd.get(key, minimum))
            if not minimum <= value <= maximum:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"external_hdd.{key} muss zwischen {minimum} und {maximum} liegen")
    return errors


# Singleton
_config: ConfigStore | None = None
_config_lock = threading.Lock()


def get_config() -> ConfigStore:
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = ConfigStore()
    return _config
