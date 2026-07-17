"""Bildnormalisierung und atomarer Thumbnail-Cache.

Pillow ist für einzelne Bilder deutlich leichter als ein ffmpeg-Prozess pro
Request. Keyed Locks verhindern, dass mehrere Browserrequests denselben Cache
parallel erzeugen.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000

_lock_guard = threading.Lock()
_locks: Dict[str, threading.Lock] = {}


def _keyed_lock(key: str) -> threading.Lock:
    with _lock_guard:
        return _locks.setdefault(key, threading.Lock())


def cached_thumbnail_path(source: Path, width: int) -> Path:
    return source.parent / f"thumb-w{int(width)}.jpg"


def ensure_thumbnail(source: Path, width: int, *, quality: int = 84) -> Path:
    """Erzeugt/aktualisiert ``thumb-w<width>.jpg`` atomar und gibt den Pfad zurück."""
    source = Path(source)
    width = max(64, min(2048, int(width)))
    target = cached_thumbnail_path(source, width)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    key = f"{source.resolve()}::{width}"
    with _keyed_lock(key):
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((width, width * 8), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "L"):
                    background = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        background.paste(image, mask=image.getchannel("A"))
                    else:
                        background.paste(image)
                    image = background
                elif image.mode == "L":
                    image = image.convert("RGB")
                image.save(tmp, format="JPEG", quality=quality, optimize=True, progressive=True)
            os.replace(tmp, target)
            return target
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def normalize_image(source: Path, target: Path, *, max_width: int = 2400, quality: int = 88) -> Path:
    """Dekodiert ein Bild vollständig, entfernt Metadaten und schreibt JPEG atomar."""
    source = Path(source)
    target = Path(target)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with Image.open(source) as image:
            image.load()  # vollständiges Dekodieren, nicht nur Header lesen
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_width, max_width * 8), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "L"):
                canvas = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    canvas.paste(image, mask=image.getchannel("A"))
                else:
                    canvas.paste(image)
                image = canvas
            elif image.mode == "L":
                image = image.convert("RGB")
            image.save(tmp, format="JPEG", quality=quality, optimize=True, progressive=True)
        os.replace(tmp, target)
        return target
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def invalidate_thumbnail_cache(folder: Path) -> None:
    for cached in Path(folder).glob("thumb-w*.jpg"):
        try:
            cached.unlink()
        except OSError:
            pass
