"""Read long TikTok captions that only appear after clicking "mehr"."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MORE_LABELS = ("mehr", "more", "voir plus", "ver más", "altro", "meer", "mais")
_LESS_LABELS = ("weniger", "less", "voir moins", "ver menos", "meno", "minder", "menos")
_SUMMARY_NOTICES = (
    "dies ist eine ki-generierte zusammenfassung",
    "this is an ai-generated summary",
    "ceci est un résumé généré par l’ia",
    "ceci est un résumé généré par l'ia",
)


def is_tiktok_url(url: str) -> bool:
    """Return True only for TikTok web URLs."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def clean_expanded_caption(text: str) -> str:
    """Normalize rendered text and remove TikTok UI/disclaimer suffixes."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]

    cutoff = len(lines)
    for idx, line in enumerate(lines):
        folded = line.strip().lstrip("ⓘℹ️ ").casefold()
        if any(marker in folded for marker in _SUMMARY_NOTICES):
            cutoff = idx
            break
    lines = lines[:cutoff]

    while lines and (
        not lines[-1].strip()
        or lines[-1].strip().casefold() in _LESS_LABELS + _MORE_LABELS
    ):
        lines.pop()

    cleaned: List[str] = []
    for line in lines:
        if not line.strip() and (not cleaned or not cleaned[-1].strip()):
            continue
        cleaned.append(line.strip())
    return "\n".join(cleaned).strip()


def caption_from_article_text(text: str, page_title: str) -> str:
    """Extract the long caption from TikTok's full rendered article text.

    The expanded recipe is currently a sibling of the short description
    container. Its title is also used as the browser page title, which gives
    us a stable boundary without creator, location, video, or counter UI.
    """
    title = re.sub(r"\s*\|\s*TikTok\s*$", "", page_title or "", flags=re.IGNORECASE).strip()
    if not title:
        return ""
    start = text.find(title)
    if start < 0:
        return ""
    return clean_expanded_caption(text[start:])


def parse_netscape_cookies(path: Optional[str]) -> List[Dict[str, Any]]:
    """Convert a Netscape cookies.txt file to Playwright cookie objects."""
    if not path:
        return []
    cookie_file = Path(path)
    if not cookie_file.is_file():
        return []
    try:
        lines = cookie_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("TikTok-Cookies konnten nicht gelesen werden: %s", exc)
        return []

    cookies: List[Dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _include_subdomains, path_value, secure, expires, name, value = parts
        if not domain or not name:
            continue
        cookie: Dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path_value or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": http_only,
        }
        try:
            expires_number = int(expires)
            if expires_number > 0:
                cookie["expires"] = expires_number
        except ValueError:
            pass
        cookies.append(cookie)
    return cookies


def _dismiss_overlays(page: Any) -> None:
    """Best-effort dismissal of cookie and CAPTCHA overlays."""
    for name in (
        "Optionale Cookies ablehnen",
        "Reject optional cookies",
        "Alle ablehnen",
        "Reject all",
        "Schließen",
        "Close",
    ):
        try:
            locator = page.get_by_role("button", name=name, exact=True)
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1_500)
                page.wait_for_timeout(200)
        except Exception:
            continue


def _target_article(page: Any, url: str) -> Any:
    match = re.search(r"/@([^/]+)/video/(\d+)", urlparse(url).path)
    if match:
        username, video_id = match.groups()
        for selector in (
            f'article:has(a[href*="/video/{video_id}"])',
            f'article:has(a[href^="/@{username}"])',
        ):
            candidate = page.locator(selector)
            if candidate.count():
                return candidate.first
    described = page.locator('article:has([data-e2e="video-desc"])')
    return described.first if described.count() else page.locator("article").first


def fetch_expanded_tiktok_caption(
    url: str,
    *,
    fallback_text: str = "",
    cookies_file: Optional[str] = None,
    timeout_seconds: int = 35,
    executable_path: Optional[str] = None,
) -> Optional[str]:
    """Open TikTok, click "mehr", and return the rendered long caption.

    Returns ``None`` when Playwright/Chromium is unavailable, TikTok blocks
    the browser, or the rendered text is not better than the metadata fallback.
    """
    if not is_tiktok_url(url):
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning(
            "Playwright fehlt; lange TikTok-Caption wird übersprungen. "
            "Installation: pip install playwright && python -m playwright install chromium"
        )
        return None

    timeout_ms = max(5, min(int(timeout_seconds), 90)) * 1_000
    try:
        with sync_playwright() as playwright:
            launch_options: Dict[str, Any] = {
                "headless": True,
                "timeout": timeout_ms,
                "args": ["--disable-dev-shm-usage"],
            }
            if executable_path:
                launch_options["executable_path"] = executable_path
            browser = playwright.chromium.launch(**launch_options)
            try:
                context = browser.new_context(locale="de-DE", viewport={"width": 1280, "height": 900})
                cookies = parse_netscape_cookies(cookies_file)
                if cookies:
                    context.add_cookies(cookies)
                page = context.new_page()
                page.set_default_timeout(min(timeout_ms, 10_000))
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                _dismiss_overlays(page)
                page_title = page.title()

                article = _target_article(page, url)
                article.wait_for(state="visible", timeout=timeout_ms)
                description = article.locator('[data-media-card-description-container="true"]')
                if not description.count():
                    description = article.locator('[data-e2e="video-desc"]')
                before = description.first.inner_text(timeout=3_000) if description.count() else ""

                for label in _MORE_LABELS:
                    try:
                        more = article.get_by_text(label, exact=True)
                        if more.count() and more.first.is_visible():
                            try:
                                more.first.click(timeout=3_000, force=True)
                            except Exception:
                                # TikTok occasionally places a transparent
                                # overlay above the text node. A DOM click still
                                # triggers React's expand handler in that case.
                                more.first.evaluate("element => element.click()")
                            break
                    except Exception:
                        continue

                best_container = before
                best_article = ""
                for _ in range(12):
                    page.wait_for_timeout(250)
                    expanded = article.locator('[data-media-card-description-container="true"]')
                    if expanded.count():
                        candidate = expanded.first.inner_text(timeout=3_000)
                        if len(candidate) > len(best_container):
                            best_container = candidate
                    article_candidate = article.inner_text(timeout=3_000)
                    if len(article_candidate) > len(best_article):
                        best_article = article_candidate
                    article_caption = caption_from_article_text(best_article, page_title)
                    if len(article_caption) >= max(240, len(before) + 100):
                        break

                caption = max(
                    clean_expanded_caption(best_container),
                    caption_from_article_text(best_article, page_title),
                    key=len,
                )
                fallback = clean_expanded_caption(fallback_text)
                if len(caption) < max(160, len(fallback) + 80):
                    logger.info(
                        "TikTok-Caption blieb kurz: container=%s, article=%s, fallback=%s",
                        len(best_container), len(best_article), len(fallback),
                    )
                    return None
                logger.info("Lange TikTok-Caption geladen: %s Zeichen", len(caption))
                return caption
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Lange TikTok-Caption konnte nicht geladen werden: %s", exc)
        return None
