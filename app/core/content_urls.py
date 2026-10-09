"""Shared URL normalization for direct recipe and social-media imports."""
from __future__ import annotations

from typing import Optional

from .recipe_web import normalize_recipe_url


def normalize_content_url(url: str) -> Optional[str]:
    """Return a supported canonical recipe or social-post URL."""
    return normalize_recipe_url(url)


def is_content_url(url: str) -> bool:
    return normalize_content_url(url) is not None
