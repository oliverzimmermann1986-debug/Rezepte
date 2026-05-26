"""
Konfigurationsverwaltung.
Lädt/Speichert config.yaml und stellt einen typisierten Accessor bereit.
"""
from __future__ import annotations

import os
import threading
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
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(DEFAULT_CONFIG_PATH.read_text())
            else:
                self._data = {}
                return
        with open(self.path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

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
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self._data, f, allow_unicode=True, sort_keys=False, default_flow_style=False
                )
            tmp.replace(self.path)
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass

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
