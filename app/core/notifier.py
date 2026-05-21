"""
Telegram-Benachrichtigungen.
WICHTIG: Nur senden! Reply-Handling für Pending wurde durch das Web-UI ersetzt.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, label: str = "telegram"):
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.label = label

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, *, parse_mode: str = "HTML",
             disable_preview: bool = True) -> Optional[int]:
        if not self.enabled:
            logger.debug(f"[{self.label}] disabled, skipping: {text[:60]}")
            return None
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
