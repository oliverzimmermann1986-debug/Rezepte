"""Bild und Datenbankzeiger gemeinsam mit kompensierendem Rollback veröffentlichen."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional
import uuid

from ..core.safety import atomic_write_bytes, resolve_regular_file_under
from .image_cache import image_publication_lock

logger = logging.getLogger(__name__)


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Temporäre Bilddatei konnte nicht entfernt werden: %s", path)


@contextmanager
def publish_image(
    staged: Path, target: Path, *, validate: Optional[Callable[[], None]] = None,
) -> Iterator[None]:
    """Erhält das aktive Bild bis zum atomaren Austausch und sichert DB-Fehler ab.

    Im Context muss der Aufrufer den DB-Zeiger in einer Transaktion speichern.
    Scheitert auch die Wiederherstellung, bleibt die Rückfallkopie erhalten.
    Der Ordner-Lock umfasst Dateiaustausch und DB-Commit, damit ein fehlgeschlagener
    Upload keinen parallel erfolgreichen Bildwechsel zurückrollen kann.
    """
    with image_publication_lock(target.parent):
        if validate is not None:
            validate()
        with _publish_image(staged, target):
            yield


@contextmanager
def _publish_image(staged: Path, target: Path) -> Iterator[None]:
    if staged == target or staged.is_symlink() or target.is_symlink():
        raise ValueError("Unsicherer Pfad für Bildveröffentlichung")
    staged = resolve_regular_file_under(staged, target.parent)
    rollback = target.parent / f".thumb-rollback-{uuid.uuid4().hex}{target.suffix}"
    had_target = target.is_file()
    published = False
    committed = False
    try:
        if had_target:
            original = resolve_regular_file_under(target, target.parent)
            # Copy instead of moving: readers retain the original even if the
            # subsequent replacement fails or the process stops beforehand.
            atomic_write_bytes(rollback, original.read_bytes())
        os.replace(staged, target)
        published = True
        yield
        committed = True
    except BaseException:
        if published:
            if had_target:
                os.replace(rollback, target)
            else:
                target.unlink(missing_ok=True)
            published = False
        raise
    finally:
        _cleanup(staged)
        if committed or not published:
            _cleanup(rollback)


@contextmanager
def _remove_images(paths: Iterable[Path], folder: Path) -> Iterator[None]:
    """Entfernt Cover mit Rückfallkopien; der Aufrufer hält den Ordner-Lock."""
    removed = []
    committed = False
    try:
        for path in dict.fromkeys(paths):
            original = resolve_regular_file_under(path, folder)
            rollback = folder / f".thumb-rollback-{uuid.uuid4().hex}{original.suffix}"
            atomic_write_bytes(rollback, original.read_bytes())
            removed.append((original, rollback))
            original.unlink()
        yield
        committed = True
    except BaseException:
        for original, rollback in reversed(removed):
            try:
                os.replace(rollback, original)
            except OSError:
                # Jede weitere Datei ebenfalls retten; fehlgeschlagene Kopien
                # bleiben für eine manuelle Wiederherstellung erhalten.
                logger.exception("Bild-Rollback fehlgeschlagen; Sicherung: %s", rollback)
        raise
    finally:
        if committed:
            for _, rollback in removed:
                _cleanup(rollback)
