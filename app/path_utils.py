"""Sichere Dateisystem-Helfer für vom Benutzer oder von KI erzeugte Namen."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WS = re.compile(r"\s+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def safe_component(value: object, *, fallback: str = "Unbekannt", max_length: int = 96) -> str:
    """Macht aus beliebigem Text genau *eine* sichere Pfadkomponente.

    Verhindert Traversal (``.``/``..``), Steuerzeichen, Windows-reservierte
    Namen und extrem lange Dateinamen. Unicode bleibt lesbar erhalten.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _INVALID_CHARS.sub("", text)
    text = _WS.sub("_", text)
    text = _MULTI_UNDERSCORE.sub("_", text)
    text = text.strip(" ._")
    if not text or text in {".", ".."}:
        text = fallback
    if text.upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    # Nach UTF-8-Bytes begrenzen, damit auch auf ext4/SMB keine 255-Byte-Grenze
    # gerissen wird. 96 ist bewusst konservativ, weil Suffixe hinzukommen.
    encoded = text.encode("utf-8")
    if len(encoded) > max_length:
        encoded = encoded[:max_length]
        while encoded:
            try:
                text = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        text = text.rstrip(" ._") or fallback
    return text


def ensure_within(path: Path, root: Path) -> Path:
    """Löst ``path`` auf und wirft ValueError, wenn es außerhalb ``root`` liegt."""
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Pfad liegt außerhalb des erlaubten Roots: {resolved}") from exc
    return resolved


def build_under(root: Path, components: Iterable[object]) -> Path:
    """Baut einen Zielpfad ausschließlich aus sicheren Komponenten unter ``root``."""
    target = root
    for component in components:
        target = target / safe_component(component)
    ensure_within(target, root)
    return target


def unique_directory(target: Path, *, timestamp: str) -> Path:
    """Liefert ``target`` oder eine kollisionsfreie Variante mit Zeit/Counter."""
    if not target.exists():
        return target
    base = safe_component(target.name)
    candidate = target.parent / f"{base}_{timestamp}"
    counter = 2
    while candidate.exists():
        candidate = target.parent / f"{base}_{timestamp}_{counter}"
        counter += 1
    return candidate
