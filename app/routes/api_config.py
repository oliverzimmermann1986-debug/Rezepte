"""API für Config-CRUD."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..auth import hash_password, is_hashed, require_auth
from ..config_store import get_config

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])


@router.get("")
def read_config() -> Dict[str, Any]:
    """Liefert die Config zurück. Passwörter werden maskiert."""
    cfg = get_config().all()
    return _mask(cfg)


@router.put("")
def update_config(payload: Dict[str, Any]):
    """Schreibt die komplette Config neu. Maskierte Felder werden zurückgemerged."""
    store = get_config()
    current = store.all()
    merged = _unmask(payload, current)
    # Web-Passwort, falls Klartext, immer bcrypt-hashen
    pw = _get(merged, ("web", "password"))
    if isinstance(pw, str) and pw and not is_hashed(pw):
        if len(pw) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        _set(merged, ("web", "password"), hash_password(pw))
    store.replace(merged)
    store.save()
    return {"ok": True}


@router.post("/reload")
def reload_config():
    get_config().reload()
    return {"ok": True}


# -------------------- Helper --------------------
MASKED = "********"
MASK_PATHS = [
    ("web", "password"),
    ("web", "secret_key"),
    ("mail", "recipe", "password"),
    ("mail", "wedding", "password"),
]


def _get(d: dict, path: tuple):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set(d: dict, path: tuple, value: Any) -> None:
    cur = d
    for k in path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def _mask(cfg: dict) -> dict:
    import copy
    out = copy.deepcopy(cfg)
    for path in MASK_PATHS:
        v = _get(out, path)
        if v:
            _set(out, path, MASKED)
    return out


def _unmask(incoming: dict, current: dict) -> dict:
    """Übernimmt aktuelle Werte wenn das Feld noch maskiert ist."""
    import copy
    out = copy.deepcopy(incoming)
    for path in MASK_PATHS:
        if _get(out, path) == MASKED:
            real = _get(current, path)
            if real is not None:
                _set(out, path, real)
    return out
