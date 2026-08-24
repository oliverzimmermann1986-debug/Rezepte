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
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"https://(?:www\.|m\.|vm\.|vt\.)?(?:tiktok\.com|instagram\.com)/\S+",
    re.IGNORECASE,
)


_TIKTOK_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "vm.tiktok.com", "vt.tiktok.com",
}
_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}


def normalize_content_url(url: str) -> Optional[str]:
    """Validiert und normalisiert einen einzelnen TikTok-/Instagram-Post.

    Profil-Links wie ``tiktok.com/@chefkoch`` oder ``instagram.com/handle`` lassen
    wir nicht als Rezeptquelle zu. Exakte Host-Prüfung verhindert Verwechslungen
    wie ``instagram.com.evil.example`` oder eingebettete Zugangsdaten. Tracking-
    Parameter und Fragmente werden nicht gespeichert.
    """
    try:
        parsed = urlsplit((url or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None

    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path or "/"
    path_lower = path.lower()
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        if path == "/":
            return None
    elif host in _TIKTOK_HOSTS:
        if "/video/" not in path_lower and "/photo/" not in path_lower:
            return None
    elif host in _INSTAGRAM_HOSTS:
        if not any(marker in path_lower for marker in ("/reel/", "/p/", "/tv/")):
            return None
    else:
        return None

    return urlunsplit(("https", host, path, "", ""))


def is_content_url(url: str) -> bool:
    """Kompatibilitätshelfer für bestehende Aufrufer."""
    return normalize_content_url(url) is not None

# Attachment-Filename-Endungen die wir verarbeiten. Alles andere wird
# ignoriert (PDFs für Rezept-Karten/Hochzeitspläne, JPGs für Fotos).
ATTACHMENT_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
DEFAULT_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024


def _decode_attachment_payload(part, max_bytes: int) -> Optional[bytes]:
    """Dekodiert einen MIME-Part nur, wenn er innerhalb des Limits liegt."""
    raw = part.get_payload(decode=False)
    transfer_encoding = (part.get("Content-Transfer-Encoding") or "").lower()
    if transfer_encoding == "base64" and isinstance(raw, (str, bytes)):
        non_whitespace = sum(
            1 for char in raw
            if not (char.isspace() if isinstance(char, str) else chr(char).isspace())
        )
        if non_whitespace > ((max_bytes + 2) * 4 // 3 + 4):
            return None
    payload = part.get_payload(decode=True)
    if not payload or len(payload) > max_bytes:
        return None
    return payload


def _decode_filename(part) -> str:
    """Filename aus Content-Disposition oder Content-Type ziehen + dekodieren."""
    raw = part.get_filename()
    if not raw:
        return ""
    parts = decode_header(raw)
    out = ""
    for p, charset in parts:
        if isinstance(p, bytes):
            out += p.decode(charset or "utf-8", errors="ignore")
        else:
            out += p
    return out


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


def _html_to_text_and_urls(html: str) -> str:
    """Zieht aus einer HTML-Mail sowohl den sichtbaren Text als auch die
    ``href``-Attribute aller ``<a>``-Tags. Letztere werden ans Ende des Texts
    angehängt - so erwischt das URL-Pattern auch URLs, die in ``<a href="...">``
    versteckt waren (z.B. „Hier klicken")."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Fallback: BeautifulSoup nicht installiert -> Roh-HTML zurück
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()
        text = soup.get_text(separator="\n")
        hrefs = [a.get("href", "") for a in soup.find_all("a") if a.get("href")]
        if hrefs:
            text += "\n\n" + "\n".join(hrefs)
        return text
    except Exception:
        return html


def _extract_body(msg: email.message.Message) -> str:
    """Extrahiert den Body als reinen Text. HTML-Parts werden via BeautifulSoup
    in Text + extrahierte Hrefs umgewandelt, damit auch URLs in
    ``<a href="...">``-Tags erfasst werden."""
    text_parts: List[str] = []
    html_parts: List[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain":
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    text_parts.append(payload.decode(errors="ignore"))
            except Exception:
                pass
        elif ctype == "text/html":
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    html_parts.append(payload.decode(errors="ignore"))
            except Exception:
                pass
    out = "\n".join(text_parts)
    # HTML nur dazumergen wenn Plain-Text leer ist ODER zusätzliche URLs
    # liefert (Newsletter haben oft beides, Hauptinhalt im HTML).
    if html_parts:
        html_text = _html_to_text_and_urls("\n".join(html_parts))
        out = (out + "\n" + html_text) if out.strip() else html_text
    return out


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
        max_attachment_mb = int(cfg.get("attachment_max_mb", 25) or 25)
        self.attachment_max_bytes = max(1, min(100, max_attachment_mb)) * 1024 * 1024
        self.default_category = default_category or cfg.get("default_category")
        self.enabled = bool(cfg.get("enabled", True))
        # Verarbeitete Mails nach dem Lauf löschen (\Deleted + EXPUNGE).
        # Bewusst config-gated — destruktiv. Retries brauchen die Mail nicht
        # mehr (kommen aus download_failures).
        self.delete_processed = bool(cfg.get("delete_processed", False))

    @contextmanager
    def _connect(self, *, readonly: bool = False):
        mail = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        try:
            mail.login(self.username, self.password)
            mail.select(self.folder, readonly=readonly)
            yield mail
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def fetch_urls(self) -> List[Dict]:
        """Holt URLs aus Mails (Bestandsverhalten)."""
        result = self.fetch_all()
        return result.get("urls", [])

    def fetch_all(self) -> Dict[str, List[Dict]]:
        """Holt URLs UND Attachments. Returnt {'urls': [...], 'attachments': [...]}."""
        return self._fetch_all_with_retries(readonly=False)

    def fetch_all_readonly(
        self,
        *,
        max_mails: Optional[int] = None,
        include_attachments: bool = True,
    ) -> Dict[str, List[Dict]]:
        """Inventarisiert Mails, ohne Flags oder Mailbox-Inhalt zu verändern.

        IMAP ``EXAMINE`` (``readonly=True``) und ``BODY.PEEK[]`` verhindern,
        dass der Abgleich Nachrichten als gelesen markiert. Diese Methode ist
        für Audits/Reconciliation gedacht und löscht niemals Mails.
        """
        return self._fetch_all_with_retries(
            readonly=True,
            max_mails=max_mails,
            include_attachments=include_attachments,
        )

    def _fetch_all_with_retries(
        self,
        *,
        readonly: bool,
        max_mails: Optional[int] = None,
        include_attachments: bool = True,
    ) -> Dict[str, List[Dict]]:
        if not self.enabled or not self.username or not self.password:
            return {"urls": [], "attachments": []}
        # 3 Versuche mit exponentiellem Backoff. Gmail wirft sporadisch
        # "imaplib.error: socket error" oder Auth-Glitches; ein Retry
        # eliminiert die meisten False-Failures.
        last_error: Optional[Exception] = None
        for attempt, sleep_s in enumerate([0, 1, 4], start=1):
            if sleep_s:
                logger.info(f"[{self.name}] IMAP-Retry {attempt}/3 nach {sleep_s}s")
                time.sleep(sleep_s)
            try:
                return self._fetch_all_once(
                    readonly=readonly,
                    max_mails=max_mails,
                    include_attachments=include_attachments,
                )
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error,
                    OSError, ConnectionError) as e:
                last_error = e
                logger.warning(f"[{self.name}] IMAP-Versuch {attempt} fehlgeschlagen: {e}")
            except Exception as e:
                # Unerwartete Exception: nicht retryen, sofort raus
                logger.error(f"[{self.name}] IMAP-Hardfailure: {e}")
                return {"urls": [], "attachments": []}
        logger.error(f"[{self.name}] IMAP nach 3 Versuchen aufgegeben: {last_error}")
        return {"urls": [], "attachments": []}

    def _fetch_all_once(
        self,
        *,
        readonly: bool = False,
        max_mails: Optional[int] = None,
        include_attachments: bool = True,
    ) -> Dict[str, List[Dict]]:
        """Ein Fetch-Durchgang. Liest URLs aus Body + Attachments aus PDF/JPG."""
        urls: List[Dict] = []
        attachments: List[Dict] = []
        seen_urls: set[str] = set()
        seen_attach: set[str] = set()   # (mail-msgid, filename) Tupel-Hash

        with self._connect(readonly=readonly) as mail:
            _, data = mail.search(None, "ALL")
            limit = self.max_mails if max_mails is None else max(1, int(max_mails))
            ids = data[0].split()[-limit:]
            logger.info(f"[{self.name}] Verarbeite {len(ids)} Mails")
            for mid in ids:
                try:
                    fetch_query = "(BODY.PEEK[])" if readonly else "(RFC822)"
                    _, msg_data = mail.fetch(mid, fetch_query)
                    if not msg_data or not msg_data[0]:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    subject = _decode_subject(msg)
                    body = _extract_body(msg)
                    full = f"{subject}\n{body}"
                    msg_id = (msg.get("Message-ID") or "").strip() or f"mid-{mid.decode() if isinstance(mid, bytes) else mid}"
                    mail_uid = mid.decode() if isinstance(mid, bytes) else str(mid)

                    # 1. URLs aus Body
                    for url in URL_PATTERN.findall(full):
                        url = url.rstrip(".,);]>'\"")
                        url = normalize_content_url(url)
                        if not url:
                            logger.info(f"[{self.name}] Profil-/Nicht-Post-URL übersprungen")
                            continue
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        urls.append({
                            "url": url,
                            "type": self.content_type,
                            "subject": subject,
                            "default_category": self.default_category,
                            "source_account": self.name,
                            "mail_uid": mail_uid,
                        })

                    # 2. Attachments (PDF/JPG/PNG)
                    if include_attachments and msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            disp = (part.get("Content-Disposition") or "").lower()
                            if "attachment" not in disp and "inline" not in disp:
                                continue
                            fname = _decode_filename(part)
                            if not fname:
                                continue
                            ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                            if ext not in ATTACHMENT_EXTS:
                                continue
                            # Dedupe via msg_id+filename
                            dedupe_key = f"{msg_id}::{fname}"
                            if dedupe_key in seen_attach:
                                continue
                            seen_attach.add(dedupe_key)
                            payload = _decode_attachment_payload(
                                part, self.attachment_max_bytes,
                            )
                            if not payload:
                                logger.warning(
                                    "[%s] Anhang %s ist leer oder größer als %s MB und wird übersprungen",
                                    self.name, fname, self.attachment_max_bytes // (1024 * 1024),
                                )
                                continue
                            attachments.append({
                                "msg_id": msg_id,
                                "filename": fname,
                                "ext": ext,
                                "content_type": ctype,
                                "data": payload,             # bytes
                                "size": len(payload),
                                "type": self.content_type,   # 'recipe' | 'wedding'
                                "subject": subject,
                                "body_excerpt": body[:500],  # Hinweis für die KI
                                "default_category": self.default_category,
                                "source_account": self.name,
                                "mail_uid": mail_uid,
                            })

                except Exception as e:
                    logger.warning(f"[{self.name}] Mail {mid}: {e}")

        logger.info(f"[{self.name}] {len(urls)} URLs + {len(attachments)} Attachments gefunden")
        return {"urls": urls, "attachments": attachments}

    # Bestands-Methode für Backwards-Compat (von Tests / CLI aufgerufen)
    def _fetch_urls_once(self) -> List[Dict]:
        return self._fetch_all_once().get("urls", [])

    def delete_mails(self, mail_uids: Iterable[str]) -> int:
        """Markiert die Mails als \\Deleted und expunged. Returnt Anzahl.
        Gmail: je nach IMAP-Einstellung wandert die Mail in den Papierkorb
        oder nur aus der INBOX (Archiv) — in beiden Fällen liest der
        nächste Lauf sie nicht mehr."""
        uids = [u for u in mail_uids if u]
        if not uids or not self.delete_processed:
            return 0
        deleted = 0
        try:
            with self._connect() as mail:
                for uid in uids:
                    try:
                        mail.store(uid, "+FLAGS", "\\Deleted")
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"[{self.name}] Delete Mail {uid}: {e}")
                mail.expunge()
        except Exception as e:
            logger.warning(f"[{self.name}] Mail-Delete fehlgeschlagen (non-fatal): {e}")
        if deleted:
            logger.info(f"[{self.name}] {deleted} verarbeitete Mails gelöscht")
        return deleted


class EmailRouter:
    """Bündelt mehrere MailAccounts."""

    def __init__(self, accounts: Iterable[MailAccount]):
        self.accounts = list(accounts)

    def fetch_all(self) -> List[Dict]:
        """Bestandsverhalten: alle URLs aus allen Konten (deduped per URL)."""
        out: List[Dict] = []
        seen: set[str] = set()
        for acc in self.accounts:
            for item in acc.fetch_urls():
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                out.append(item)
        return out

    def fetch_all_with_attachments(self) -> Dict[str, List[Dict]]:
        """Sammelt URLs + Attachments. URLs deduped via Set, Attachments via msg_id+filename."""
        urls: List[Dict] = []
        attachments: List[Dict] = []
        seen_urls: set[str] = set()
        seen_attach: set[str] = set()
        for acc in self.accounts:
            r = acc.fetch_all()
            for item in r.get("urls", []):
                if item["url"] in seen_urls:
                    continue
                seen_urls.add(item["url"])
                urls.append(item)
            for att in r.get("attachments", []):
                key = f"{att['msg_id']}::{att['filename']}"
                if key in seen_attach:
                    continue
                seen_attach.add(key)
                attachments.append(att)
        return {"urls": urls, "attachments": attachments}

    def delete_processed_mails(self, uids_by_account: Dict[str, set]) -> int:
        """Löscht verarbeitete Mails pro Konto (nur Konten mit delete_processed=true)."""
        total = 0
        for acc in self.accounts:
            uids = uids_by_account.get(acc.name) or set()
            if uids:
                total += acc.delete_mails(sorted(uids))
        return total
