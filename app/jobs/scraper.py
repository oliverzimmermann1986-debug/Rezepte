"""
Scraper-Job (TikTok/Instagram -> Rezepte/Hochzeit Ordner).

Vereinfachte KI-Cascade (kein Vision-Fallback mehr):
  Ollama-fast -> Ollama-fallback -> Pending (manuell im Web-UI)

Pending-Items werden im Web-UI über ein <video>-Element angezeigt -
keine Standbild-Extraktion mehr nötig.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config_store import get_config
from ..db import get_db
from ..core.analyzer import OllamaAnalyzer, RecipeAnalysis, WeddingAnalysis, build_analyzer
from ..core.downloader import VideoDownloader, cancel_active_downloads
from ..core.email_processor import MailAccount, EmailRouter
from ..path_utils import build_under, ensure_within, safe_component, unique_directory

logger = logging.getLogger(__name__)


# Cancel-Flag (modul-global, threading-safe). Wird im Web-Trigger und beim
# Job-Start reset, vom Cancel-Endpoint gesetzt, im run()-Loop pro URL geprüft.
_CANCEL_EVENT = threading.Event()

# Max yt-dlp Versuche bevor URL als 'download_failed' in history landet
MAX_DOWNLOAD_ATTEMPTS = 3


def cancel_job() -> dict:
    """Setzt das Cancel-Flag. Der laufende Scraper bricht beim nächsten
    URL-Check ab und beendet zusätzlich aktive yt-dlp-Prozessgruppen."""
    _CANCEL_EVENT.set()
    stopped = cancel_active_downloads()
    return {"ok": True, "stopped_downloads": stopped}


def is_cancelled() -> bool:
    return _CANCEL_EVENT.is_set()


def reset_cancel() -> None:
    _CANCEL_EVENT.clear()


def _sanitize(name: str) -> str:
    return safe_component(name, fallback="Unbekannt", max_length=96)


def _has_usable_description(text: Optional[str], min_len: int) -> bool:
    if not text:
        return False
    clean = re.sub(r"#\S+|https?://\S+", "", text).strip()
    return len(clean) >= min_len


def _save_video_files(target_dir: Path, video_path: Path,
                       description: Optional[str], info: Dict) -> None:
    """Schreibt einen vollständigen Datensatz zunächst in ein Staging-Verzeichnis."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = target_dir.parent / f".{target_dir.name}.tmp-{uuid.uuid4().hex[:10]}"
    staging.mkdir(mode=0o700)
    try:
        file_base = target_dir.name
        if video_path and video_path.exists():
            shutil.copy2(video_path, staging / f"{file_base}{video_path.suffix.lower()}")
        if description:
            (staging / "description.txt").write_text(description, encoding="utf-8")
        with open(staging / "info.json", "w", encoding="utf-8") as handle:
            json.dump(info, handle, indent=2, ensure_ascii=False)
        staging.replace(target_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


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

        # AI-Provider: 'ollama' (default) oder 'openai'.
        # build_analyzer() liefert je nach Config eine OllamaAnalyzer- oder
        # OpenAIAnalyzer-Instanz mit identischem Interface (analyze_recipe,
        # analyze_wedding, spellcheck, health).
        ai_cfg = cfg.get("ai", default={}) or {}
        provider = (ai_cfg.get("provider") or "ollama").lower().strip()
        self.ai_provider = provider

        # Primary-Analyzer
        try:
            self.ollama = build_analyzer(ai_cfg)   # name 'ollama' historisch, ist jetzt Provider-agnostisch
            self.ollama_enabled = True
        except Exception as e:
            logger.error(f"AI-Analyzer Init fehlgeschlagen ({provider}): {e}")
            self.ollama = None
            self.ollama_enabled = False

        # Fallback-Analyzer: nur bei Ollama sinnvoll (zweites lokales Modell).
        # Für OpenAI macht ein 'Fallback-Model' keinen Sinn - GPT-4o-mini ist
        # schon das günstige Modell. Wer Cascade will: ollama-fast → openai.
        # Das ist aber komplex und selten gefragt - skip für jetzt.
        self.ollama_fallback = None
        if provider == "ollama":
            ollama_cfg = ai_cfg.get("ollama") or {}
            fb_model = (ollama_cfg.get("fallback_model") or "").strip()
            if fb_model and self.ollama_enabled:
                from ..core.analyzer import OllamaAnalyzer
                self.ollama_fallback = OllamaAnalyzer(
                    (ollama_cfg.get("url") or "http://localhost:11434").strip(),
                    fb_model,
                    int(ollama_cfg.get("timeout") or 60),
                )

        self.confidence_threshold = float(cfg.get("ai", "confidence_threshold", default=0.75) or 0.75)
        self.fallback_threshold = float(cfg.get("ai", "fallback_threshold", default=0.5) or 0.5)
        self.min_desc_len = int(cfg.get("ai", "description_min_length", default=20) or 20)

        # Downloader (mit optionalem Cookie-Jar für private Inhalte)
        ytdlp_cfg = cfg.get("ytdlp", default={}) or {}
        self.downloader = VideoDownloader(
            ytdlp_cfg.get("binary", "/opt/scrapper/venv/bin/yt-dlp"),
            self.temp_dir,
            cookies_file=ytdlp_cfg.get("cookies_file") or None,
            timeout=int(ytdlp_cfg.get("timeout_sec", 300) or 300),
            max_filesize_mb=int(ytdlp_cfg.get("max_filesize_mb", 500) or 500),
            retries=int(ytdlp_cfg.get("retries", 3) or 3),
        )

        # E-Mail Konten
        mail_cfg = cfg.get("mail", default={}) or {}
        max_attachment_bytes = int(mail_cfg.get("max_attachment_mb", 20) or 20) * 1024 * 1024
        max_attachments_per_mail = int(mail_cfg.get("max_attachments_per_mail", 10) or 10)
        max_mail_bytes = int(mail_cfg.get("max_mail_mb", 50) or 50) * 1024 * 1024
        accounts = []
        if mail_cfg.get("recipe"):
            accounts.append(MailAccount(
                "recipe", mail_cfg["recipe"], "recipe",
                max_attachment_bytes=max_attachment_bytes,
                max_attachments_per_mail=max_attachments_per_mail,
                max_mail_bytes=max_mail_bytes,
            ))
        if mail_cfg.get("wedding"):
            accounts.append(MailAccount(
                "wedding", mail_cfg["wedding"], "wedding",
                default_category=mail_cfg["wedding"].get("default_category", "Sonstiges"),
                max_attachment_bytes=max_attachment_bytes,
                max_attachments_per_mail=max_attachments_per_mail,
                max_mail_bytes=max_mail_bytes,
            ))
        self.router = EmailRouter(accounts)

        self.wedding_always_pending = bool(
            (mail_cfg.get("wedding") or {}).get("always_pending", False)
        )

        self.wedding_categories = cfg.get(
            "wedding_categories",
            default=["Deko", "Foto", "Basteln", "Einladung", "Standesamt", "Sonstiges"],
        )

    # ---------------- Analyse (Ollama-only) ----------------
    def _analyze_recipe(self, description: Optional[str]) -> RecipeAnalysis:
        """Cascade: fast Modell -> fallback Modell -> bestes Ergebnis (oder Unbekannt)."""
        best: Optional[RecipeAnalysis] = None
        if self.ollama and _has_usable_description(description, self.min_desc_len):
            r = self.ollama.analyze_recipe(description)
            logger.info(f"Ollama fast: name={r.name} typ={r.type} conf={r.confidence:.2f}")
            if not r.needs_manual_input(self.confidence_threshold):
                return r
            best = r
            if self.ollama_fallback:
                r2 = self.ollama_fallback.analyze_recipe(description)
                logger.info(f"Ollama fallback: name={r2.name} typ={r2.type} conf={r2.confidence:.2f}")
                if not r2.needs_manual_input(self.fallback_threshold):
                    return r2
                if r2.confidence > best.confidence:
                    best = r2
        if best:
            return best
        return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)

    def _analyze_wedding(self, description: Optional[str]) -> WeddingAnalysis:
        best: Optional[WeddingAnalysis] = None
        if self.ollama and _has_usable_description(description, self.min_desc_len):
            w = self.ollama.analyze_wedding(description, self.wedding_categories)
            logger.info(f"Ollama fast (wedding): name={w.name} cat={w.category} conf={w.confidence:.2f}")
            if not self.wedding_always_pending and not w.needs_manual_input(self.confidence_threshold):
                return w
            best = w
            if self.ollama_fallback:
                w2 = self.ollama_fallback.analyze_wedding(description, self.wedding_categories)
                logger.info(f"Ollama fallback (wedding): name={w2.name} cat={w2.category} conf={w2.confidence:.2f}")
                if not self.wedding_always_pending and not w2.needs_manual_input(self.fallback_threshold):
                    return w2
                if w2.confidence > best.confidence:
                    best = w2
        if best:
            return best
        return WeddingAnalysis("Unbekannt", None, 0.0)

    # ---------------- Save ----------------
    def _save_recipe(self, r: RecipeAnalysis, url: str, video: Path,
                     description: Optional[str]) -> Path:
        type_n = _sanitize(r.type)
        cat_n = _sanitize(r.category or "Allgemein")
        name_n = _sanitize(r.name)
        target = build_under(self.recipe_dir, (type_n, cat_n, name_n))
        target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
        info = {
            "url": url, "name": r.name, "type": r.type, "category": r.category,
            "confidence": r.confidence, "is_manual": r.is_manual,
            "content_type": "recipe", "description": description,
            "timestamp": datetime.now().isoformat(),
        }
        _save_video_files(target, video, description, info)
        return target

    def _save_wedding(self, w: WeddingAnalysis, url: str, video: Path,
                      description: Optional[str], default_cat: str = "Sonstiges") -> Path:
        cat = _sanitize(w.category or default_cat)
        name_n = _sanitize(w.name) if w.name.lower() != "unbekannt" \
            else f"Hochzeit_{datetime.now():%Y%m%d_%H%M%S}"
        target = build_under(self.wedding_dir, (cat, name_n))
        target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
        info = {
            "url": url, "name": w.name, "wedding_category": w.category or default_cat,
            "confidence": w.confidence, "is_manual": w.is_manual,
            "content_type": "wedding", "description": description,
            "timestamp": datetime.now().isoformat(),
        }
        _save_video_files(target, video, description, info)
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
                    self.db.history_add(
                        url, content_type="recipe", name=r.name, target_dir=str(target),
                        recipe_type=r.type, category=r.category or "Allgemein",
                        description=description or "", source="social",
                    )
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
        """Bei OpenAI-Provider: Vision-API für JPG/PNG. Schickt das Bild
        als base64 mit. Returnt RecipeAnalysis oder WeddingAnalysis.

        Nur wenn ai.provider == 'openai' (sonst kann Ollama keine Bilder).
        """
        if self.ai_provider != "openai" or not self.ollama:
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
            r = self.ollama.session.post(
                f"{self.ollama.base_url}/chat/completions",
                json={
                    "model": self.ollama.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
                timeout=self.ollama.timeout,
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
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = target_dir.parent / f".{target_dir.name}.tmp-{uuid.uuid4().hex[:10]}"
        staging.mkdir(mode=0o700)
        try:
            file_base = target_dir.name
            (staging / f"{file_base}{ext}").write_bytes(attachment_data)
            if source_text:
                (staging / "description.txt").write_text(source_text, encoding="utf-8")
            with open(staging / "info.json", "w", encoding="utf-8") as handle:
                json.dump(info, handle, indent=2, ensure_ascii=False)
            staging.replace(target_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

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
                    pending_file = self._stash_bytes_for_pending(data, att["filename"], ext)
                    self.db.pending_add(
                        url=synth_url, content_type="recipe",
                        description=description[:5000],
                        video_path=str(pending_file),
                        ai_suggestion={
                            "name": analysis.name, "type": analysis.type,
                            "category": analysis.category, "confidence": analysis.confidence,
                            "source": "mail-attachment", "filename": att["filename"],
                            "extension": ext, "media_kind": self._media_kind(ext),
                            "size_bytes": len(data), "mail_subject": subject,
                        },
                    )
                    result.update({"status": "pending", "name": analysis.name})
                else:
                    type_n = _sanitize(analysis.type)
                    cat_n = _sanitize(analysis.category or "Allgemein")
                    name_n = _sanitize(analysis.name)
                    target = build_under(self.recipe_dir, (type_n, cat_n, name_n))
                    target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
                    info = {
                        "url": synth_url, "name": analysis.name, "type": analysis.type,
                        "category": analysis.category, "confidence": analysis.confidence,
                        "content_type": "recipe", "source": "mail-attachment",
                        "filename": att["filename"], "mail_subject": subject,
                        "description": description[:5000],
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._save_attachment_file(target, data, ext, info, description)
                    self.db.history_add(
                        synth_url, content_type="recipe", name=analysis.name,
                        target_dir=str(target), recipe_type=analysis.type,
                        category=analysis.category or "Allgemein",
                        description=description or "", source="mail-attachment",
                    )
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
                    pending_file = self._stash_bytes_for_pending(data, att["filename"], ext)
                    self.db.pending_add(
                        url=synth_url, content_type="wedding",
                        description=description[:5000],
                        video_path=str(pending_file),
                        ai_suggestion={
                            "name": analysis.name, "category": analysis.category or default_cat,
                            "confidence": analysis.confidence,
                            "source": "mail-attachment", "filename": att["filename"],
                            "extension": ext, "media_kind": self._media_kind(ext),
                            "size_bytes": len(data), "mail_subject": subject,
                        },
                    )
                    result.update({"status": "pending", "name": analysis.name})
                else:
                    cat = _sanitize(analysis.category or default_cat)
                    name_n = _sanitize(analysis.name) if analysis.name.lower() != "unbekannt" \
                        else f"Mail_{datetime.now():%Y%m%d_%H%M%S}"
                    target = build_under(self.wedding_dir, (cat, name_n))
                    target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
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

    @staticmethod
    def _media_kind(ext: str) -> str:
        ext = (ext or "").lower()
        if ext == ".pdf":
            return "pdf"
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return "image"
        if ext in {".mp4", ".webm", ".mkv", ".mov"}:
            return "video"
        return "file"

    def _pending_root(self) -> Path:
        root = self.temp_dir / "pending"
        ensure_within(root, self.temp_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _stash_for_pending(self, source: Path) -> Optional[Path]:
        """Kopiert eine heruntergeladene Datei atomar in den Pending-Stash."""
        if not source or not source.exists() or not source.is_file():
            return None
        suffix = source.suffix.lower()[:12]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = self._pending_root() / f"{ts}_{safe_component(source.stem, max_length=48)}{suffix}"
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        shutil.copy2(source, tmp)
        tmp.replace(dst)
        return dst

    def _stash_bytes_for_pending(self, data: bytes, filename: str, ext: str) -> Path:
        if not data:
            raise ValueError("Leerer Mail-Anhang kann nicht in Pending abgelegt werden")
        safe_ext = ext.lower() if ext.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp"} else ".bin"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = safe_component(Path(filename or "attachment").stem, max_length=48)
        dst = self._pending_root() / f"{ts}_{stem}{safe_ext}"
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
        return dst

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
        if self.ollama_enabled and self.ollama and not self.ollama.health():
            if self.ai_provider == "openai":
                msg = (f"OpenAI nicht erreichbar oder Modell '{self.ollama.model}' nicht verfügbar - "
                       f"Details im Server-Log (api_key gültig? Internet vom Container? Billing aktiv?). "
                       f"Job abgebrochen damit nicht alle URLs in Pending landen")
            else:
                msg = (f"Ollama nicht erreichbar oder Modell '{self.ollama.model}' fehlt - "
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

            # yt-dlp Failed-Tracking: nach MAX_DOWNLOAD_ATTEMPTS aufgeben
            # und URL wie 'resolved' behandeln (kommt nicht wieder durch).
            attempts = self.db.download_failure_attempts(url)
            if attempts >= MAX_DOWNLOAD_ATTEMPTS:
                logger.info(f"Skip {url}: {attempts} Download-Fehlversuche, aufgegeben")
                self.db.history_add(url, content_type=item["type"], name="(download failed)")
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
            identity = f"{att.get('source_account','')}\0{att.get('msg_id','')}\0{att.get('filename','')}"
            synth_url = "mail-attachment://" + hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()
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
        """Fetch metadata through the same validated, cancellable downloader."""
        return self.downloader.fetch_description(url, timeout=60)


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

        if not self.ollama_enabled:
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
            if content_type == "recipe":
                self.db.history_update(
                    url, name=r.name, recipe_type=r.type,
                    category=r.category or "Allgemein", description=description,
                )
            else:
                self.db.history_update(url, name=new_name, description=description)

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
        content_type = entry.get("content_type") or "recipe"
        allowed_root = self.recipe_dir if content_type == "recipe" else self.wedding_dir
        if not old_dir or not old_dir.exists():
            return {"ok": False, "error": f"Alter Pfad existiert nicht: {old_dir}"}
        try:
            old_dir = ensure_within(old_dir, allowed_root)
        except ValueError:
            return {"ok": False, "error": "Gespeicherter Zielpfad liegt außerhalb des erlaubten Bereichs"}

        sanitized_name = _sanitize(new_name)

        if content_type == "recipe":
            if not new_type:
                return {"ok": False, "error": "Typ fehlt"}
            new_dir = build_under(
                self.recipe_dir,
                (_sanitize(new_type), _sanitize(new_category or "Sonstiges"), sanitized_name),
            )
        else:
            new_dir = build_under(
                self.wedding_dir,
                (_sanitize(new_category or "Sonstiges"), sanitized_name),
            )

        if new_dir.resolve() == old_dir.resolve():
            return {"ok": True, "action": "noop", "target": str(new_dir)}

        new_dir = unique_directory(new_dir, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")

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

        self.db.history_update(
            url, name=new_name, target_dir=str(new_dir),
            recipe_type=new_type if content_type == "recipe" else None,
            category=(new_category or "Allgemein") if content_type == "recipe" else new_category,
        )
        self._cleanup_empty_parents(old_dir)
        return {"ok": True, "action": "moved", "target": str(new_dir)}

    def delete_history_item(self, url: str) -> Dict:
        entry = self.db.history_get(url)
        if not entry:
            return {"ok": False, "error": "Eintrag nicht in Historie"}
        target_dir = entry.get("target_dir")
        if target_dir:
            root = self.recipe_dir if entry.get("content_type") == "recipe" else self.wedding_dir
            try:
                d = ensure_within(Path(target_dir), root)
            except ValueError:
                return {"ok": False, "error": "Zielpfad liegt außerhalb des erlaubten Bereichs"}
            if d.exists():
                shutil.rmtree(d)
                self._cleanup_empty_parents(d)
        self.db.history_delete(url)
        return {"ok": True, "action": "deleted"}

    def _cleanup_empty_parents(self, removed_dir: Path) -> None:
        parent = removed_dir.parent
        for _ in range(4):
            try:
                if not parent.exists():
                    break
                inside = False
                for root in (self.recipe_dir, self.wedding_dir):
                    try:
                        parent.resolve(strict=False).relative_to(root.resolve(strict=False))
                        if parent.resolve(strict=False) != root.resolve(strict=False):
                            inside = True
                            break
                    except ValueError:
                        continue
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
    @staticmethod
    def _is_attachment_entry(entry: Dict) -> bool:
        suggestion = entry.get("ai_suggestion") or {}
        return suggestion.get("source") == "mail-attachment" or str(entry.get("url") or "").startswith("mail-attachment://")

    def _analyze_pending_attachment(self, entry: Dict, file_path: Path):
        suggestion = entry.get("ai_suggestion") or {}
        description = entry.get("description") or ""
        subject = suggestion.get("mail_subject") or ""
        ext = file_path.suffix.lower()
        content_type = entry.get("content_type") or "recipe"
        analysis = None
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            mime = {
                ".png": "image/png",
                ".webp": "image/webp",
            }.get(ext, "image/jpeg")
            analysis = self._analyze_image_via_openai(
                file_path.read_bytes(), mime, content_type, subject,
                categories=self.wedding_categories if content_type == "wedding" else None,
            )
        if analysis:
            return analysis
        if content_type == "recipe":
            return self._analyze_recipe(description)
        return self._analyze_wedding(description)

    def _save_pending_attachment(self, entry: Dict, file_path: Path, analysis) -> Path:
        url = entry["url"]
        description = entry.get("description") or ""
        suggestion = entry.get("ai_suggestion") or {}
        original_filename = suggestion.get("filename") or file_path.name
        ext = file_path.suffix.lower() or str(suggestion.get("extension") or ".bin")
        data = file_path.read_bytes()
        if entry.get("content_type") == "recipe":
            type_n = _sanitize(analysis.type)
            cat_n = _sanitize(analysis.category or "Allgemein")
            name_n = _sanitize(analysis.name)
            target = build_under(self.recipe_dir, (type_n, cat_n, name_n))
            target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
            info = {
                "url": url, "name": analysis.name, "type": analysis.type,
                "category": analysis.category, "confidence": analysis.confidence,
                "is_manual": bool(getattr(analysis, "is_manual", False)),
                "content_type": "recipe", "source": "mail-attachment",
                "filename": original_filename, "description": description[:5000],
                "timestamp": datetime.now().isoformat(),
            }
        else:
            default_cat = "Sonstiges"
            cat_n = _sanitize(analysis.category or default_cat)
            name_n = _sanitize(analysis.name) if analysis.name.lower() != "unbekannt" \
                else f"Mail_{datetime.now():%Y%m%d_%H%M%S}"
            target = build_under(self.wedding_dir, (cat_n, name_n))
            target = unique_directory(target, timestamp=f"{datetime.now():%Y%m%d_%H%M%S}")
            info = {
                "url": url, "name": analysis.name,
                "wedding_category": analysis.category or default_cat,
                "confidence": analysis.confidence,
                "is_manual": bool(getattr(analysis, "is_manual", False)),
                "content_type": "wedding", "source": "mail-attachment",
                "filename": original_filename, "description": description[:5000],
                "timestamp": datetime.now().isoformat(),
            }
        self._save_attachment_file(target, data, ext, info, description)
        return target

    def reanalyze_pending(self, url: str) -> Dict:
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        file_path = Path(entry["video_path"]) if entry.get("video_path") else None
        if not file_path or not file_path.exists() or not file_path.is_file():
            return {"ok": False, "error": "Pending-Datei fehlt (vermutlich aufgeräumt)"}

        is_attachment = self._is_attachment_entry(entry)
        content_type = entry.get("content_type") or "recipe"
        if is_attachment:
            analysis = self._analyze_pending_attachment(entry, file_path)
        elif content_type == "recipe":
            analysis = self._analyze_recipe(entry.get("description"))
        else:
            analysis = self._analyze_wedding(entry.get("description"))

        metadata = dict(entry.get("ai_suggestion") or {})
        if content_type == "recipe":
            metadata.update({
                "name": analysis.name, "type": analysis.type,
                "category": analysis.category, "confidence": analysis.confidence,
            })
        else:
            metadata.update({
                "name": analysis.name, "category": analysis.category or "Sonstiges",
                "confidence": analysis.confidence,
            })

        if not analysis.needs_manual_input(self.confidence_threshold):
            if is_attachment:
                target = self._save_pending_attachment(entry, file_path, analysis)
            elif content_type == "recipe":
                target = self._save_recipe(analysis, url, file_path, entry.get("description"))
            else:
                target = self._save_wedding(analysis, url, file_path, entry.get("description"), "Sonstiges")
            self.db.history_add(
                url, content_type=content_type, name=analysis.name, target_dir=str(target),
                recipe_type=analysis.type if content_type == "recipe" else "",
                category=(analysis.category or "Allgemein") if content_type == "recipe" else (analysis.category or "Sonstiges"),
                description=entry.get("description") or "",
                source="mail-attachment" if is_attachment else "social",
            )
            self.db.pending_resolve(url, status="resolved")
            self._remove_pending_files(entry)
            return {"ok": True, "action": "auto_saved", "target": str(target), "analysis": metadata}

        self.db.pending_update_suggestion(url, metadata)
        return {"ok": True, "action": "still_pending", "analysis": metadata}

    def resolve_pending(self, url: str, decision: Dict) -> Dict:
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        if decision.get("action") == "skip":
            self.db.pending_resolve(url, status="skipped")
            self.db.history_add(url, content_type=entry["content_type"], name="(skipped)")
            self._remove_pending_files(entry)
            return {"ok": True, "action": "skipped"}

        file_path = Path(entry["video_path"]) if entry.get("video_path") else None
        if not file_path or not file_path.exists() or not file_path.is_file():
            return {"ok": False, "error": "Pending-Datei fehlt (vermutlich aufgeräumt)"}

        description = entry.get("description")
        is_attachment = self._is_attachment_entry(entry)
        if entry["content_type"] == "recipe":
            analysis = RecipeAnalysis(
                name=decision.get("name", "Unbekannt"),
                type=decision.get("type", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_pending_attachment(entry, file_path, analysis) if is_attachment \
                else self._save_recipe(analysis, url, file_path, description)
            self.db.history_add(
                url, content_type="recipe", name=analysis.name, target_dir=str(target),
                recipe_type=analysis.type, category=analysis.category or "Allgemein",
                description=description or "",
                source="mail-attachment" if is_attachment else "social",
            )
        else:
            analysis = WeddingAnalysis(
                name=decision.get("name", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_pending_attachment(entry, file_path, analysis) if is_attachment \
                else self._save_wedding(analysis, url, file_path, description, default_cat="Sonstiges")
            self.db.history_add(url, content_type="wedding", name=analysis.name, target_dir=str(target))
        self.db.pending_resolve(url, status="resolved")
        self._remove_pending_files(entry)
        return {"ok": True, "action": "saved", "target": str(target)}

    def _remove_pending_files(self, entry: Dict) -> None:
        path_value = entry.get("video_path")
        if not path_value:
            return
        try:
            candidate = ensure_within(Path(path_value), self._pending_root())
            candidate.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            logger.warning("Pending-Datei nicht gelöscht (%s): %s", path_value, exc)


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
