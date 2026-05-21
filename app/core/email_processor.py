"""
E-Mail-Verarbeitung mit 2 separaten IMAP-Konten:
  - Konto 'recipe'  → alle Links werden als Rezept verarbeitet
  - Konto 'wedding' → alle Links werden als Hochzeit verarbeitet

Kein Betreff-Check mehr nötig!
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from contextlib import contextmanager
from email.header import decode_header
from typing import Iterable, List, Dict, Optional

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.)?(?:tiktok\.com|instagram\.com)/\S+",
    re.IGNORECASE,
)


def _decode_subject(msg) -> str:
    s = msg.get("Subject", "")
    if not s:
        return ""
    parts = decode_header(s)
    out = ""
    for p, charset in parts:
        if isinstance(p, bytes):
            out += p.decode(charset or "utf-8", errors="ignore")
        else:
            out += p
    return out


def _extract_body(msg: email.message.Message) -> str:
    body = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(errors="ignore")
            except Exception:
                pass
        elif part.get_content_type() == "text/html" and not body:
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(errors="ignore")
            except Exception:
                pass
    return body


class MailAccount:
    def __init__(self, name: str, cfg: dict, content_type: str,
                 default_category: Optional[str] = None):
        self.name = name
        self.content_type = content_type  # 'recipe' | 'wedding'
        self.host = cfg.get("imap_host", "imap.gmail.com")
        self.port = int(cfg.get("imap_port", 993))
        self.username = cfg.get("username", "")
        self.password = cfg.get("password", "")
        self.folder = cfg.get("folder", "INBOX")
        self.max_mails = int(cfg.get("max_mails", 20))
        self.default_category = default_category or cfg.get("default_category")
        self.enabled = bool(cfg.get("enabled", True))

    @contextmanager
    def _connect(self):
        mail = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        try:
            mail.login(self.username, self.password)
            mail.select(self.folder)
            yield mail
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def fetch_urls(self) -> List[Dict]:
        if not self.enabled or not self.username or not self.password:
            return []
        # 3 Versuche mit exponentiellem Backoff. Gmail wirft sporadisch
        # "imaplib.error: socket error" oder Auth-Glitches; ein Retry
        # eliminiert die meisten False-Failures.
        last_error: Optional[Exception] = None
        for attempt, sleep_s in enumerate([0, 1, 4], start=1):
            if sleep_s:
                logger.info(f"[{self.name}] IMAP-Retry {attempt}/3 nach {sleep_s}s")
                time.sleep(sleep_s)
            try:
                return self._fetch_urls_once()
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error,
                    OSError, ConnectionError) as e:
                last_error = e
                logger.warning(f"[{self.name}] IMAP-Versuch {attempt} fehlgeschlagen: {e}")
            except Exception as e:
                # Unerwartete Exception: nicht retryen, sofort raus
                logger.error(f"[{self.name}] IMAP-Hardfailure: {e}")
                return []
        logger.error(f"[{self.name}] IMAP nach 3 Versuchen aufgegeben: {last_error}")
        return []

    def _fetch_urls_once(self) -> List[Dict]:
        """Ein einziger Fetch-Durchgang. Wirft Exceptions, die fetch_urls retryt."""
        results: List[Dict] = []
        seen: set[str] = set()
        with self._connect() as mail:
            _, data = mail.search(None, "ALL")
            ids = data[0].split()[-self.max_mails:]
            logger.info(f"[{self.name}] Verarbeite {len(ids)} Mails")
            for mid in ids:
                try:
                    _, msg_data = mail.fetch(mid, "(RFC822)")
                    if not msg_data or not msg_data[0]:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    subject = _decode_subject(msg)
                    body = _extract_body(msg)
                    full = f"{subject}\n{body}"
                    for url in URL_PATTERN.findall(full):
                        url = url.rstrip(".,);]>'\"")
                        if url in seen:
                            continue
                        seen.add(url)
                        results.append({
                            "url": url,
                            "type": self.content_type,
                            "subject": subject,
                            "default_category": self.default_category,
                            "source_account": self.name,
                        })
                except Exception as e:
                    logger.warning(f"[{self.name}] Mail {mid}: {e}")
        logger.info(f"[{self.name}] {len(results)} URLs gefunden")
        return results


class EmailRouter:
    """Bündelt mehrere MailAccounts."""

    def __init__(self, accounts: Iterable[MailAccount]):
        self.accounts = list(accounts)

    def fetch_all(self) -> List[Dict]:
        out: List[Dict] = []
        seen: set[str] = set()
        for acc in self.accounts:
            for item in acc.fetch_urls():
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                out.append(item)
        return out
