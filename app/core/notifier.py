"""Telegram-Benachrichtigungen.

WICHTIG: Nur senden! Reply-Handling für Pending wurde durch das Web-UI ersetzt.

``send()``      = synchron (blockt bis zu 10 s pro Aufruf)
``send_async()`` = fire-and-forget über einen modul-globalen Thread-Pool. Wird
                   im Scraper-Loop genutzt, damit ein hängender Telegram-Server
                   nicht den ganzen Job-Loop blockiert. Pool-Größe ist klein
                   damit wir bei vielen URLs nicht 50 Connections aufmachen.
"""
from __future__ import annotations

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# Globaler Pool für fire-and-forget. 2 Worker reichen - Telegram limitiert
# eh auf ~30 Msg/s und unser Cron-Lauf macht selten mehr als 50 Calls.
_TG_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram")


def _shutdown_pool() -> None:
    _TG_POOL.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_pool)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, label: str = "telegram"):
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.label = label

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _post(self, text: str, parse_mode: str, disable_preview: bool) -> Optional[int]:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": disable_preview,
                },
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("result", {}).get("message_id")
        except Exception as e:
            logger.error(f"[{self.label}] send fehlgeschlagen: {e}")
            return None

    def send(self, text: str, *, parse_mode: str = "HTML",
             disable_preview: bool = True) -> Optional[int]:
        """Synchron senden. Blockt bis zu 10 s."""
        if not self.enabled:
            logger.debug(f"[{self.label}] disabled, skipping: {text[:60]}")
            return None
        return self._post(text, parse_mode, disable_preview)

    def send_async(self, text: str, *, parse_mode: str = "HTML",
                   disable_preview: bool = True) -> None:
        """Fire-and-forget. Kehrt sofort zurück.

        Wenn Telegram hängt blockiert der Worker-Thread, aber NICHT der
        Caller. Bei vielen sequentiellen Calls (z.B. Scraper-Loop mit
        50 URLs) ist das der spürbar bessere Pfad.
        """
        if not self.enabled:
            logger.debug(f"[{self.label}] disabled, skipping (async): {text[:60]}")
            return
        try:
            _TG_POOL.submit(self._post, text, parse_mode, disable_preview)
        except RuntimeError:
            # Pool wurde im Shutdown geschlossen - fallback sync
            self._post(text, parse_mode, disable_preview)
