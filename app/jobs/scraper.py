"""
Scraper-Job (TikTok/Instagram -> Rezepte/Hochzeit Ordner).

KI-Cascade ist seit dem Ollama-Removal flat:
  OpenAI-Call -> Pending (manuell im Web-UI) wenn confidence zu niedrig

Pending-Items werden im Web-UI über ein <video>-Element angezeigt -
keine Standbild-Extraktion mehr nötig.

Pre-Analyse-Schritt: nicht-deutsche Captions werden automatisch nach
Deutsch übersetzt (siehe _maybe_translate_description). Das Original
bleibt als description_original.txt im Rezept-Ordner erhalten.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config_store import get_config
from ..db import get_db
from ..core.analyzer import RecipeAnalysis, WeddingAnalysis, build_analyzer
from ..core.downloader import VideoDownloader
from ..core.email_processor import MailAccount, EmailRouter

logger = logging.getLogger(__name__)


# Cancel-Flag (modul-global, threading-safe). Wird im Web-Trigger und beim
# Job-Start reset, vom Cancel-Endpoint gesetzt, im run()-Loop pro URL geprüft.
_CANCEL_EVENT = threading.Event()

# Max yt-dlp Versuche bevor URL als 'download_failed' in history landet
MAX_DOWNLOAD_ATTEMPTS = 3


def cancel_job() -> dict:
    """Setzt das Cancel-Flag. Der laufende Scraper bricht beim nächsten
    URL-Check ab. Nicht-blockierend - kein subprocess wird hier gekillt
    (yt-dlp läuft, fertige URLs werden komplett verarbeitet)."""
    _CANCEL_EVENT.set()
    return {"ok": True}


def is_cancelled() -> bool:
    return _CANCEL_EVENT.is_set()


def reset_cancel() -> None:
    _CANCEL_EVENT.clear()


def _sanitize(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name or "Unbekannt"


def _has_usable_description(text: Optional[str], min_len: int) -> bool:
    if not text:
        return False
    clean = re.sub(r"#\S+|https?://\S+", "", text).strip()
    return len(clean) >= min_len


def _save_video_files(target_dir: Path, video_path: Path,
                       description: Optional[str], info: Dict,
                       description_original: Optional[str] = None) -> None:
    """Schreibt Video + description.txt + info.json in den Ziel-Ordner.

    Wenn description_original gesetzt ist (= Caption wurde übersetzt), wird
    sie als description_original.txt zusätzlich geschrieben. So bleibt das
    Original erhalten für späteres Audit oder Re-Übersetzung.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    file_base = target_dir.name
    if video_path and video_path.exists():
        shutil.copy2(video_path, target_dir / f"{file_base}{video_path.suffix}")
        # yt-dlp legt das Cover als video.jpg neben das Video (--write-thumbnail).
        # Mitkopieren als thumb.jpg → Indexer setzt thumb_filename, kein "Kein Bild".
        for t in sorted(video_path.parent.glob("*.jpg")) + sorted(video_path.parent.glob("*.webp")) + sorted(video_path.parent.glob("*.png")):
            shutil.copy2(t, target_dir / f"thumb{t.suffix}")
            break
    if description:
        (target_dir / "description.txt").write_text(description, encoding="utf-8")
    if description_original:
        (target_dir / "description_original.txt").write_text(description_original, encoding="utf-8")
    with open(target_dir / "info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)


class ScraperJob:
    def __init__(self):
        cfg = get_config()
        self.cfg = cfg
        self.db = get_db()

        # Pfade
        self.recipe_dir = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte"))
        self.wedding_dir = Path(cfg.get("paths", "wedding_dir", default="/mnt/hochzeit"))
        self.temp_dir = Path(cfg.get("paths", "temp_dir", default="/opt/scrapper/temp"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # AI-Provider: nur noch OpenAI. build_analyzer() liefert einen
        # OpenAIAnalyzer (oder raised wenn api_key fehlt/gemaskt ist).
        ai_cfg = cfg.get("ai", default={}) or {}
        self.ai_provider = "openai"

        try:
            self.analyzer = build_analyzer(ai_cfg)
            self.analyzer_enabled = True
        except Exception as e:
            logger.error(f"AI-Analyzer Init fehlgeschlagen: {e}")
            self.analyzer = None
            self.analyzer_enabled = False

        self.confidence_threshold = float(cfg.get("ai", "confidence_threshold", default=0.75) or 0.75)
        # fallback_threshold bleibt für Bestandskonfigs lesbar aber wird nicht
        # mehr genutzt (kein zweites Modell mehr seit Ollama-Removal).
        self.min_desc_len = int(cfg.get("ai", "description_min_length", default=20) or 20)
        # Auto-Translate: bei Default true; lässt sich per Config deaktivieren
        # falls jemand das Original behalten will.
        self.auto_translate = bool(cfg.get("ai", "auto_translate", default=True))

        # Downloader (mit optionalem Cookie-Jar für private Inhalte)
        ytdlp_cfg = cfg.get("ytdlp", default={}) or {}
        self.downloader = VideoDownloader(
            ytdlp_cfg.get("binary", "/opt/scrapper/venv/bin/yt-dlp"),
            self.temp_dir,
            cookies_file=ytdlp_cfg.get("cookies_file") or None,
        )

        # E-Mail Konten
        mail_cfg = cfg.get("mail", default={}) or {}
        accounts = []
        if mail_cfg.get("recipe"):
            accounts.append(MailAccount("recipe", mail_cfg["recipe"], "recipe"))
        if mail_cfg.get("wedding"):
            accounts.append(MailAccount(
                "wedding", mail_cfg["wedding"], "wedding",
                default_category=mail_cfg["wedding"].get("default_category", "Sonstiges"),
            ))
        self.router = EmailRouter(accounts)

        self.wedding_always_pending = bool(
            (mail_cfg.get("wedding") or {}).get("always_pending", False)
        )

        self.wedding_categories = cfg.get(
            "wedding_categories",
            default=["Deko", "Foto", "Basteln", "Einladung", "Standesamt", "Sonstiges"],
        )

    # ---------------- Analyse (OpenAI-only) ----------------
    def _analyze_recipe(self, description: Optional[str]) -> RecipeAnalysis:
        """Single OpenAI-Call. Bei niedriger confidence → Pending im Web-UI."""
        if self.analyzer and _has_usable_description(description, self.min_desc_len):
            r = self.analyzer.analyze_recipe(description)
            logger.info(f"AI recipe: name={r.name} typ={r.type} conf={r.confidence:.2f}")
            if not r.needs_manual_input(self.confidence_threshold):
                return r
            return r  # auch das schwache Ergebnis durchgeben; needs_manual_input
                      # wird vom Aufrufer ausgewertet um Pending-Zweig zu wählen
        return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)

    def _analyze_wedding(self, description: Optional[str]) -> WeddingAnalysis:
        if self.analyzer and _has_usable_description(description, self.min_desc_len):
            w = self.analyzer.analyze_wedding(description, self.wedding_categories)
            logger.info(f"AI wedding: name={w.name} cat={w.category} conf={w.confidence:.2f}")
            return w
        return WeddingAnalysis("Unbekannt", None, 0.0)

    def _maybe_translate_description(self, description: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Versucht die Caption nach Deutsch zu übersetzen.

        Returns: (final_description, original_or_None)
          - final_description: was wir weiter verarbeiten (deutsch wenn übersetzt,
            sonst Original)
          - original_or_None: Original-Text WENN übersetzt wurde, sonst None
            (Aufrufer entscheidet ob description_original.txt geschrieben wird)
        """
        if not self.auto_translate or not self.analyzer:
            return description, None
        if not description or len(description.strip()) < self.min_desc_len:
            return description, None
        try:
            translated = self.analyzer.translate_to_german(description)
        except Exception as e:
            logger.warning(f"Translate-Call fehlgeschlagen, behalte Original: {e}")
            return description, None
        if translated:
            logger.info(f"Caption übersetzt: {len(description)}→{len(translated)} chars")
            return translated, description
        return description, None

    # ---------------- Save ----------------
    def _save_recipe(self, r: RecipeAnalysis, url: str, video: Path,
                     description: Optional[str]) -> Path:
        # Pre-Save Translate: nicht-deutsche Captions auf Deutsch
        # umsetzen, Original als Side-File aufheben.
        description, description_original = self._maybe_translate_description(description)
        type_n = _sanitize(r.type)
        cat_n = _sanitize(r.category or "Allgemein")
        name_n = _sanitize(r.name)
        target = self.recipe_dir / type_n / cat_n / name_n
        if target.exists():
            target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
        info = {
            "url": url, "name": r.name, "type": r.type, "category": r.category,
            "confidence": r.confidence, "is_manual": r.is_manual,
            "content_type": "recipe", "description": description,
            "timestamp": datetime.now().isoformat(),
        }
        if description_original:
            info["description_original"] = description_original
            info["translated"] = True
        _save_video_files(target, video, description, info, description_original)
        return target

    def _save_wedding(self, w: WeddingAnalysis, url: str, video: Path,
                      description: Optional[str], default_cat: str = "Sonstiges") -> Path:
        description, description_original = self._maybe_translate_description(description)
        cat = _sanitize(w.category or default_cat)
        name_n = _sanitize(w.name) if w.name.lower() != "unbekannt" \
            else f"Hochzeit_{datetime.now():%Y%m%d_%H%M%S}"
        target = self.wedding_dir / cat / name_n
        if target.exists():
            target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
        info = {
            "url": url, "name": w.name, "wedding_category": w.category or default_cat,
            "confidence": w.confidence, "is_manual": w.is_manual,
            "content_type": "wedding", "description": description,
            "timestamp": datetime.now().isoformat(),
        }
        if description_original:
            info["description_original"] = description_original
            info["translated"] = True
        _save_video_files(target, video, description, info, description_original)
        return target

    # ---------------- URL-Verarbeitung ----------------
    def process_url(self, item: Dict) -> Dict:
        url = item["url"]
        content_type = item["type"]
        result: Dict = {"url": url, "type": content_type, "status": "error"}

        video = self.downloader.download(url)
        if not video:
            # Download-Fehler: Versuch zählen. Nach MAX_DOWNLOAD_ATTEMPTS
            # wird die URL im run()-Loop als 'aufgegeben' history_add'd.
            self.db.download_failure_record(url, "yt-dlp Download fehlgeschlagen")
            result["error"] = "download failed"
            return result

        # Download geklappt - falls die URL frühere Fehlversuche hatte, jetzt löschen
        self.db.download_failure_clear(url)
        description = self.downloader.read_description(video)

        try:
            if content_type == "recipe":
                r = self._analyze_recipe(description)
                if r.needs_manual_input(self.confidence_threshold):
                    pending_video = self._stash_for_pending(video)
                    self.db.pending_add(
                        url=url, content_type="recipe",
                        description=description,
                        video_path=str(pending_video) if pending_video else None,
                        ai_suggestion={
                            "name": r.name, "type": r.type,
                            "category": r.category, "confidence": r.confidence,
                        },
                    )
                    result.update({"status": "pending", "name": r.name})
                else:
                    target = self._save_recipe(r, url, video, description)
                    self.db.history_add(url, content_type="recipe", name=r.name,
                                         target_dir=str(target))
                    result.update({"status": "auto", "name": r.name, "target": str(target)})
            else:  # wedding
                default_cat = item.get("default_category") or "Sonstiges"
                w = self._analyze_wedding(description)
                if w.needs_manual_input(self.confidence_threshold) or self.wedding_always_pending:
                    pending_video = self._stash_for_pending(video)
                    self.db.pending_add(
                        url=url, content_type="wedding",
                        description=description,
                        video_path=str(pending_video) if pending_video else None,
                        ai_suggestion={
                            "name": w.name, "category": w.category or default_cat,
                            "confidence": w.confidence,
                        },
                    )
                    result.update({"status": "pending", "name": w.name})
                else:
                    target = self._save_wedding(w, url, video, description, default_cat)
                    self.db.history_add(url, content_type="wedding", name=w.name,
                                         target_dir=str(target))
                    result.update({"status": "auto", "name": w.name, "target": str(target)})
        finally:
            self._cleanup_temp(video)

        return result

    # ---------------- Mail-Attachments (PDF + JPG/PNG) ----------------

    def _extract_pdf_text(self, pdf_bytes: bytes) -> Optional[str]:
        """PDF -> Text. Versucht pdfplumber zuerst (besseres Layout-Handling),
        fällt auf pypdf zurück wenn pdfplumber nicht installiert ist."""
        try:
            import pdfplumber
            from io import BytesIO
            text_parts = []
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                # Erste 5 Seiten reichen meist - Rezepte/Hochzeitspläne sind kurz
                for page in pdf.pages[:5]:
                    t = page.extract_text() or ""
                    if t:
                        text_parts.append(t)
            return "\n".join(text_parts).strip() or None
        except ImportError:
            pass
        try:
            import pypdf
            from io import BytesIO
            reader = pypdf.PdfReader(BytesIO(pdf_bytes))
            return "\n".join(p.extract_text() or "" for p in reader.pages[:5]).strip() or None
        except ImportError:
            logger.warning("Weder pdfplumber noch pypdf installiert - PDF-Text-Extract nicht möglich")
            return None
        except Exception as e:
            logger.warning(f"PDF-Extract fail: {e}")
            return None

    def _analyze_image_via_openai(self, image_bytes: bytes, mime: str,
                                    content_type: str, subject: str,
                                    categories: list = None):
        """OpenAI Vision-API für JPG/PNG-Attachments aus Mails. Schickt das
        Bild als base64 mit. Returnt RecipeAnalysis oder WeddingAnalysis.
        """
        if not self.analyzer:
            return None

        import base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        if content_type == "recipe":
            system = (
                "Du analysierst Bilder von Rezept-Karten oder Food-Fotos. "
                "Erkenne Rezeptname, Typ (Hauptgericht, Vorspeise, Nachspeise, Snack, "
                "Frühstück, Getränk, Beilage) und Unterkategorie (Pasta, Fleisch, Fisch, "
                "Vegetarisch, Vegan, Kuchen, Suppe). Antworte AUSSCHLIESSLICH mit gültigem JSON: "
                '{"rezeptname":"...","typ":"...","kategorie":"...","confidence":0.85}. '
                "Bei Unsicherheit nutze 'Unbekannt'."
            )
        else:
            cats = ", ".join(categories or [])
            system = (
                "Du analysierst Bilder von Hochzeits-Content "
                f"(Deko, Foto, Basteln, Einladung, etc.). Mögliche Kategorien: {cats}. "
                "Erstelle einen kurzen deutschen Namen (max 5 Wörter) UND wähle die passendste "
                "Kategorie aus der Liste. Antworte AUSSCHLIESSLICH mit gültigem JSON: "
                '{"name":"Kurzer Name","kategorie":"Deko","confidence":0.85}. '
            )

        user_content = [
            {"type": "text", "text": f"Mail-Subject: {subject}"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

        try:
            import requests
            r = self.analyzer.session.post(
                f"{self.analyzer.base_url}/chat/completions",
                json={
                    "model": self.analyzer.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
                timeout=self.analyzer.timeout,
            )
            r.raise_for_status()
            content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not content:
                return None
            data = json.loads(content)
            if content_type == "recipe":
                return RecipeAnalysis.from_dict(data)
            return WeddingAnalysis(
                name=data.get("name") or "Unbekannt",
                category=data.get("kategorie") or data.get("category"),
                confidence=float(data.get("confidence", 0)),
            )
        except Exception as e:
            logger.warning(f"OpenAI Vision fail: {e}")
            return None

    def _save_attachment_file(self, target_dir: Path, attachment_data: bytes,
                                ext: str, info: Dict, source_text: Optional[str] = None) -> None:
        """Schreibt die Attachment-Datei + info.json + optional die extrahierte
        Text-Description in den target_dir."""
        target_dir.mkdir(parents=True, exist_ok=True)
        file_base = target_dir.name
        (target_dir / f"{file_base}{ext}").write_bytes(attachment_data)
        if source_text:
            (target_dir / "description.txt").write_text(source_text, encoding="utf-8")
        with open(target_dir / "info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)

    def process_attachment(self, att: Dict, synth_url: str) -> Dict:
        """Verarbeitet ein Mail-Attachment (PDF/JPG/PNG):

        - PDF: Text via pdfplumber/pypdf extrahieren, durch Text-Analyzer
        - JPG/PNG: bei OpenAI-Provider via Vision-API; sonst Subject-Fallback
        - Ergebnis wie Video-Pipeline: Auto-Save bei hoher Confidence, sonst Pending
        """
        ext = att["ext"]
        content_type = att["type"]
        data = att["data"]
        subject = att.get("subject", "")
        body_excerpt = att.get("body_excerpt", "")
        default_cat = att.get("default_category") or "Sonstiges"
        result: Dict = {"url": synth_url, "type": content_type, "status": "error"}

        # Description bestimmen
        if ext == ".pdf":
            description = self._extract_pdf_text(data) or f"{subject}\n\n{body_excerpt}"
        else:  # .jpg / .jpeg / .png
            description = f"{subject}\n\n{body_excerpt}".strip()

        if not description and ext != ".pdf":
            # Kein Text greifbar
            description = subject or "(kein Subject)"

        try:
            if content_type == "recipe":
                # Bei JPG/PNG + OpenAI-Provider: Vision-Call
                analysis = None
                if ext in (".jpg", ".jpeg", ".png"):
                    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
                    analysis = self._analyze_image_via_openai(data, mime, "recipe", subject)
                if not analysis:
                    analysis = self._analyze_recipe(description)

                if analysis.needs_manual_input(self.confidence_threshold):
                    self.db.pending_add(
                        url=synth_url, content_type="recipe",
                        description=description[:5000],
                        video_path=None,   # kein Video bei Attachments
                        ai_suggestion={
                            "name": analysis.name, "type": analysis.type,
                            "category": analysis.category, "confidence": analysis.confidence,
                            "source": "mail-attachment", "filename": att["filename"],
                        },
                    )
                    result.update({"status": "pending", "name": analysis.name})
                else:
                    type_n = _sanitize(analysis.type)
                    cat_n = _sanitize(analysis.category or "Allgemein")
                    name_n = _sanitize(analysis.name)
                    target = self.recipe_dir / type_n / cat_n / name_n
                    if target.exists():
                        target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
                    info = {
                        "url": synth_url, "name": analysis.name, "type": analysis.type,
                        "category": analysis.category, "confidence": analysis.confidence,
                        "content_type": "recipe", "source": "mail-attachment",
                        "filename": att["filename"], "mail_subject": subject,
                        "description": description[:5000],
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._save_attachment_file(target, data, ext, info, description)
                    self.db.history_add(synth_url, content_type="recipe",
                                        name=analysis.name, target_dir=str(target))
                    result.update({"status": "auto", "name": analysis.name, "target": str(target)})

            else:  # wedding
                analysis = None
                if ext in (".jpg", ".jpeg", ".png"):
                    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
                    analysis = self._analyze_image_via_openai(
                        data, mime, "wedding", subject,
                        categories=self.wedding_categories,
                    )
                if not analysis:
                    analysis = self._analyze_wedding(description)

                if analysis.needs_manual_input(self.confidence_threshold) or self.wedding_always_pending:
                    self.db.pending_add(
                        url=synth_url, content_type="wedding",
                        description=description[:5000],
                        video_path=None,
                        ai_suggestion={
                            "name": analysis.name, "category": analysis.category or default_cat,
                            "confidence": analysis.confidence,
                            "source": "mail-attachment", "filename": att["filename"],
                        },
                    )
                    result.update({"status": "pending", "name": analysis.name})
                else:
                    cat = _sanitize(analysis.category or default_cat)
                    name_n = _sanitize(analysis.name) if analysis.name.lower() != "unbekannt" \
                        else f"Mail_{datetime.now():%Y%m%d_%H%M%S}"
                    target = self.wedding_dir / cat / name_n
                    if target.exists():
                        target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
                    info = {
                        "url": synth_url, "name": analysis.name,
                        "wedding_category": analysis.category or default_cat,
                        "confidence": analysis.confidence,
                        "content_type": "wedding", "source": "mail-attachment",
                        "filename": att["filename"], "mail_subject": subject,
                        "description": description[:5000],
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._save_attachment_file(target, data, ext, info, description)
                    self.db.history_add(synth_url, content_type="wedding",
                                        name=analysis.name, target_dir=str(target))
                    result.update({"status": "auto", "name": analysis.name, "target": str(target)})
        except Exception as e:
            logger.exception(f"process_attachment fail {att.get('filename')}: {e}")
            result["error"] = str(e)

        return result
        """Kopiert das Video nach temp_dir/pending/ damit es das Cleanup überlebt."""
        if not video or not video.exists():
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        pending_root = self.temp_dir / "pending"
        pending_root.mkdir(parents=True, exist_ok=True)
        dst = pending_root / f"{ts}_video{video.suffix}"
        shutil.copy2(video, dst)
        return dst

    def _stash_for_pending(self, video: Path) -> Optional[str]:
        """Kopiert das Temp-Video an einen persistenten Pending-Ort, da der
        Temp-Download-Ordner danach via _cleanup_temp gelöscht wird. Rückgabe
        = Pfad für pending.video_path (Auslieferung via /api/pending/video,
        Aufräumen via _remove_pending_files). None bei Fehler — Pending-Eintrag
        bleibt dann ohne Video, aber der Lauf crasht nicht."""
        try:
            pending_dir = self.temp_dir / "pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            dest = pending_dir / f"{video.parent.name}{video.suffix or '.mp4'}"
            shutil.copy2(video, dest)
            return str(dest)
        except Exception as e:
            logger.warning(f"Stash für Pending fehlgeschlagen ({video}): {e}")
            return None

    def _cleanup_temp(self, video: Path) -> None:
        try:
            if video and video.parent.exists() and video.parent.parent == self.temp_dir:
                shutil.rmtree(video.parent, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Cleanup: {e}")

    # ---------------- Hauptlauf ----------------
    def run(self) -> Dict:
        start = time.time()
        summary = {
            "started_at": datetime.now().isoformat(),
            "fetched": 0, "new": 0, "auto": 0, "pending": 0,
            "errors": 0, "cancelled": False, "skipped_failed": 0,
            "recipe_auto": 0, "recipe_pending": 0,
            "wedding_auto": 0, "wedding_pending": 0,
        }

        # AI-Health-Check vor dem Loop. Wenn der Analyzer tot ist landen sonst
        # ALLE URLs in Pending (weil analyze_* leer zurückkommt) - das wollen
        # wir verhindern und stattdessen den Job sofort als 'error' beenden,
        # damit keine 50 Pending-Items entstehen und keine Videos sinnlos
        # gedownloaded werden.
        if self.analyzer_enabled and self.analyzer and not self.analyzer.health():
            msg = (f"OpenAI nicht erreichbar oder Modell '{self.analyzer.model}' nicht verfügbar - "
                   f"Details im Server-Log (api_key gültig? Internet vom Container? Billing aktiv?). "
                   f"Job abgebrochen damit nicht alle URLs in Pending landen")
            logger.error(msg)
            summary["error"] = msg
            summary["duration_sec"] = round(time.time() - start, 1)
            raise RuntimeError(msg)

        # Mails holen: URLs + Attachments in einem Pass
        fetched = self.router.fetch_all_with_attachments()
        url_items = fetched["urls"]
        attach_items = fetched["attachments"]
        summary["fetched"] = len(url_items)
        summary["attachments_fetched"] = len(attach_items)

        pending_urls = {p["url"] for p in self.db.pending_list("pending")}
        new_items = [
            it for it in url_items
            if not self.db.history_has(it["url"]) and it["url"] not in pending_urls
        ]
        summary["new"] = len(new_items)
        logger.info(f"Neue URLs: {len(new_items)}, Attachments: {len(attach_items)}")

        for item in new_items:
            # Cancel zwischen URLs prüfen - laufende process_url-Calls
            # werden nicht unterbrochen, neue starten aber nicht mehr.
            if is_cancelled():
                logger.warning(f"Scraper cancelled - {len([i for i in new_items if i == item]) } URLs übersprungen")
                summary["cancelled"] = True
                break

            url = item["url"]

            # yt-dlp Failed-Tracking: nach MAX_DOWNLOAD_ATTEMPTS überspringen.
            # WICHTIG: NICHT in die History schreiben — sonst gilt die URL für
            # immer als erledigt und ein Retry ist nur per SQL möglich (unsichtbar).
            # Sie bleibt in download_failures und erscheint im Audit unter
            # "Endgültig fehlgeschlagen" mit Retry-/Verwerfen-Aktion.
            attempts = self.db.download_failure_attempts(url)
            if attempts >= MAX_DOWNLOAD_ATTEMPTS:
                logger.info(f"Skip {url}: {attempts} Download-Fehlversuche, aufgegeben (Audit → Retry/Verwerfen)")
                summary["skipped_failed"] += 1
                continue

            try:
                r = self.process_url(item)
                if r["status"] == "auto":
                    summary["auto"] += 1
                    summary[f"{item['type']}_auto"] += 1
                elif r["status"] == "pending":
                    summary["pending"] += 1
                    summary[f"{item['type']}_pending"] += 1
                else:
                    summary["errors"] += 1
            except Exception as e:
                logger.exception(f"URL fehlgeschlagen {url}: {e}")
                summary["errors"] += 1

        # Attachments verarbeiten (PDF + JPG)
        summary["attach_auto"] = 0
        summary["attach_pending"] = 0
        summary["attach_skipped"] = 0
        for att in attach_items:
            if is_cancelled():
                summary["cancelled"] = True
                break
            # Synthetic-URL für Dedupe: msg_id::filename. Wenn schon
            # in History oder Pending, skip.
            synth_url = f"mail-attachment://{att['msg_id']}::{att['filename']}"
            if self.db.history_has(synth_url) or synth_url in pending_urls:
                summary["attach_skipped"] += 1
                continue
            try:
                r = self.process_attachment(att, synth_url)
                if r.get("status") == "auto":
                    summary["attach_auto"] += 1
                    summary[f"{att['type']}_auto"] += 1
                elif r.get("status") == "pending":
                    summary["attach_pending"] += 1
                    summary[f"{att['type']}_pending"] += 1
                else:
                    summary["errors"] += 1
            except Exception as e:
                logger.exception(f"Attachment fehlgeschlagen {att.get('filename')}: {e}")
                summary["errors"] += 1

        summary["duration_sec"] = round(time.time() - start, 1)
        summary["total_pending"] = self.db.pending_count()
        logger.info(f"Job-Summary: {summary}")

        # Webhook-Notifications (asynchron, blockt das Job-Ende nicht)
        try:
            from ..core import webhook
            webhook.notify("scraper_done", summary)
            # Pending-High-Alarm wenn Schwelle überschritten
            threshold = int(self.cfg.get("notifications", "pending_high_threshold",
                                          default=50) or 50)
            if summary["total_pending"] >= threshold:
                webhook.notify("pending_high", {
                    "pending_count": summary["total_pending"],
                    "threshold": threshold,
                })
        except Exception as e:
            logger.warning(f"webhook.notify failed (non-fatal): {e}")

        return summary

    # ---------------- History neu analysieren ----------------

    def _fetch_description_via_ytdlp(self, url: str) -> Optional[str]:
        """Lädt nur die Description einer URL (skip-download). Nutzt das
        existing yt-dlp Binary + Cookie-Datei, kein File-Download."""
        import subprocess
        binary = self.downloader.ytdlp_path
        cmd = [binary, "--skip-download", "--no-warnings", "--no-playlist",
               "--print", "%(description)s\n%(title)s"]
        if self.downloader.cookies_file:
            cmd += ["--cookies", self.downloader.cookies_file]
        cmd.append(url)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                logger.warning(f"yt-dlp metadata fail {url}: {r.stderr.strip()[:200]}")
                return None
            text = r.stdout.strip()
            return text if text else None
        except Exception as e:
            logger.error(f"yt-dlp metadata exception {url}: {e}")
            return None

    def reanalyze_history_one(self, url: str, *, dry_run: bool = False,
                                auto_move: bool = False) -> Dict:
        """Holt die Description neu (via yt-dlp), schickt durch den aktuellen
        Analyzer, aktualisiert in der DB wenn Confidence ausreichend ist.

        Args:
            dry_run:    Nichts in DB/Filesystem ändern, nur was-passieren-würde.
            auto_move:  Files in den neuen target_dir verschieben wenn die
                        Klassifikation deutlich anders ist. Default False -
                        nur DB-Name wird aktualisiert, File bleibt wo es ist.

        Returns:
          {ok, url, action: 'updated'|'moved'|'unchanged'|'low_confidence'|'fail',
           old: {name, content_type, target_dir},
           new: {name, type, category, confidence, target_dir(if moved)}}
        """
        entry = self.db.history_get(url)
        if not entry:
            return {"ok": False, "error": "Nicht in History"}

        if not self.analyzer_enabled:
            return {"ok": False, "error": "AI-Analyzer nicht initialisiert"}

        content_type = entry.get("content_type") or "recipe"
        old_name = entry.get("name") or ""
        old_target = entry.get("target_dir") or ""

        description = self._fetch_description_via_ytdlp(url)
        if not description:
            return {"ok": False, "url": url, "action": "fail",
                    "error": "Description konnte nicht geladen werden (Video offline?)"}

        if content_type == "recipe":
            r = self._analyze_recipe(description)
            new_name = f"{r.name} ({r.type})" if r.type and r.type.lower() != "unbekannt" else r.name
            payload = {"name": r.name, "type": r.type, "category": r.category,
                       "confidence": r.confidence}
            unchanged = (new_name == old_name or r.name.lower() == "unbekannt")
        else:  # wedding
            w = self._analyze_wedding(description)
            new_name = w.name
            payload = {"name": w.name, "category": w.category, "confidence": w.confidence}
            unchanged = (new_name == old_name or w.name.lower() == "unbekannt")

        if unchanged or payload["confidence"] < self.confidence_threshold:
            return {"ok": True, "url": url,
                    "action": "unchanged" if unchanged else "low_confidence",
                    "old": {"name": old_name, "content_type": content_type,
                            "target_dir": old_target},
                    "new": payload}

        # Updaten
        if auto_move and old_target and Path(old_target).exists():
            # File-Move: nutzt move_history_item das die ganze Logik (mkdir,
            # info.json updaten, rename, empty-parents cleanup) kapselt.
            if dry_run:
                # Simulieren: target-Pfad berechnen ohne move
                if content_type == "recipe":
                    new_dir = self.recipe_dir / _sanitize(r.type) / _sanitize(r.category or "Allgemein") / _sanitize(r.name)
                else:
                    new_dir = self.wedding_dir / _sanitize(w.category or "Sonstiges") / _sanitize(w.name)
                payload["target_dir"] = str(new_dir)
                return {"ok": True, "url": url, "action": "moved",
                        "old": {"name": old_name, "content_type": content_type,
                                "target_dir": old_target},
                        "new": payload, "dry_run": True}

            try:
                if content_type == "recipe":
                    move_res = self.move_history_item(
                        url, new_name=new_name,
                        new_type=r.type, new_category=r.category or "Allgemein",
                    )
                else:
                    move_res = self.move_history_item(
                        url, new_name=new_name,
                        new_category=w.category or "Sonstiges",
                    )
                if move_res.get("ok"):
                    payload["target_dir"] = move_res.get("target", "")
                    return {"ok": True, "url": url, "action": "moved",
                            "old": {"name": old_name, "content_type": content_type,
                                    "target_dir": old_target},
                            "new": payload}
                return {"ok": False, "url": url, "action": "fail",
                        "error": f"Move fehlgeschlagen: {move_res.get('error')}"}
            except Exception as e:
                logger.exception(f"Auto-Move fail {url}: {e}")
                return {"ok": False, "url": url, "action": "fail",
                        "error": f"Move-Exception: {e}"}

        # Kein Auto-Move: nur DB-Name updaten
        if not dry_run:
            self.db.history_update(url, name=new_name)

        return {"ok": True, "url": url, "action": "updated",
                "old": {"name": old_name, "content_type": content_type,
                        "target_dir": old_target},
                "new": payload, "dry_run": dry_run}

    def reanalyze_history_all(self, *, dry_run: bool = False,
                                limit: int = 1000,
                                auto_move: bool = False) -> Dict:
        """Iteriert über alle History-Items und versucht eine Reanalyse.
        Sehr langsam (yt-dlp pro Item) - sollte als Background-Job laufen.

        auto_move=True verschiebt Files in den neuen target_dir wenn die
        Klassifikation sich ändert. Wichtig: das aktualisiert sowohl DB
        als auch das Filesystem.
        """
        items = self.db.history_list(limit=limit)
        updated = 0
        moved = 0
        unchanged = 0
        low_conf = 0
        failed = 0
        details = []

        for i, entry in enumerate(items, 1):
            if is_cancelled():
                logger.info(f"Reanalyze-History abgebrochen bei {i}/{len(items)}")
                break
            url = entry["url"]
            try:
                res = self.reanalyze_history_one(url, dry_run=dry_run, auto_move=auto_move)
                action = res.get("action")
                if action == "moved":
                    moved += 1
                    details.append({"url": url, "action": "moved",
                                    "from": res["old"]["name"],
                                    "to": res["new"]})
                elif action == "updated":
                    updated += 1
                    details.append({"url": url, "action": "updated",
                                    "from": res["old"]["name"],
                                    "to": res["new"]})
                elif action == "unchanged":
                    unchanged += 1
                elif action == "low_confidence":
                    low_conf += 1
                else:
                    failed += 1
            except Exception as e:
                logger.exception(f"Reanalyze-History fail {url}: {e}")
                failed += 1

        return {
            "total": len(items),
            "updated": updated,
            "moved": moved,
            "unchanged": unchanged,
            "low_confidence": low_conf,
            "failed": failed,
            "dry_run": dry_run,
            "auto_move": auto_move,
            "details": details[:50],
        }

    def cleanup_junk_items(self, *, dry_run: bool = True) -> Dict:
        """Findet History-Items deren Klassifikation 'Müll' aussieht und
        listet sie auf. Heuristiken:
        - name == 'Unbekannt' (case-insensitive)
        - name == content_type (z.B. 'Hochzeit' als Name)
        - name fängt mit 'Hochzeit_YYYYMMDD' an (Auto-Fallback)
        - name kürzer als 3 Zeichen
        - target_dir-Parent endet auf 'Sonstiges' UND name beginnt mit 'TikTok' o.ä.

        Returns Liste der gefundenen Junk-Items. Schreibt nichts bei dry_run=True
        (Default). User entscheidet manuell ob er sie löscht/reanalysiert.
        """
        items = self.db.history_list(limit=10000)
        junk = []
        for entry in items:
            name = (entry.get("name") or "").strip()
            ct = entry.get("content_type") or ""
            target = entry.get("target_dir") or ""

            reasons = []
            if name.lower() in ("unbekannt", "unknown", ""):
                reasons.append("name=Unbekannt/leer")
            if name.lower() == ct.lower():
                reasons.append(f"name=content_type ({ct})")
            if re.match(r"^Hochzeit_\d{8}", name):
                reasons.append("auto-fallback-Name")
            if 0 < len(name) < 3:
                reasons.append(f"name zu kurz ({len(name)} chars)")
            if name.lower().startswith(("tiktok", "instagram", "video")):
                reasons.append("default-Plattform-Name")
            if "/Sonstiges/" in target and name.lower() in ("hochzeit", "rezept"):
                reasons.append("nur Default-Kategorie")

            if reasons:
                junk.append({
                    "url": entry["url"],
                    "name": name,
                    "content_type": ct,
                    "target_dir": target,
                    "processed_at": entry.get("processed_at"),
                    "reasons": reasons,
                })

        return {"total_history": len(items), "junk_count": len(junk),
                "items": junk, "dry_run": dry_run}
    def move_history_item(self, url: str, *, new_name: str, new_type: str = None,
                            new_category: str = None) -> Dict:
        entry = self.db.history_get(url)
        if not entry:
            return {"ok": False, "error": "Eintrag nicht in Historie"}

        old_dir = Path(entry["target_dir"]) if entry.get("target_dir") else None
        if not old_dir or not old_dir.exists():
            return {"ok": False, "error": f"Alter Pfad existiert nicht: {old_dir}"}

        content_type = entry.get("content_type") or "recipe"
        sanitized_name = _sanitize(new_name)

        if content_type == "recipe":
            if not new_type:
                return {"ok": False, "error": "Typ fehlt"}
            new_dir = self.recipe_dir / _sanitize(new_type) / _sanitize(new_category or "Sonstiges") / sanitized_name
        else:
            new_dir = self.wedding_dir / _sanitize(new_category or "Sonstiges") / sanitized_name

        if new_dir.resolve() == old_dir.resolve():
            return {"ok": True, "action": "noop", "target": str(new_dir)}

        if new_dir.exists():
            new_dir = new_dir.parent / f"{sanitized_name}_{datetime.now():%Y%m%d_%H%M%S}"

        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))

        # info.json updaten
        info_file = new_dir / "info.json"
        if info_file.exists():
            try:
                with open(info_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
                info["name"] = new_name
                info["is_manual"] = True
                if content_type == "recipe":
                    info["type"] = new_type
                    info["category"] = new_category
                else:
                    info["wedding_category"] = new_category
                info["edited_at"] = datetime.now().isoformat()
                with open(info_file, "w", encoding="utf-8") as f:
                    json.dump(info, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"info.json update: {e}")

        # Datei-Basisnamen anpassen
        for ext in (".mp4", ".webm", ".mkv"):
            for f in new_dir.glob(f"*{ext}"):
                if f.stem != sanitized_name:
                    target = new_dir / f"{sanitized_name}{ext}"
                    if not target.exists():
                        try:
                            f.rename(target)
                        except Exception as e:
                            logger.warning(f"rename {f}: {e}")

        self.db.history_update(url, name=new_name, target_dir=str(new_dir))
        self._cleanup_empty_parents(old_dir)
        return {"ok": True, "action": "moved", "target": str(new_dir)}

    def delete_history_item(self, url: str) -> Dict:
        entry = self.db.history_get(url)
        if not entry:
            return {"ok": False, "error": "Eintrag nicht in Historie"}
        target_dir = entry.get("target_dir")
        if target_dir:
            d = Path(target_dir)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                self._cleanup_empty_parents(d)
        self.db.history_delete(url)
        return {"ok": True, "action": "deleted"}

    def _cleanup_empty_parents(self, removed_dir: Path) -> None:
        parent = removed_dir.parent
        for _ in range(4):
            try:
                if not parent.exists():
                    break
                rels = [self.recipe_dir, self.wedding_dir]
                inside = any(str(parent).startswith(str(r)) and str(parent) != str(r) for r in rels)
                if not inside:
                    break
                if not any(parent.iterdir()):
                    parent.rmdir()
                    logger.info(f"Leeren Ordner gelöscht: {parent}")
                    parent = parent.parent
                else:
                    break
            except Exception as e:
                logger.warning(f"Cleanup-Parents: {e}")
                break

    # ---------------- Pending im Web auflösen ----------------
    def reanalyze_pending(self, url: str) -> Dict:
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        description = entry.get("description")
        video_path = Path(entry["video_path"]) if entry.get("video_path") else None
        if not video_path or not video_path.exists():
            return {"ok": False, "error": "Video-Datei fehlt (vermutlich aufgeräumt)"}

        content_type = entry.get("content_type") or "recipe"

        if content_type == "recipe":
            r = self._analyze_recipe(description)
            suggestion = {
                "name": r.name, "type": r.type,
                "category": r.category, "confidence": r.confidence,
            }
            if not r.needs_manual_input(self.confidence_threshold):
                target = self._save_recipe(r, url, video_path, description)
                self.db.history_add(url, content_type="recipe", name=r.name, target_dir=str(target))
                self.db.pending_resolve(url, status="resolved")
                self._remove_pending_files(entry)
                return {"ok": True, "action": "auto_saved", "target": str(target),
                        "analysis": suggestion}
            self.db.pending_update_suggestion(url, suggestion)
            return {"ok": True, "action": "still_pending", "analysis": suggestion}
        else:  # wedding
            w = self._analyze_wedding(description)
            default_cat = "Sonstiges"
            suggestion = {
                "name": w.name, "category": w.category or default_cat,
                "confidence": w.confidence,
            }
            if not w.needs_manual_input(self.confidence_threshold):
                target = self._save_wedding(w, url, video_path, description, default_cat)
                self.db.history_add(url, content_type="wedding", name=w.name, target_dir=str(target))
                self.db.pending_resolve(url, status="resolved")
                self._remove_pending_files(entry)
                return {"ok": True, "action": "auto_saved", "target": str(target),
                        "analysis": suggestion}
            self.db.pending_update_suggestion(url, suggestion)
            return {"ok": True, "action": "still_pending", "analysis": suggestion}

    def resolve_pending(self, url: str, decision: Dict) -> Dict:
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        if decision.get("action") == "skip":
            self.db.pending_resolve(url, status="skipped")
            self.db.history_add(url, content_type=entry["content_type"], name="(skipped)")
            self._remove_pending_files(entry)
            return {"ok": True, "action": "skipped"}

        video_path = Path(entry["video_path"]) if entry.get("video_path") else None
        description = entry.get("description")

        if not video_path or not video_path.exists():
            self.db.pending_resolve(url, status="resolved")
            return {"ok": False, "error": "Video-Datei fehlt (vermutlich aufgeräumt)"}

        if entry["content_type"] == "recipe":
            r = RecipeAnalysis(
                name=decision.get("name", "Unbekannt"),
                type=decision.get("type", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_recipe(r, url, video_path, description)
            self.db.history_add(url, content_type="recipe", name=r.name, target_dir=str(target))
        else:
            w = WeddingAnalysis(
                name=decision.get("name", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_wedding(w, url, video_path, description, default_cat="Sonstiges")
            self.db.history_add(url, content_type="wedding", name=w.name, target_dir=str(target))
        self.db.pending_resolve(url, status="resolved")
        self._remove_pending_files(entry)
        return {"ok": True, "action": "saved", "target": str(target)}

    def _remove_pending_files(self, entry: Dict) -> None:
        p = entry.get("video_path")
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def run_job() -> Dict:
    return get_scraper_job().run()


# ---------------- Singleton-Accessor ----------------
# ScraperJob() konstruiert 30+ Config-Werte, Ollama-Clients, IMAP-Klassen.
# Bei vielen UI-Klicks (Resolve, Reanalyze, Edit) summiert sich das auf
# 200-800 ms pro Call. Singleton cached die Instanz und wird bei Config-
# Save invalidiert, sodass neue Settings im nächsten Call greifen.
_job_instance: Optional["ScraperJob"] = None
_job_lock = threading.Lock()


def get_scraper_job() -> "ScraperJob":
    """Liefert die globale ScraperJob-Instanz. Konstruiert sie lazy."""
    global _job_instance
    if _job_instance is None:
        with _job_lock:
            if _job_instance is None:
                _job_instance = ScraperJob()
    return _job_instance


def invalidate_scraper_job() -> None:
    """Wird von api_config nach jedem Config-Save aufgerufen. Beim nächsten
    get_scraper_job() wird eine frische Instanz mit den neuen Settings gebaut."""
    global _job_instance
    with _job_lock:
        _job_instance = None
    logger.info("ScraperJob-Singleton invalidiert (Config-Reload)")
