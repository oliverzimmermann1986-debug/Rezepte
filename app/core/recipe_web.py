"""Sicherer Import öffentlicher Rezeptseiten und einzelner Social-Posts."""
from __future__ import annotations

import ipaddress
import json
import re
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .webhook import pinned_https_request


_TIKTOK_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
}
_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_PINTEREST_ROOTS = {
    "pinterest.com", "pinterest.de", "pinterest.at", "pinterest.ch",
    "pinterest.co.uk", "pinterest.fr", "pinterest.es", "pinterest.it",
    "pinterest.ca", "pinterest.com.au", "pinterest.jp", "pinterest.nl",
}
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "igsh", "ref", "referrer", "share", "si", "source",
}
_MAX_URL_LENGTH = 2048
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _is_pinterest_host(host: str) -> bool:
    if host == "pin.it" or host.endswith(".pin.it"):
        return True
    return any(host == root or host.endswith(f".{root}") for root in _PINTEREST_ROOTS)


def _looks_like_protected_host(host: str) -> bool:
    protected = ("instagram.com", "tiktok.com", "youtube.com", "youtu.be", "pin.it")
    if host in (_TIKTOK_HOSTS | _INSTAGRAM_HOSTS | _YOUTUBE_HOSTS) or _is_pinterest_host(host):
        return False
    return any(name in host for name in protected) or "pinterest." in host


def _safe_host(parsed) -> Optional[str]:
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    try:
        if parsed.port not in (None, 443):
            return None
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    return host


def _clean_path(path: str) -> Optional[str]:
    path = path or "/"
    decoded = unquote(unquote(path))
    if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        return None
    return path


def _clean_generic_query(query: str) -> str:
    pairs = []
    for key, values in parse_qs(query, keep_blank_values=True).items():
        lowered = key.casefold()
        if lowered in _TRACKING_QUERY_KEYS or lowered.startswith(_TRACKING_QUERY_PREFIXES):
            continue
        pairs.extend((key, value) for value in values)
    return urlencode(pairs, doseq=True)


def normalize_recipe_url(url: str) -> Optional[str]:
    """Normalisiert eine einzelne unterstützte Quelle ohne DNS-Zugriff.

    Die eigentliche Website-Abfrage wird zusätzlich DNS-gepinnt und lehnt
    private, lokale oder per Redirect eingeschleuste Ziele ab.
    """
    candidate = str(url or "").strip()
    if not candidate or len(candidate) > _MAX_URL_LENGTH or "\\" in candidate or "\x00" in candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    host = _safe_host(parsed)
    path = _clean_path(parsed.path)
    if not host or path is None:
        return None
    if _looks_like_protected_host(host):
        return None
    lower_path = path.casefold()

    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        if path == "/":
            return None
        return urlunsplit(("https", host, path, "", ""))
    if host in _TIKTOK_HOSTS:
        if "/video/" not in lower_path and "/photo/" not in lower_path:
            return None
        return urlunsplit(("https", host, path, "", ""))
    if host in _INSTAGRAM_HOSTS:
        if not any(marker in lower_path for marker in ("/reel/", "/p/", "/tv/")):
            return None
        return urlunsplit(("https", host, path, "", ""))

    if host in _YOUTUBE_HOSTS:
        if host == "youtu.be":
            video_id = path.strip("/").split("/", 1)[0]
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                return None
            return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"
        if lower_path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0].strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                return None
            return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"
        if not any(lower_path.startswith(prefix) for prefix in ("/shorts/", "/live/", "/embed/")):
            return None
        return urlunsplit(("https", "www.youtube.com", path, "", ""))

    if _is_pinterest_host(host):
        if host == "pin.it" or host.endswith(".pin.it"):
            return urlunsplit(("https", host, path, "", "")) if path != "/" else None
        if "/pin/" not in lower_path:
            return None
        return urlunsplit(("https", host, path, "", ""))

    return urlunsplit(("https", host, path, _clean_generic_query(parsed.query), ""))


def recipe_source_platform(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    if "tiktok.com" in host:
        return "TikTok"
    if "instagram.com" in host:
        return "Instagram"
    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return "YouTube"
    if _is_pinterest_host(host):
        return "Pinterest"
    return "Webseite"


def is_video_recipe_source(url: str) -> bool:
    return recipe_source_platform(url) in {"TikTok", "Instagram", "YouTube"}


def _request_following_public_redirects(url: str, *, max_bytes: int, accept: str):
    current = normalize_recipe_url(url)
    if not current:
        raise ValueError("Ungültige Rezept-URL")
    for _ in range(4):
        response = pinned_https_request(
            "GET",
            current,
            headers={"Accept": accept, "User-Agent": "Rezepte/1.0 (+self-hosted recipe importer)"},
            timeout=(5, 20),
            max_response_bytes=max_bytes,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response, current
        target = normalize_recipe_url(urljoin(current, response.headers.get("location") or ""))
        if not target:
            raise ValueError("Weiterleitung führte zu einer nicht erlaubten URL")
        current = target
    raise ValueError("Zu viele Weiterleitungen")


def _json_ld_nodes(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from _json_ld_nodes(graph)
    elif isinstance(value, list):
        for item in value:
            yield from _json_ld_nodes(item)


def _is_recipe_node(node: Dict[str, Any]) -> bool:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return any(str(value or "").casefold() == "recipe" for value in values)


def _instruction_text(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("name") or "").strip()
            if text:
                result.append(text)
            result.extend(_instruction_text(item.get("itemListElement")))
    return result


def _image_url(raw: Any) -> Optional[str]:
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, list):
        for item in raw:
            found = _image_url(item)
            if found:
                return found
    if isinstance(raw, dict):
        return _image_url(raw.get("url") or raw.get("contentUrl"))
    return None


def _fetch_image(url: str) -> tuple[Optional[bytes], Optional[str]]:
    normalized = normalize_recipe_url(url)
    if not normalized:
        return None, None
    try:
        response, _ = _request_following_public_redirects(
            normalized,
            max_bytes=_MAX_IMAGE_BYTES,
            accept="image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        )
    except Exception:
        return None, None
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].casefold()
    suffix = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    }.get(content_type)
    if response.status_code != 200 or not suffix or not response.content:
        return None, None
    return response.content, suffix


def extract_recipe_web_metadata(
    url: str, *, include_thumbnail: bool = True
) -> Dict[str, Any]:
    """Extrahiert Recipe-JSON-LD, sichtbare Metadaten und optional ein Cover.

    Der Quellenwächter braucht nur Text und lädt deshalb keine möglicherweise
    großen Bilder nach. Bestehende Importaufrufe behalten das bisherige
    Verhalten über ``include_thumbnail=True``.
    """
    response, final_url = _request_following_public_redirects(
        url,
        max_bytes=_MAX_HTML_BYTES,
        accept="text/html,application/xhtml+xml;q=0.9",
    )
    if response.status_code != 200:
        raise ValueError(f"Rezeptseite antwortete mit HTTP {response.status_code}")
    content_type = str(response.headers.get("content-type") or "").casefold()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise ValueError("Die URL liefert keine HTML-Rezeptseite")
    soup = BeautifulSoup(response.text, "html.parser")
    recipe_node: Dict[str, Any] = {}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        recipe_node = next((node for node in _json_ld_nodes(payload) if _is_recipe_node(node)), {})
        if recipe_node:
            break

    def meta(*keys: str) -> str:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            value = str(tag.get("content") or "").strip() if tag else ""
            if value:
                return value
        return ""

    title = str(recipe_node.get("name") or meta("og:title", "twitter:title") or "").strip()
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    description = str(
        recipe_node.get("description") or meta("og:description", "description", "twitter:description") or ""
    ).strip()
    ingredients = [str(item).strip() for item in recipe_node.get("recipeIngredient") or [] if str(item).strip()]
    instructions = _instruction_text(recipe_node.get("recipeInstructions"))
    servings = str(recipe_node.get("recipeYield") or "").strip()

    parts = [title, description]
    if servings:
        parts.append(f"Portionen: {servings}")
    if ingredients:
        parts.append("Zutaten:\n- " + "\n- ".join(ingredients))
    if instructions:
        parts.append("Zubereitung:\n" + "\n".join(
            f"{index}. {text}" for index, text in enumerate(instructions, 1)
        ))
    description_text = "\n\n".join(part for part in parts if part).strip()[:200_000]

    canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    canonical_url = normalize_recipe_url(
        urljoin(final_url, str(canonical_tag.get("href") or "")) if canonical_tag else final_url
    ) or final_url
    image_url = _image_url(recipe_node.get("image")) or meta("og:image", "twitter:image")
    if image_url:
        image_url = urljoin(final_url, image_url)
    thumbnail_bytes, thumbnail_suffix = (
        _fetch_image(image_url) if include_thumbnail and image_url else (None, None)
    )

    return {
        "canonical_url": canonical_url,
        "description_text": description_text or None,
        "description_source": "recipe-json-ld" if recipe_node else "website-metadata",
        "page_title": title or None,
        "thumbnail_bytes": thumbnail_bytes,
        "thumbnail_suffix": thumbnail_suffix,
    }
