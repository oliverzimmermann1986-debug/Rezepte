"""
Konfigurationsverwaltung.
Lädt/Speichert config.yaml und stellt einen typisierten Accessor bereit.
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_PATH = Path(os.getenv("SCRAPPER_CONFIG", "/opt/scrapper/data/config.yaml"))
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.example.yaml"


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            if DEFAULT_CONFIG_PATH.exists():
                with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as source:
                    self._data = yaml.safe_load(source) or {}
                self.save()
                return
            else:
                self._data = {}
                return
        with open(self.path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

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
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.parent / (
                f".{self.path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:10]}"
            )
            try:
                fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        self._data, f, allow_unicode=True, sort_keys=False,
                        default_flow_style=False,
                    )
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
                os.chmod(self.path, 0o600)
                try:
                    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    dir_fd = os.open(str(self.path.parent), dir_flags)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    @staticmethod
    def _deepcopy(obj):
        import copy
        return copy.deepcopy(obj)


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


def migrate_pdf_quality_defaults() -> bool:
    """Hebt ausschließlich die bekannten 1.1/1.2-Standardwerte an.

    Eigene abweichende Werte bleiben unangetastet. Neue Optionen werden ergänzt,
    damit bestehende Installationen nach einem Update die robustere Rotation und
    das 300-DPI-Qualitätsprofil tatsächlich verwenden.
    """
    store = get_config()
    pdf = store.get("pdf", default={}) or {}
    changed = False

    replacements = {
        "text_dominance": (0.65, 0.60),
        "osd_min_confidence": (3.0, 1.0),
        "max_osd_pages": (12, 100),
        "deskew_scans": (False, True),
        "improve_contrast": (False, True),
    }
    for key, (old, new) in replacements.items():
        if pdf.get(key) == old:
            store.set("pdf", key, new)
            changed = True

    additions = {
        "use_ocr_vote": True,
        "sharpen_scans": True,
        "scan_dpi": 300,
    }
    for key, value in additions.items():
        if key not in pdf:
            store.set("pdf", key, value)
            changed = True

    if changed:
        store.save()
    return changed
