"""
Scraper-Job (TikTok/Instagram -> Rezepte/Hochzeit Ordner).

KI-Cascade ist seit dem Ollama-Removal flat:
  OpenAI-Call -> Pending (manuell im Web-UI) wenn confidence zu niedrig

Social-Media-Videos werden nur als begrenzte, temporäre Analysequelle genutzt
und weder an die native App noch über eine öffentliche Medienroute ausgeliefert.

Pre-Analyse-Schritt: nicht-deutsche Captions werden automatisch nach
Deutsch übersetzt (siehe _maybe_translate_description). Das Original
bleibt als description_original.txt im Rezept-Ordner erhalten.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from ..config_store import get_config
from ..db import get_db
from ..core.analyzer import RecipeAnalysis, WeddingAnalysis, build_analyzer
from ..core.downloader import VideoDownloader
from ..core.email_processor import MailAccount, EmailRouter
from ..core.pdf_processing import process_pdf_bytes
from ..recipes.pdf_recipe_extract import (
    ExtractedRecipeData, apply_extracted_recipe_data, existing_hints,
    extract_recipe_data, prepare_recipe_ingredients,
)
from ..recipes.auto_tags import refresh_diet_auto_tags
from ..recipes.canonical import canonical_name
from ..recipes.units import normalize_unit
from ..recipes.video_recipe_extract import (
    VideoAnalysisResult,
    analyze_recipe_video_file,
)

logger = logging.getLogger(__name__)


# Cancel-Flag (modul-global, threading-safe). Wird im Web-Trigger und beim
# Job-Start reset, vom Cancel-Endpoint gesetzt, im run()-Loop pro URL geprüft.
_CANCEL_EVENT = threading.Event()
_HISTORY_CANCEL_EVENT = threading.Event()

# Anzahl automatischer Wiederholungen, bevor ein fehlgeschlagener Download
# ausschließlich über die manuelle Prüfung erneut angestoßen wird.
MAX_DOWNLOAD_ATTEMPTS = 3

def cancel_job() -> dict:
    """Setzt das Cancel-Flag. Der laufende Scraper bricht beim nächsten
    URL-Check ab. Nicht-blockierend - kein subprocess wird hier gekillt
    (yt-dlp läuft, fertige URLs werden komplett verarbeitet)."""
    _CANCEL_EVENT.set()
    from .locks import request_cancel
    request_cancel("scraper")
    return {"ok": True}


def is_cancelled() -> bool:
    from .locks import cancel_requested
    return _CANCEL_EVENT.is_set() or cancel_requested("scraper")


def reset_cancel() -> None:
    _CANCEL_EVENT.clear()
    from .locks import clear_cancel
    clear_cancel("scraper")


def cancel_history_job() -> dict:
    _HISTORY_CANCEL_EVENT.set()
    from .locks import request_cancel
    request_cancel("history-reanalyze")
    return {"ok": True}


def is_history_cancelled() -> bool:
    from .locks import cancel_requested
    return _HISTORY_CANCEL_EVENT.is_set() or cancel_requested("history-reanalyze")


def reset_history_cancel() -> None:
    _HISTORY_CANCEL_EVENT.clear()
    from .locks import clear_cancel
    clear_cancel("history-reanalyze")


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
                       description_original: Optional[str] = None) -> Path:
    """Schreibt Video + description.txt + info.json in den Ziel-Ordner.

    Wenn description_original gesetzt ist (= Caption wurde übersetzt), wird
    sie als description_original.txt zusätzlich geschrieben. So bleibt das
    Original erhalten für späteres Audit oder Re-Übersetzung.
    """
    from ..core.safety import (
        AtomicDirectoryCommit,
        atomic_copy_file,
        atomic_write_json,
        atomic_write_text,
    )

    with AtomicDirectoryCommit(target_dir) as commit:
        work_dir = commit.stage_dir
        file_base = commit.target_dir.name
        if video_path and video_path.exists():
            atomic_copy_file(video_path, work_dir / f"{file_base}{video_path.suffix}")
            # yt-dlp legt das Cover als video.jpg neben das Video
            # (--write-thumbnail). Mitkopieren als thumb.jpg.
            thumbnails = (
                sorted(video_path.parent.glob("*.jpg"))
                + sorted(video_path.parent.glob("*.webp"))
                + sorted(video_path.parent.glob("*.png"))
            )
            for thumbnail in thumbnails:
                atomic_copy_file(thumbnail, work_dir / f"thumb{thumbnail.suffix}")
                break
        if description:
            atomic_write_text(work_dir / "description.txt", description)
        if description_original:
            atomic_write_text(
                work_dir / "description_original.txt",
                description_original,
            )
        atomic_write_json(work_dir / "info.json", info)
        return commit.commit(
            manifest_source={"kind": "recipe", "name": file_base},
        )


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

        # PDF-Ausrichtung: Text-Layer lokal, Scan-Fallback optional via Tesseract OSD.
        pdf_cfg = cfg.get("pdf", default={}) or {}
        self.pdf_auto_rotate = bool(pdf_cfg.get("auto_rotate", True))
        self.pdf_use_tesseract_osd = bool(pdf_cfg.get("use_tesseract_osd", True))
        self.pdf_min_text_chars = max(4, int(pdf_cfg.get("min_text_chars", 20) or 20))
        self.pdf_text_dominance = min(1.0, max(0.5, float(pdf_cfg.get("text_dominance", 0.60) or 0.60)))
        self.pdf_osd_min_confidence = max(0.0, float(pdf_cfg.get("osd_min_confidence", 1.0) or 1.0))
        self.pdf_max_osd_pages = max(0, int(pdf_cfg.get("max_osd_pages", 100) or 100))
        self.pdf_use_ocr_vote = bool(pdf_cfg.get("use_ocr_vote", True))
        self.pdf_remove_blank_pages = bool(pdf_cfg.get("remove_blank_pages", True))
        self.pdf_auto_crop = bool(pdf_cfg.get("auto_crop", True))
        self.pdf_deskew_scans = bool(pdf_cfg.get("deskew_scans", True))
        self.pdf_ocr_scans = bool(pdf_cfg.get("ocr_scans", True))
        self.pdf_improve_contrast = bool(pdf_cfg.get("improve_contrast", True))
        self.pdf_sharpen_scans = bool(pdf_cfg.get("sharpen_scans", True))
        self.pdf_scan_dpi = max(180, min(400, int(pdf_cfg.get("scan_dpi", 300) or 300)))
        self.pdf_ocr_language = str(pdf_cfg.get("ocr_language", "deu+eng") or "deu+eng")[:80]
        self.pdf_keep_original = bool(pdf_cfg.get("keep_original", True))

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

    def _queue_recipe_image(self, recipe_id: Optional[int]) -> None:
        """Plant für neue Rezepte genau eine persistente Bildgenerierung ein."""
        cfg = getattr(self, "cfg", None)
        if cfg is None:
            return
        settings = cfg.get("ai", "image_generation", default={}) or {}
        if (
            not recipe_id
            or not bool(settings.get("enabled", True))
            or not bool(getattr(self, "analyzer_enabled", False))
        ):
            return
        try:
            self.db.background_task_enqueue(
                "recipe_image_generate",
                {"recipe_id": int(recipe_id), "batch_id": uuid.uuid4().hex},
                dedupe_key=str(int(recipe_id)),
            )
        except Exception:
            # Der Rezeptimport selbst bleibt erfolgreich; der fehlende Bildtask
            # ist über image_generation_status sichtbar und kann erneut gestartet werden.
            logger.exception("Bildgenerierung für Rezept #%s konnte nicht eingereiht werden", recipe_id)

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
        return _save_video_files(
            target,
            video,
            description,
            info,
            description_original,
        )

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
        return _save_video_files(
            target,
            video,
            description,
            info,
            description_original,
        )

    # ---------------- URL-Verarbeitung ----------------
    def process_url(self, item: Dict) -> Dict:
        """Analysiert Social-Link-Metadaten, ohne Medien herunterzuladen.

        TikTok-/Instagram-Medien bleiben in der App externe Links. Reichen
        Caption und Cover nicht aus, wird das Video nur temporär für Frame-OCR
        und Audiotranskription geladen. Sobald ein Rezeptname erkannt ist,
        entsteht ein Rezept; fehlende Zutaten oder Schritte bleiben dort als
        sichtbare manuelle Pflegeaufgabe erhalten.
        """
        from ..core.email_processor import normalize_content_url

        raw_url = str(item.get("url") or "")
        url = normalize_content_url(raw_url)
        content_type = str(item.get("type") or "recipe")
        if not url:
            return {
                "url": raw_url,
                "type": content_type,
                "status": "error",
                "error": "ungültiger TikTok-/Instagram-Post",
            }
        source_url = url
        result: Dict = {"url": url, "type": content_type, "status": "error"}
        # Frühere Versionen führten Download-Fehler. Im Link-only-Modell ist
        # dieser Zustand nicht mehr relevant und darf den Import nicht blockieren.
        self.db.download_failure_clear(url)

        existing = self.db.history_get(url)
        source_recipe = self.db.recipe_get_by_url(url)
        is_tiktok_short_link = urlsplit(url).hostname in {
            "vm.tiktok.com", "vt.tiktok.com",
        }
        if (
            existing
            and existing.get("target_dir")
            and Path(existing["target_dir"]).is_dir()
            and not is_tiktok_short_link
        ):
            return {
                **result,
                "status": "already_processed",
                "target": existing["target_dir"],
                "recipe_id": int(source_recipe["id"]) if source_recipe else None,
            }

        existing_pending = self.db.pending_get(url)
        if (
            existing_pending
            and existing_pending.get("status") == "pending"
            and not item.get("reanalyze_existing")
            and not is_tiktok_short_link
        ):
            return {**result, "status": "pending", "name": "Unvollständiger Link-Import"}

        metadata = self._fetch_external_link_metadata(url) or {}
        canonical_url = normalize_content_url(
            str(metadata.get("canonical_url") or "")
        )
        if canonical_url:
            url = canonical_url
            result["url"] = url
            if url != source_url:
                result["source_url"] = source_url
                self.db.download_failure_clear(url)

                canonical_recipe = self.db.recipe_get_by_url(url)
                if source_recipe:
                    from ..recipes.manage import safe_canonicalize_recipe_url

                    migrated = safe_canonicalize_recipe_url(
                        self.db,
                        int(source_recipe["id"]),
                        expected_url=source_url,
                        canonical_url=url,
                    )
                    if migrated.get("ok"):
                        if existing_pending:
                            self.db.pending_resolve(source_url, status="resolved")
                            self._remove_pending_files(existing_pending)
                        return {
                            **result,
                            "status": "already_processed",
                            "target": migrated.get("folder_path"),
                            "recipe_id": migrated.get("recipe_id"),
                        }
                if canonical_recipe:
                    if existing_pending:
                        self.db.pending_resolve(source_url, status="resolved")
                        self._remove_pending_files(existing_pending)
                    return {
                        **result,
                        "status": "already_processed",
                        "target": canonical_recipe.get("folder_path"),
                        "recipe_id": int(canonical_recipe["id"]),
                    }

                canonical_history = self.db.history_get(url)
                if (
                    canonical_history
                    and canonical_history.get("target_dir")
                    and Path(canonical_history["target_dir"]).is_dir()
                ):
                    if existing_pending:
                        self.db.pending_resolve(source_url, status="resolved")
                        self._remove_pending_files(existing_pending)
                    return {
                        **result,
                        "status": "already_processed",
                        "target": canonical_history["target_dir"],
                    }

                canonical_pending = self.db.pending_get(url)
                if canonical_pending:
                    existing_pending = canonical_pending
                elif existing_pending and existing_pending.get("status") == "pending":
                    # Die vorhandene Kurzlink-Zeile liefert weiterhin Caption
                    # und Cover; neu gespeichert wird anschließend nur noch die
                    # kanonische URL.
                    pass

        platform = "TikTok" if "tiktok.com" in url else "Instagram"
        description = metadata.get("description_text")
        thumbnail_bytes = metadata.get("thumbnail_bytes")
        thumbnail_suffix = metadata.get("thumbnail_suffix")
        if not thumbnail_bytes and existing_pending:
            thumbnail_bytes, thumbnail_suffix = self._read_pending_thumbnail(existing_pending)
        suggestion = {
            "name": f"{platform}-Rezept prüfen",
            "type": "Sonstiges",
            "category": "Allgemein",
            "confidence": 0.0,
            "source": "external-link",
            "platform": platform,
            "analysis_state": "metadata_unavailable",
            "ingredients": [],
            "steps": [],
            "servings": None,
        }

        structured_recipe = None
        if content_type == "recipe":
            working_description = str(description or "").strip()
            if working_description:
                analysis = self._analyze_recipe(working_description)
                structured_recipe = self._extract_recipe_data(working_description)
            else:
                analysis = RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)
                structured_recipe = ExtractedRecipeData()
            thumbnail_scanned = False
            video_result = None

            # Ein Social-Cover kann eine komplette Rezeptkarte oder einen
            # Screenshot mit Zutaten enthalten. Bei unvollständiger Caption
            # wird es deshalb als echte Vision-Quelle ausgewertet.
            if not self._recipe_data_complete(analysis, structured_recipe) and thumbnail_bytes:
                thumbnail_text = self._extract_social_thumbnail_text(
                    thumbnail_bytes,
                    thumbnail_suffix,
                    f"{platform}-Cover zu {url}",
                )
                if thumbnail_text:
                    thumbnail_scanned = True
                    working_description = self._combine_social_text(
                        working_description,
                        "LESBARER REZEPTTEXT AUS DEM BILD",
                        thumbnail_text,
                    )
                    analysis = self._analyze_recipe(working_description)
                    structured_recipe = self._extract_recipe_data(working_description)
                elif not self._recipe_name_found(analysis):
                    mime = (
                        "image/png"
                        if str(thumbnail_suffix or "").lower() == ".png"
                        else "image/jpeg"
                    )
                    image_analysis = self._analyze_image_via_openai(
                        thumbnail_bytes,
                        mime,
                        "recipe",
                        f"{platform}-Cover",
                    )
                    if self._recipe_name_found(image_analysis):
                        analysis = image_analysis

            # Caption und Cover reichen nicht: Video nur temporär laden. Erst
            # wenige Frames auf eingeblendete Mengen prüfen, dann die Audiospur
            # transkribieren. Das Video wird danach wieder aus temp entfernt.
            if not self._recipe_data_complete(analysis, structured_recipe):
                video_result = self._analyze_social_video(url, working_description)
                if video_result and video_result.content is not None:
                    if video_result.evidence_text:
                        working_description = video_result.evidence_text
                    structured_recipe = self._structured_from_video_content(
                        video_result.content,
                        working_description,
                    )
                    video_analysis = self._analyze_recipe(working_description)
                    if self._recipe_name_found(video_analysis) or not self._recipe_name_found(analysis):
                        analysis = video_analysis

            complete = self._recipe_data_complete(analysis, structured_recipe)
            analysis = self._prepare_recipe_analysis(analysis, complete=complete)
            description = working_description
            suggestion.update({
                "name": analysis.name,
                "type": analysis.type,
                "category": analysis.category or "Allgemein",
                "confidence": analysis.confidence,
                "analysis_state": "complete" if complete else "incomplete",
                "ingredients": structured_recipe.ingredients,
                "steps": structured_recipe.steps,
                "servings": structured_recipe.servings,
                "tags": structured_recipe.tags,
                "extraction_method": structured_recipe.method,
                "warnings": structured_recipe.warnings,
                "thumbnail_vision_used": thumbnail_scanned,
                "video_frames_with_text": (
                    video_result.frame_text_count if video_result else 0
                ),
                "audio_transcribed": bool(video_result and video_result.transcribed),
                "video_analysis_reason": video_result.reason if video_result else None,
            })
            # Neue Regel: Ein erkannter Rezeptname erzeugt immer einen
            # Rezeptdatensatz. Fehlende Zutaten/Schritte bleiben über
            # needs_manual_care sichtbar, statt den Import im Pending zu parken.
            if self._recipe_name_found(analysis):
                target, recipe_id = self._save_external_link_recipe(
                    url=url,
                    platform=platform,
                    analysis=analysis,
                    description=description,
                    structured=structured_recipe,
                    thumbnail_bytes=thumbnail_bytes,
                    thumbnail_suffix=thumbnail_suffix,
                )
                if existing_pending:
                    self.db.pending_resolve(url, status="resolved")
                    self._remove_pending_files(existing_pending)
                if source_url != url:
                    source_pending = self.db.pending_get(source_url)
                    if source_pending and source_pending.get("status") == "pending":
                        self.db.pending_resolve(source_url, status="resolved")
                        self._remove_pending_files(source_pending)
                result.update({
                    "status": "auto",
                    "name": analysis.name,
                    "platform": platform,
                    "target": str(target),
                    "recipe_id": recipe_id,
                    "needs_manual_care": not complete,
                    "ingredients": len(structured_recipe.ingredients),
                    "steps": len(structured_recipe.steps),
                    "video_frames_with_text": suggestion["video_frames_with_text"],
                    "audio_transcribed": suggestion["audio_transcribed"],
                    "message": (
                        "Rezeptname, Zutaten und Schritte wurden erkannt und importiert."
                        if complete
                        else "Rezeptname erkannt; das unvollständige Rezept wurde importiert und zur manuellen Pflege markiert."
                    ),
                })
                return result
        elif description and content_type == "wedding":
            analysis = self._analyze_wedding(description)
            suggestion.update({
                "name": analysis.name,
                "category": analysis.category or "Sonstiges",
                "confidence": analysis.confidence,
                "analysis_state": "incomplete",
            })

        frame_path = None
        if thumbnail_bytes:
            frame_path = self._stash_external_thumbnail_for_pending(
                thumbnail_bytes,
                thumbnail_suffix,
                url,
            )
            suggestion["has_thumbnail"] = True
        self.db.pending_add(
            url=url,
            content_type=content_type,
            description=(description or "")[:5000] or None,
            video_path=None,
            frame_path=frame_path,
            ai_suggestion=suggestion,
        )
        if source_url != url:
            source_pending = self.db.pending_get(source_url)
            if source_pending and source_pending.get("status") == "pending":
                self.db.pending_resolve(source_url, status="resolved")
                self._remove_pending_files(source_pending)
        result.update({
            "status": "pending",
            "name": suggestion["name"],
            "platform": platform,
            "message": (
                "Link und Caption wurden geprüft. Fehlende Zutaten oder Schritte bitte unter „Manuelle Prüfung“ ergänzen."
                if description
                else "Link gespeichert. Die Caption war nicht abrufbar und muss unter „Manuelle Prüfung“ ergänzt werden."
            ),
        })
        return result

    def _recipe_data_complete(self, analysis: RecipeAnalysis, structured) -> bool:
        return bool(
            structured
            and structured.ingredients
            and structured.steps
            and not analysis.needs_manual_input(self.confidence_threshold)
        )

    @staticmethod
    def _recipe_name_found(analysis: Optional[RecipeAnalysis]) -> bool:
        """Ein belastbarer Name reicht für einen sichtbaren Rezeptimport.

        Zutaten und Schritte dürfen fehlen; die Rezept-API markiert den Datensatz
        anschließend automatisch als manuell zu pflegen. Generische Platzhalter
        bleiben dagegen in der Prüfliste und erzeugen keine Karteileichen.
        """
        if analysis is None:
            return False
        name = " ".join(str(analysis.name or "").split()).casefold()
        placeholders = {
            "", "unbekannt", "rezept", "tiktok-rezept", "instagram-rezept",
            "tiktok-rezept prüfen", "instagram-rezept prüfen", "rezept prüfen",
        }
        return len(name) >= 3 and name not in placeholders and not name.endswith("rezept prüfen")

    def _prepare_recipe_analysis(self, analysis: RecipeAnalysis, *, complete: bool) -> RecipeAnalysis:
        """Ersetzt nur fehlende Ablagekategorien, niemals den erkannten Namen."""
        if not self._recipe_name_found(analysis):
            return analysis
        recipe_type = str(analysis.type or "").strip()
        if not recipe_type or recipe_type.casefold() == "unbekannt":
            recipe_type = "Sonstiges"
        category = str(analysis.category or "").strip()
        if not category or category.casefold() == "unbekannt":
            category = "Allgemein"
        return RecipeAnalysis(
            name=str(analysis.name).strip(),
            type=recipe_type,
            category=category,
            confidence=float(analysis.confidence or 0),
            is_manual=bool(analysis.is_manual or not complete),
        )

    @staticmethod
    def _combine_social_text(description: Optional[str], label: str, evidence: str) -> str:
        base = str(description or "").strip()
        extra = str(evidence or "").strip()
        if not extra:
            return base
        if extra.casefold() in base.casefold():
            return base
        if not base:
            return extra[:30000]
        return f"{base}\n\n{label}:\n{extra}"[:30000]

    @staticmethod
    def _structured_from_video_content(content: dict, evidence_text: str) -> ExtractedRecipeData:
        steps = []
        for item in content.get("steps") or []:
            instruction = str(item.get("instruction") or "").strip()
            if not instruction:
                continue
            timer = item.get("timer_seconds")
            try:
                timer = int(timer) if timer is not None and int(timer) > 0 else None
            except (TypeError, ValueError):
                timer = None
            steps.append({"instruction": instruction, "timer_seconds": timer})
        servings = content.get("servings")
        try:
            servings = int(servings) if servings is not None and int(servings) > 0 else None
        except (TypeError, ValueError):
            servings = None
        return ExtractedRecipeData(
            text=str(evidence_text or "").strip(),
            ingredients=prepare_recipe_ingredients(content.get("ingredients") or []),
            steps=steps,
            servings=servings,
            tags=sorted({
                str(tag).strip().casefold()
                for tag in content.get("tags") or []
                if str(tag).strip()
            })[:60],
            allergen_info=content.get("allergen_info"),
            method="video-ai",
        )

    def _extract_social_thumbnail_text(
        self,
        data: Optional[bytes],
        suffix: Optional[str],
        context: str,
    ) -> Optional[str]:
        analyzer = getattr(self, "analyzer", None)
        extract = getattr(analyzer, "extract_description_from_image_bytes", None)
        if not data or not callable(extract):
            return None
        mime = "image/png" if str(suffix or "").lower() == ".png" else "image/jpeg"
        try:
            return str(extract(data, mime, context) or "").strip() or None
        except Exception as exc:
            logger.warning("Social-Cover konnte nicht per Vision gelesen werden: %s", exc)
            return None

    def _analyze_social_video(self, url: str, description: str) -> Optional[VideoAnalysisResult]:
        analyzer = getattr(self, "analyzer", None)
        download = getattr(getattr(self, "downloader", None), "download", None)
        if analyzer is None or not callable(download):
            return None
        video_path = None
        try:
            video_path = download(url)
            if not video_path:
                return None
            video_description = description
            read_description = getattr(self.downloader, "read_description", None)
            if not video_description and callable(read_description):
                video_description = str(read_description(Path(video_path)) or "").strip()
            tags, canonical = existing_hints(self.db)
            cfg = getattr(self, "cfg", None)
            ai_config = cfg.get("ai", default={}) if cfg is not None else {}
            return analyze_recipe_video_file(
                analyzer,
                Path(video_path),
                ai_config=ai_config or {},
                existing_tags=tags,
                existing_canonical=canonical,
                description=video_description,
            )
        except Exception as exc:
            logger.warning("Video-Fallback für %s fehlgeschlagen: %s", url, exc)
            return None
        finally:
            if video_path:
                try:
                    parent = Path(video_path).resolve(strict=False).parent
                    temp_root = self.temp_dir.resolve(strict=False)
                    if parent.parent == temp_root and parent != temp_root:
                        shutil.rmtree(parent, ignore_errors=True)
                except (OSError, RuntimeError, ValueError):
                    logger.warning("Temporärer Video-Download für %s konnte nicht bereinigt werden", url)

    def _save_external_link_recipe(
        self,
        *,
        url: str,
        platform: str,
        analysis: RecipeAnalysis,
        description: str,
        structured,
        thumbnail_bytes: Optional[bytes] = None,
        thumbnail_suffix: Optional[str] = None,
    ) -> tuple[Path, int]:
        """Speichert ein erkanntes Link-Rezept ohne Video, optional mit Cover."""
        from ..core.safety import (
            atomic_write_bytes,
            atomic_write_json,
            atomic_write_text,
            write_manifest,
        )

        existing = self.db.recipe_get_by_url(url)
        if existing:
            return Path(existing["folder_path"]), int(existing["id"])

        target = (
            self.recipe_dir
            / _sanitize(analysis.type)
            / _sanitize(analysis.category or "Allgemein")
            / _sanitize(analysis.name)
        )
        if target.exists():
            target = target.parent / f"{target.name}_{datetime.now():%Y%m%d_%H%M%S}"
        target.mkdir(parents=True, exist_ok=False)
        info = {
            "url": url,
            "name": analysis.name,
            "type": analysis.type,
            "category": analysis.category or "Allgemein",
            "confidence": analysis.confidence,
            "content_type": "recipe",
            "source": "external-link",
            "platform": platform,
            "description": description[:5000],
            "timestamp": datetime.now().isoformat(),
            "is_manual": not self._recipe_data_complete(analysis, structured),
            "recipe_extraction": {
                "method": structured.method,
                "ingredients": len(structured.ingredients),
                "steps": len(structured.steps),
                "servings": structured.servings,
                "warnings": structured.warnings,
            },
        }
        recipe_id: Optional[int] = None
        try:
            thumb_filename = None
            if thumbnail_bytes:
                suffix = self._safe_thumbnail_suffix(thumbnail_suffix)
                thumb_filename = f"{target.name}{suffix}"
                atomic_write_bytes(target / thumb_filename, thumbnail_bytes)
            atomic_write_text(target / "description.txt", description)
            atomic_write_json(target / "info.json", info)
            write_manifest(target, source={"kind": "external-link", "url": url})
            recipe_id = self.db.recipe_upsert(
                url=url,
                name=analysis.name,
                type=analysis.type,
                category=analysis.category or "Allgemein",
                folder_path=str(target),
                description=description,
                thumb_filename=thumb_filename,
                video_filename=None,
                source_added_at=time.time(),
            )
            applied = apply_extracted_recipe_data(
                self.db,
                recipe_id,
                structured,
                actor="social-link-ai",
                overwrite=False,
                create_version=False,
                update_description=True,
            )
            if not applied.get("ok"):
                raise RuntimeError(applied.get("error") or "Rezeptdaten konnten nicht gespeichert werden")
            self.db.history_add(
                url,
                content_type="recipe",
                name=analysis.name,
                target_dir=str(target),
            )
            self._queue_recipe_image(recipe_id)
            return target, recipe_id
        except Exception:
            # Der Ordner wurde ausschließlich für diesen neuen Import angelegt.
            # Bei einem späteren History-Fehler müssen auch die bereits
            # angelegte Recipe-Zeile und ihre Kinddaten kompensiert werden.
            if recipe_id is not None:
                try:
                    self.db.recipe_delete(recipe_id)
                except Exception:
                    logger.exception(
                        "Social-Import-Rollback für Recipe #%s fehlgeschlagen",
                        recipe_id,
                    )
            shutil.rmtree(target, ignore_errors=True)
            raise

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
            r = self.analyzer.request(
                "POST",
                "/chat/completions",
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
                                ext: str, info: Dict, source_text: Optional[str] = None,
                                original_pdf_data: Optional[bytes] = None) -> None:
        """Schreibt die Attachment-Datei + info.json + optional die extrahierte
        Text-Description in den target_dir."""
        from ..core.safety import atomic_write_bytes, atomic_write_text, atomic_write_json, write_manifest
        target_dir.mkdir(parents=True, exist_ok=True)
        file_base = target_dir.name
        atomic_write_bytes(target_dir / f"{file_base}{ext}", attachment_data)
        if ext == ".pdf" and original_pdf_data and original_pdf_data != attachment_data:
            original_dir = target_dir / ".pdf-originals"
            original_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(original_dir / f"{file_base}.original.pdf", original_pdf_data)
        if source_text:
            atomic_write_text(target_dir / "description.txt", source_text)
        atomic_write_json(target_dir / "info.json", info)
        try:
            write_manifest(target_dir, source={"kind": "attachment", "name": file_base})
        except Exception:
            pass

    def _stash_attachment_for_pending(self, data: bytes, ext: str, synth_url: str) -> str:
        """Bewahrt einen unsicheren Dateiimport bis zur manuellen Freigabe auf."""
        import hashlib
        from ..core.safety import atomic_write_bytes

        pending_dir = self.temp_dir / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(synth_url.encode("utf-8")).hexdigest()[:24]
        target = pending_dir / f"attachment-{digest}{ext}"
        atomic_write_bytes(target, data)
        return str(target)

    @staticmethod
    def _safe_thumbnail_suffix(suffix: Optional[str]) -> str:
        normalized = str(suffix or "").strip().lower()
        return normalized if normalized in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"

    def _stash_external_thumbnail_for_pending(
        self,
        data: bytes,
        suffix: Optional[str],
        url: str,
    ) -> str:
        """Bewahrt das Social-Media-Cover bis zur manuellen Freigabe auf."""
        import hashlib
        from ..core.safety import atomic_write_bytes

        pending_dir = self.temp_dir / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        target = pending_dir / f"external-thumb-{digest}{self._safe_thumbnail_suffix(suffix)}"
        atomic_write_bytes(target, data)
        return str(target)

    def _read_pending_thumbnail(self, entry: Dict) -> Tuple[Optional[bytes], Optional[str]]:
        """Liest ausschließlich ein von uns im Temp-Verzeichnis abgelegtes Cover."""
        path = Path(str(entry.get("frame_path") or ""))
        try:
            if path.is_symlink():
                raise ValueError("symlink")
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.temp_dir.resolve())
            if not resolved.is_file() or resolved.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("invalid size")
            return resolved.read_bytes(), self._safe_thumbnail_suffix(resolved.suffix)
        except (OSError, RuntimeError, ValueError):
            return None, None

    def _extract_recipe_data(self, description: str):
        """Liest Zutaten, Schritte, Portionen und Tags aus erkanntem Rezepttext.

        Der lokale Parser funktioniert ohne Cloud; bei konfiguriertem Analyzer
        ergänzt die KI schwierig formatierte Zutatenlisten und Arbeitsschritte.
        """
        tags, canonical = existing_hints(self.db)
        return extract_recipe_data(
            description, analyzer=self.analyzer if self.analyzer_enabled else None,
            existing_tags=tags, existing_canonical=canonical,
        )

    def _extract_pdf_recipe_data(self, description: str):
        """Kompatibilitätsalias für bestehende PDF-Aufrufer und Tests."""
        return self._extract_recipe_data(description)

    def _index_saved_attachment_recipe(self, target: Path, analysis: RecipeAnalysis,
                                       synth_url: str, description: str,
                                       structured) -> Optional[int]:
        """Legt ein PDF-Rezept sofort in der DB an und übernimmt die erkannten
        Zutaten. Damit muss der Benutzer nicht erst die Rezeptseite öffnen, um
        den allgemeinen Hintergrundindexer anzustoßen.
        """
        try:
            recipe_id = self.db.recipe_upsert(
                url=synth_url, name=analysis.name, type=analysis.type,
                category=analysis.category or "Allgemein",
                folder_path=str(target), description=description,
                thumb_filename=None, video_filename=None,
                source_added_at=time.time(),
            )
            applied = apply_extracted_recipe_data(
                self.db, recipe_id, structured, actor="mail-import",
                overwrite=False, create_version=False, update_description=True,
            )
            if not applied.get("ok"):
                logger.warning("PDF-Rezeptdaten konnten nicht gespeichert werden: %s", applied)
            self._queue_recipe_image(recipe_id)
            return recipe_id
        except Exception as exc:
            logger.exception("PDF-Rezept konnte nicht direkt indiziert werden: %s", exc)
            return None

    def process_attachment(self, att: Dict, synth_url: str) -> Dict:
        """Verarbeitet ein Mail-Attachment (PDF/JPG/PNG):

        - PDF: Text via pdfplumber/pypdf extrahieren, durch Text-Analyzer
        - JPG/PNG: bei OpenAI-Provider via Vision-API; sonst Subject-Fallback
        - Ergebnis wie Video-Pipeline: Auto-Save bei hoher Confidence, sonst Pending
        """
        ext = att["ext"]
        content_type = att["type"]
        data = att["data"]
        original_pdf_data = data if ext == ".pdf" else None
        subject = att.get("subject", "")
        body_excerpt = att.get("body_excerpt", "")
        default_cat = att.get("default_category") or "Sonstiges"
        source_kind = str(att.get("source") or "mail-attachment")
        result: Dict = {"url": synth_url, "type": content_type, "status": "error"}
        pdf_rotation = None
        structured_recipe = None

        # PDF vor Textanalyse und Ablage konservativ aufbereiten. Das Original
        # wird bei jeder echten Änderung versteckt im Rezeptordner aufbewahrt.
        if ext == ".pdf" and (self.pdf_auto_rotate or self.pdf_remove_blank_pages
                              or self.pdf_auto_crop or self.pdf_deskew_scans
                              or self.pdf_ocr_scans or self.pdf_improve_contrast
                              or self.pdf_sharpen_scans):
            data, pdf_rotation = process_pdf_bytes(
                data,
                auto_rotate=self.pdf_auto_rotate,
                use_tesseract_osd=self.pdf_use_tesseract_osd,
                use_ocr_vote=self.pdf_use_ocr_vote,
                remove_blank_pages=self.pdf_remove_blank_pages,
                auto_crop=self.pdf_auto_crop,
                deskew_scans=self.pdf_deskew_scans,
                ocr_scans=self.pdf_ocr_scans,
                improve_contrast=self.pdf_improve_contrast,
                sharpen_scans=self.pdf_sharpen_scans,
                scan_dpi=self.pdf_scan_dpi,
                ocr_language=self.pdf_ocr_language,
                min_text_chars=self.pdf_min_text_chars,
                text_dominance=self.pdf_text_dominance,
                osd_min_confidence=self.pdf_osd_min_confidence,
                max_osd_pages=self.pdf_max_osd_pages,
            )
            if pdf_rotation.changed:
                logger.info(
                    "PDF aufbereitet: %s (rotate=%s crop=%s blank=%s deskew=%s ocr=%s contrast=%s)",
                    att.get("filename"), pdf_rotation.rotated_pages,
                    pdf_rotation.cropped_pages, pdf_rotation.removed_blank_pages,
                    pdf_rotation.deskewed_pages, pdf_rotation.ocr_pages,
                    pdf_rotation.contrast_pages,
                )

        # Description bestimmen
        if ext == ".pdf":
            description = self._extract_pdf_text(data) or f"{subject}\n\n{body_excerpt}"
        else:  # .jpg / .jpeg / .png
            description = f"{subject}\n\n{body_excerpt}".strip()

        # Ein manueller Bild-Upload wurde bislang nur grob klassifiziert. Jetzt
        # wird der lesbare Rezepttext sofort per Vision transkribiert, damit
        # Zutaten und Schritte noch in derselben Import-Pipeline entstehen.
        image_mime = None
        if ext in (".jpg", ".jpeg", ".png"):
            image_mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        if (
            image_mime
            and content_type == "recipe"
            and self.analyzer
            and hasattr(self.analyzer, "extract_description_from_image_bytes")
        ):
            extracted = self.analyzer.extract_description_from_image_bytes(
                data,
                image_mime,
                subject,
            )
            if extracted:
                description = extracted

        if not description and ext != ".pdf":
            # Kein Text greifbar
            description = subject or "(kein Subject)"

        # PDF- und Bild-Rezepte sofort strukturiert auslesen. Die Ausgabe wird
        # sowohl in Pending-Vorschlägen als auch beim Auto-Import verwendet.
        if content_type == "recipe":
            structured_recipe = self._extract_recipe_data(description)

        try:
            if content_type == "recipe":
                # Wenn Vision lesbaren Text geliefert hat, läuft auch die
                # Klassifizierung textbasiert. Ein reines Food-Foto nutzt als
                # letzten Fallback weiterhin die grobe Bild-Klassifizierung.
                analysis = None
                if _has_usable_description(description, self.min_desc_len):
                    analysis = self._analyze_recipe(description)
                elif image_mime:
                    analysis = self._analyze_image_via_openai(
                        data, image_mime, "recipe", subject,
                    )
                if not analysis:
                    analysis = self._analyze_recipe(description)

                complete = self._recipe_data_complete(analysis, structured_recipe)
                analysis = self._prepare_recipe_analysis(analysis, complete=complete)
                if not self._recipe_name_found(analysis):
                    pending_path = self._stash_attachment_for_pending(data, ext, synth_url)
                    self.db.pending_add(
                        url=synth_url, content_type="recipe",
                        description=description[:5000],
                        # Das Feld heißt aus historischen Gründen video_path,
                        # hält bei Dateiimporten aber die Originaldatei zur
                        # späteren manuellen Freigabe.
                        video_path=pending_path,
                        ai_suggestion={
                            "name": analysis.name, "type": analysis.type,
                            "category": analysis.category, "confidence": analysis.confidence,
                            "source": source_kind, "filename": att["filename"],
                            "pdf_processing": pdf_rotation.as_dict() if pdf_rotation else None,
                            "ingredients": (structured_recipe.ingredients if structured_recipe else []),
                            "steps": (structured_recipe.steps if structured_recipe else []),
                            "servings": (structured_recipe.servings if structured_recipe else None),
                            "extraction_method": (structured_recipe.method if structured_recipe else None),
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
                        "content_type": "recipe", "source": source_kind,
                        "is_manual": not complete,
                        "filename": att["filename"], "mail_subject": subject,
                        "pdf_processing": pdf_rotation.as_dict() if pdf_rotation else None,
                        "description": description[:5000],
                        "pdf_recipe_extraction": {
                            "method": structured_recipe.method if structured_recipe else None,
                            "ingredients": len(structured_recipe.ingredients) if structured_recipe else 0,
                            "steps": len(structured_recipe.steps) if structured_recipe else 0,
                            "servings": structured_recipe.servings if structured_recipe else None,
                            "warnings": structured_recipe.warnings if structured_recipe else [],
                        },
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._save_attachment_file(
                        target, data, ext, info, description,
                        original_pdf_data=original_pdf_data if self.pdf_keep_original else None,
                    )
                    recipe_id = None
                    if structured_recipe is not None:
                        recipe_id = self._index_saved_attachment_recipe(
                            target, analysis, synth_url, description, structured_recipe,
                        )
                    self.db.history_add(synth_url, content_type="recipe",
                                        name=analysis.name, target_dir=str(target))
                    result.update({
                        "status": "auto", "name": analysis.name, "target": str(target),
                        "recipe_id": recipe_id,
                        "ingredients": len(structured_recipe.ingredients) if structured_recipe else 0,
                        "steps": len(structured_recipe.steps) if structured_recipe else 0,
                        "needs_manual_care": not complete,
                    })

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
                    pending_path = self._stash_attachment_for_pending(data, ext, synth_url)
                    self.db.pending_add(
                        url=synth_url, content_type="wedding",
                        description=description[:5000],
                        video_path=pending_path,
                        ai_suggestion={
                            "name": analysis.name, "category": analysis.category or default_cat,
                            "confidence": analysis.confidence,
                            "source": source_kind, "filename": att["filename"],
                            "pdf_processing": pdf_rotation.as_dict() if pdf_rotation else None,
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
                        "content_type": "wedding", "source": source_kind,
                        "filename": att["filename"], "mail_subject": subject,
                        "pdf_processing": pdf_rotation.as_dict() if pdf_rotation else None,
                        "description": description[:5000],
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._save_attachment_file(
                        target, data, ext, info, description,
                        original_pdf_data=original_pdf_data if self.pdf_keep_original else None,
                    )
                    self.db.history_add(synth_url, content_type="wedding",
                                        name=analysis.name, target_dir=str(target))
                    result.update({"status": "auto", "name": analysis.name, "target": str(target)})
        except Exception as e:
            logger.exception(f"process_attachment fail {att.get('filename')}: {e}")
            result["error"] = str(e)

        return result

    def _stash_for_pending(self, video: Path) -> Optional[str]:
        """Kopiert das Temp-Video an einen persistenten Pending-Ort, da der
        Temp-Download-Ordner danach via _cleanup_temp gelöscht wird. Rückgabe
        = interner Pfad für pending.video_path. Die Datei wird bewusst nicht
        über HTTP ausgeliefert und nur beim Auflösen des Eingangs bzw. durch
        _remove_pending_files weiterverarbeitet oder entfernt. None bei Fehler
        — der Pending-Eintrag bleibt dann ohne Datei, der Lauf crasht aber nicht."""
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

        # Link-Imports sind seit der Link-only-Umstellung KI-frei. Ein Ausfall
        # des optionalen Analyzers darf deshalb weder das Mail-Abrufen noch das
        # persistente Ablegen solcher Links blockieren. Attachment-Parser
        # behandeln ihren jeweiligen KI-Fehler weiterhin pro Element.
        if self.analyzer_enabled and self.analyzer and not self.analyzer.health():
            msg = (f"OpenAI nicht erreichbar oder Modell '{self.analyzer.model}' nicht verfügbar - "
                   f"KI-abhängige Anhänge können fehlschlagen; Link-Import läuft weiter")
            logger.warning(msg)
            summary["ai_available"] = False
            summary["warning"] = msg
        else:
            summary["ai_available"] = bool(self.analyzer_enabled and self.analyzer)

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

        # Retry-Kandidaten aus download_failures (attempts < MAX). Quelle der
        # Wahrheit für Wiederholungen seit verarbeitete Mails gelöscht werden —
        # die URL steht in keiner Mail mehr.
        known = {it["url"] for it in new_items}
        for cand in self.db.download_failures_retry_candidates(MAX_DOWNLOAD_ATTEMPTS):
            if cand["url"] in known or self.db.history_has(cand["url"]) \
                    or cand["url"] in pending_urls:
                continue
            new_items.append({"url": cand["url"],
                              "type": cand.get("content_type") or "recipe",
                              "source_account": None, "mail_uid": None})
        summary["new"] = len(new_items)
        logger.info(f"Neue URLs: {len(new_items)}, Attachments: {len(attach_items)}")

        # Mail-Accounting: eine Mail darf erst gelöscht werden, wenn ALLE ihre
        # Items (URLs + Attachments) in diesem Lauf verarbeitet oder als
        # bereits bekannt geskippt wurden. Bei Cancel wird nichts gelöscht.
        mail_total: Dict[tuple, int] = {}
        mail_done: Dict[tuple, int] = {}
        def _mail_key(it: Dict) -> Optional[tuple]:
            if it.get("source_account") and it.get("mail_uid"):
                return (it["source_account"], it["mail_uid"])
            return None
        for it in url_items + attach_items:
            k = _mail_key(it)
            if k:
                mail_total[k] = mail_total.get(k, 0) + 1
        def _mark_done(it: Dict) -> None:
            k = _mail_key(it)
            if k:
                mail_done[k] = mail_done.get(k, 0) + 1
        # Bereits bekannte URLs (history/pending-Dedup oben) sind erledigt:
        new_urls = {it["url"] for it in new_items}
        for it in url_items:
            if it["url"] not in new_urls:
                _mark_done(it)

        for item in new_items:
            # Cancel zwischen URLs prüfen - laufende process_url-Calls
            # werden nicht unterbrochen, neue starten aber nicht mehr.
            if is_cancelled():
                logger.warning(f"Scraper cancelled - {len([i for i in new_items if i == item]) } URLs übersprungen")
                summary["cancelled"] = True
                break

            url = item["url"]

            accounted = False
            try:
                r = self.process_url(item)
                if r["status"] == "auto":
                    summary["auto"] += 1
                    summary[f"{item['type']}_auto"] += 1
                    accounted = True
                elif r["status"] == "pending":
                    summary["pending"] += 1
                    summary[f"{item['type']}_pending"] += 1
                    accounted = True
                elif r["status"] == "already_processed":
                    accounted = True
                else:
                    summary["errors"] += 1
            except Exception as e:
                logger.exception(f"URL fehlgeschlagen {url}: {e}")
                summary["errors"] += 1
            finally:
                # Nur persistierte Outcomes erlauben das Löschen der Quellmail.
                # Bei Fehler bleibt sie für einen späteren Lauf erhalten.
                if accounted:
                    _mark_done(item)

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
                _mark_done(att)
                continue
            accounted = False
            try:
                r = self.process_attachment(att, synth_url)
                if r.get("status") == "auto":
                    summary["attach_auto"] += 1
                    summary[f"{att['type']}_auto"] += 1
                    accounted = True
                elif r.get("status") == "pending":
                    summary["attach_pending"] += 1
                    summary[f"{att['type']}_pending"] += 1
                    accounted = True
                else:
                    summary["errors"] += 1
            except Exception as e:
                logger.exception(f"Attachment fehlgeschlagen {att.get('filename')}: {e}")
                summary["errors"] += 1
            finally:
                if accounted:
                    _mark_done(att)

        # Verarbeitete Mails löschen — nur wenn der Lauf nicht abgebrochen wurde
        # und ALLE Items der Mail accounted sind. Config-gated pro Konto
        # (email.<konto>.delete_processed: true).
        if not summary["cancelled"]:
            try:
                uids_by_account: Dict[str, set] = {}
                for (acc_name, uid), total in mail_total.items():
                    if mail_done.get((acc_name, uid), 0) >= total:
                        uids_by_account.setdefault(acc_name, set()).add(uid)
                deleted = self.router.delete_processed_mails(uids_by_account)
                summary["mails_deleted"] = deleted
            except Exception as e:
                logger.warning(f"Mail-Cleanup fehlgeschlagen (non-fatal): {e}")

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
        binary = getattr(self.downloader, "ytdlp_path", "")
        if not binary:
            logger.warning("yt-dlp metadata nicht verfügbar: Binary fehlt")
            return None
        cmd = [binary, "--skip-download", "--no-warnings", "--no-playlist",
               "--print", "%(description)s\n%(title)s"]
        if getattr(self.downloader, "cookies_file", None):
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

    def _fetch_external_link_metadata(self, url: str) -> Dict:
        """Holt Caption und Cover ohne Video-Download.

        ``refresh_metadata`` nutzt yt-dlp mit ``--skip-download``. Der alte
        Description-Pfad bleibt als Fallback erhalten, damit Installationen
        mit älterem Downloader und Tests ohne Mediendownload weiterlaufen.
        TikTok-Foto-Posts und manche Videos liefern über yt-dlp nur eine kurze
        oder leere Description. Deshalb wird die im Browser aufgeklappte
        Caption bereits beim Erstimport bevorzugt, nicht erst beim Re-Scrape.
        """
        metadata: Dict = {}
        refresh = getattr(self.downloader, "refresh_metadata", None)
        if callable(refresh):
            try:
                metadata = dict(refresh(url) or {})
            except Exception as exc:
                logger.warning("Social-Metadaten konnten nicht aktualisiert werden: %s", exc)
        cfg = getattr(self, "cfg", None)
        if cfg is not None:
            ytdlp_cfg = cfg.get("ytdlp", default={}) or {}
            from ..core.tiktok_caption import (
                fetch_expanded_tiktok_caption,
                fetch_tiktok_player_metadata,
                is_tiktok_url,
            )

            if is_tiktok_url(url):
                try:
                    timeout_seconds = int(
                        ytdlp_cfg.get("browser_timeout_seconds", 35)
                    )
                except (TypeError, ValueError):
                    timeout_seconds = 35
                player_meta = fetch_tiktok_player_metadata(
                    url,
                    timeout_seconds=timeout_seconds,
                )
                player_description = str(
                    player_meta.get("description_text") or ""
                ).strip()
                current_description = str(
                    metadata.get("description_text") or ""
                ).strip()
                if player_description and len(player_description) >= len(current_description):
                    metadata["description_text"] = player_description
                    metadata["description_source"] = "tiktok-player"
                for key in ("canonical_url", "thumbnail_bytes", "thumbnail_suffix"):
                    if player_meta.get(key) and not metadata.get(key):
                        metadata[key] = player_meta[key]

                # Der strukturierte Player enthält bei Foto-Posts bereits
                # vollständige Caption und Cover. Playwright bleibt als
                # Fallback für Videos/gesperrte Player-Antworten erhalten.
                if (
                    ytdlp_cfg.get("expanded_tiktok_caption", True)
                    and not (
                        player_meta.get("thumbnail_bytes")
                        and player_description
                    )
                ):
                    expanded = fetch_expanded_tiktok_caption(
                        url,
                        fallback_text=str(metadata.get("description_text") or ""),
                        cookies_file=getattr(self.downloader, "cookies_file", None),
                        timeout_seconds=timeout_seconds,
                        executable_path=str(
                            ytdlp_cfg.get("browser_executable_path") or ""
                        ).strip() or None,
                    )
                    if expanded:
                        metadata["description_text"] = expanded
                        metadata["description_source"] = "tiktok-browser"
        if not metadata.get("description_text"):
            description = self._fetch_description_via_ytdlp(url)
            if description:
                metadata["description_text"] = description
        return metadata

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
        cancelled = False
        processed = 0

        for i, entry in enumerate(items, 1):
            if is_history_cancelled():
                logger.info(f"Reanalyze-History abgebrochen bei {i}/{len(items)}")
                cancelled = True
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
            processed += 1

        return {
            "total": len(items),
            "updated": updated,
            "moved": moved,
            "unchanged": unchanged,
            "low_confidence": low_conf,
            "failed": failed,
            "processed": processed,
            "cancelled": cancelled,
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

        # Bei indizierten Rezepten ist die recipes-Tabelle die kanonische
        # Zuordnung für Medienendpunkte. Deshalb dieselbe rollback-fähige
        # Mutation wie der native Metadateneditor verwenden, statt nur die
        # History-Zeile und das Dateisystem zu verschieben.
        indexed_recipe = None
        if content_type == "recipe":
            indexed_recipe = (
                self.db.recipe_get_by_folder(str(old_dir))
                or self.db.recipe_get_by_folder(str(old_dir.resolve()))
            )
        if indexed_recipe:
            from ..recipes.manage import safe_update_recipe_metadata
            try:
                updated = safe_update_recipe_metadata(
                    self.db,
                    int(indexed_recipe["id"]),
                    name=new_name,
                    recipe_type=new_type or indexed_recipe.get("type") or "Sonstiges",
                    category=new_category or indexed_recipe.get("category") or "Allgemein",
                    description=indexed_recipe.get("description") or "",
                    servings=indexed_recipe.get("servings"),
                    url=indexed_recipe.get("url"),
                    target_folder_override=str(new_dir),
                )
            except (ValueError, RuntimeError) as exc:
                return {"ok": False, "error": str(exc)}
            try:
                self.db.history_update(
                    url,
                    name=new_name,
                    target_dir=str(updated["folder_path"]),
                    content_type=content_type,
                )
            except Exception as exc:
                try:
                    safe_update_recipe_metadata(
                        self.db,
                        int(indexed_recipe["id"]),
                        name=indexed_recipe.get("name") or old_dir.name,
                        recipe_type=indexed_recipe.get("type") or "Sonstiges",
                        category=indexed_recipe.get("category") or "Allgemein",
                        description=indexed_recipe.get("description") or "",
                        servings=indexed_recipe.get("servings"),
                        url=indexed_recipe.get("url"),
                        target_folder_override=str(old_dir),
                    )
                except Exception:
                    logger.exception("History-Move-Rollback für %s fehlgeschlagen", url)
                return {"ok": False, "error": f"History-Update fehlgeschlagen: {exc}"}
            self._cleanup_empty_parents(old_dir)
            return {
                "ok": True,
                "action": "moved",
                "target": str(updated["folder_path"]),
                "recipe_id": int(indexed_recipe["id"]),
            }

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
                from ..core.safety import atomic_write_json
                atomic_write_json(info_file, info)
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

        try:
            self.db.history_update(url, name=new_name, target_dir=str(new_dir))
        except Exception as exc:
            try:
                if new_dir.exists() and not old_dir.exists():
                    old_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(new_dir), str(old_dir))
            except Exception:
                logger.exception("History-FS-Rollback für %s fehlgeschlagen", url)
            return {"ok": False, "error": f"History-Update fehlgeschlagen: {exc}"}
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
                indexed = (
                    self.db.recipe_get_by_folder(str(d))
                    or self.db.recipe_get_by_folder(str(d.resolve()))
                )
                if indexed:
                    from ..recipes.manage import safe_delete_recipe
                    safe_delete_recipe(
                        self.db, int(indexed["id"]), delete_files=True, hard=True,
                    )
                else:
                    roots = (self.recipe_dir.resolve(), self.wedding_dir.resolve())
                    resolved = d.resolve(strict=True)
                    if not any(
                        self._is_relative_to(resolved, root) for root in roots
                    ):
                        return {"ok": False, "error": "Zielpfad liegt außerhalb der Datenwurzeln"}
                    from ..core.safety import quarantine_move
                    trash_root = Path(get_config().get(
                        "safety", "trash_dir", default="/opt/scrapper/data/trash"
                    ))
                    payload = quarantine_move(
                        resolved, trash_root, reason="history_delete",
                        source={"url": url},
                    )
                    try:
                        self.db.history_delete(url)
                    except Exception:
                        if payload and payload.exists() and not resolved.exists():
                            resolved.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(payload), str(resolved))
                        raise
                    if payload and payload.parent.exists():
                        shutil.rmtree(payload.parent)
                    self._cleanup_empty_parents(d)
                    return {"ok": True, "action": "deleted"}
                self._cleanup_empty_parents(d)
        self.db.history_delete(url)
        return {"ok": True, "action": "deleted"}

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

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
    def _reanalyze_pending_attachment(self, url: str, entry: Dict,
                                      suggestion: Dict) -> Dict:
        """Analysiert ein zurückgehaltenes Bild/PDF erneut über die
        aktuelle Datei- und Rezeptpipeline.

        ``video_path`` enthält bei Dateiimporten aus historischen Gründen die
        Originaldatei. Sie darf nicht durch den alten Video-Save-Pfad laufen:
        sonst fehlen strukturierte Zutaten/Schritte und Bilder werden wie
        Videos behandelt.
        """
        source_path = Path(str(entry.get("video_path") or ""))
        try:
            if source_path.is_symlink():
                raise ValueError("symlink")
            resolved_path = source_path.resolve(strict=True)
            resolved_path.relative_to(self.temp_dir.resolve())
        except (OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "Importdatei fehlt oder liegt außerhalb des Importbereichs"}
        if not resolved_path.is_file():
            return {"ok": False, "error": "Importdatei fehlt oder ist nicht sicher lesbar"}

        ext = resolved_path.suffix.lower()
        if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
            return {"ok": False, "error": "Dieser Dateiimport kann nicht erneut per KI geprüft werden"}

        try:
            payload = resolved_path.read_bytes()
        except OSError as exc:
            logger.warning("Pending-Datei %s konnte nicht gelesen werden: %s", url, exc)
            return {"ok": False, "error": "Importdatei konnte nicht gelesen werden"}

        filename = str(suggestion.get("filename") or resolved_path.name)
        subject = Path(filename).stem.replace("_", " ")
        description = str(entry.get("description") or "").strip()
        image_mime = None
        if ext == ".pdf":
            description = self._extract_pdf_text(payload) or description or subject
        else:
            image_mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/png"
            if (
                self.analyzer
                and hasattr(self.analyzer, "extract_description_from_image_bytes")
            ):
                extracted = self.analyzer.extract_description_from_image_bytes(
                    payload,
                    image_mime,
                    subject,
                )
                if extracted:
                    description = extracted
            description = description or subject

        content_type = str(entry.get("content_type") or "recipe")
        if content_type != "recipe":
            analysis = self._analyze_wedding(description)
            next_suggestion = {
                **suggestion,
                "name": analysis.name,
                "category": analysis.category or "Sonstiges",
                "confidence": analysis.confidence,
                "analysis_state": "complete"
                if not analysis.needs_manual_input(self.confidence_threshold)
                else "incomplete",
            }
            self.db.pending_add(
                url=url,
                content_type=content_type,
                description=description[:5000],
                video_path=str(resolved_path),
                frame_path=entry.get("frame_path"),
                ai_suggestion=next_suggestion,
            )
            return {
                "ok": True,
                "action": "still_pending",
                "analysis": next_suggestion,
                "description": description[:5000],
                "message": "Die Datei wurde erneut ausgewertet.",
            }

        analysis = None
        if _has_usable_description(description, self.min_desc_len):
            analysis = self._analyze_recipe(description)
        elif image_mime:
            analysis = self._analyze_image_via_openai(
                payload,
                image_mime,
                "recipe",
                subject,
            )
        if not analysis:
            analysis = self._analyze_recipe(description)
        structured = self._extract_recipe_data(description)
        complete = self._recipe_data_complete(analysis, structured)
        next_suggestion = {
            **suggestion,
            "name": analysis.name,
            "type": analysis.type,
            "category": analysis.category or "Allgemein",
            "confidence": analysis.confidence,
            "analysis_state": "complete" if complete else "incomplete",
            "ingredients": structured.ingredients,
            "steps": structured.steps,
            "servings": structured.servings,
            "tags": structured.tags,
            "extraction_method": structured.method,
            "warnings": structured.warnings,
        }
        self.db.pending_add(
            url=url,
            content_type=content_type,
            description=description[:5000],
            video_path=str(resolved_path),
            frame_path=entry.get("frame_path"),
            ai_suggestion=next_suggestion,
        )

        if complete:
            saved = self.resolve_pending(url, {
                "action": "save",
                "name": analysis.name,
                "type": analysis.type,
                "category": analysis.category or "Allgemein",
                "description": description[:5000],
                "ingredients": structured.ingredients,
                "steps": structured.steps,
                "servings": structured.servings,
                "verified": False,
            })
            if not saved.get("ok"):
                return saved
            return {
                **saved,
                "action": "auto_saved",
                "analysis": next_suggestion,
                "description": description[:5000],
                "message": "Die KI-Prüfung ist vollständig; das Rezept wurde einsortiert.",
            }

        return {
            "ok": True,
            "action": "still_pending",
            "analysis": next_suggestion,
            "description": description[:5000],
            "message": "Der KI-Vorschlag wurde aktualisiert und bleibt zur manuellen Prüfung offen.",
        }

    def attach_pending_photo(
        self,
        url: str,
        data: bytes,
        suffix: str,
        filename: str,
    ) -> Dict:
        """Hängt ein Nutzerfoto sicher an einen bestehenden Prüfeintrag.

        Das Foto liegt in ``frame_path``: so kann es sowohl als Vision-Quelle
        als auch nach der Freigabe als Rezeptbild verwendet werden. Erst nach
        erfolgreichem Stash wird ein zuvor vorhandenes Cover entfernt.
        """
        entry = self.db.pending_get(url)
        if not entry or entry.get("status") != "pending":
            return {"ok": False, "error": "Offener Prüfeintrag nicht gefunden"}
        if (entry.get("content_type") or "recipe") != "recipe":
            return {"ok": False, "error": "Der Foto-Scan ist derzeit nur für Rezepte verfügbar"}

        old_frame_path = str(entry.get("frame_path") or "")
        frame_path = self._stash_external_thumbnail_for_pending(data, suffix, url)
        suggestion = {
            **(entry.get("ai_suggestion") or {}),
            "attached_photo": True,
            "attached_photo_filename": filename,
            "has_thumbnail": True,
            "photo_scan_state": "running",
        }
        try:
            self.db.pending_add(
                url=url,
                content_type=entry.get("content_type") or "recipe",
                description=entry.get("description"),
                video_path=entry.get("video_path"),
                frame_path=frame_path,
                ai_suggestion=suggestion,
            )
        except Exception:
            # Die DB zeigt weiterhin auf das alte Bild. Das neue Staging-Objekt
            # darf dann nicht als unreferenzierter Temp-Payload liegen bleiben.
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                new_path = Path(frame_path)
                new_path.resolve(strict=True).relative_to(self.temp_dir.resolve())
                new_path.unlink(missing_ok=True)
            raise
        if old_frame_path and old_frame_path != frame_path:
            old_path = Path(old_frame_path)
            try:
                if not old_path.is_symlink():
                    old_path.resolve(strict=True).relative_to(self.temp_dir.resolve())
                    old_path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                logger.warning("Altes Pending-Cover für %s wurde nicht entfernt", url)
        refreshed = self.db.pending_get(url) or {
            **entry,
            "frame_path": frame_path,
            "ai_suggestion": suggestion,
        }
        return self._reanalyze_pending_photo(url, refreshed, suggestion)

    def _reanalyze_pending_photo(self, url: str, entry: Dict, suggestion: Dict) -> Dict:
        """Liest ein angehängtes Foto per Vision und strukturiert das Rezept."""
        payload, suffix = self._read_pending_thumbnail(entry)
        if not payload:
            return {"ok": False, "error": "Das angehängte Foto ist nicht mehr verfügbar"}

        analyzer = self.analyzer
        extract_image = getattr(analyzer, "extract_description_from_image_bytes", None)
        if not callable(extract_image):
            next_suggestion = {
                **suggestion,
                "photo_scan_state": "unavailable",
                "photo_scan_error": "Bilderkennung ist nicht konfiguriert",
            }
            self.db.pending_update_suggestion(url, next_suggestion)
            return {
                "ok": False,
                "action": "still_pending",
                "analysis": next_suggestion,
                "error": "Bilderkennung ist nicht konfiguriert",
            }

        mime_type = "image/png" if suffix == ".png" else "image/jpeg"
        context = str(
            suggestion.get("attached_photo_filename")
            or suggestion.get("filename")
            or suggestion.get("name")
            or "Rezeptfoto"
        )
        try:
            extracted = str(extract_image(payload, mime_type, context) or "").strip()
        except Exception as exc:
            logger.warning("Foto-Scan für %s fehlgeschlagen: %s", url, exc)
            next_suggestion = {
                **suggestion,
                "photo_scan_state": "error",
                "photo_scan_error": str(exc)[:300],
            }
            self.db.pending_update_suggestion(url, next_suggestion)
            return {
                "ok": False,
                "action": "still_pending",
                "analysis": next_suggestion,
                "error": f"Bildscan fehlgeschlagen: {str(exc)[:200]}",
            }

        if not extracted:
            next_suggestion = {
                **suggestion,
                "photo_scan_state": "no_text",
                "photo_scan_error": "Auf dem Foto wurde kein Rezepttext erkannt",
            }
            self.db.pending_update_suggestion(url, next_suggestion)
            known_analysis = RecipeAnalysis(
                str(suggestion.get("name") or "Unbekannt"),
                str(suggestion.get("type") or "Sonstiges"),
                str(suggestion.get("category") or "Allgemein"),
                float(suggestion.get("confidence") or 0.0),
            )
            if self._recipe_name_found(known_analysis):
                known_analysis = self._prepare_recipe_analysis(
                    known_analysis,
                    complete=False,
                )
                saved = self.resolve_pending(url, {
                    "action": "save",
                    "name": known_analysis.name,
                    "type": known_analysis.type,
                    "category": known_analysis.category or "Allgemein",
                    "description": str(entry.get("description") or "")[:5000],
                    "ingredients": [],
                    "steps": [],
                    "servings": None,
                    "verified": False,
                })
                if not saved.get("ok"):
                    return saved
                return {
                    **saved,
                    "action": "auto_saved",
                    "analysis": next_suggestion,
                    "needs_manual_care": True,
                    "message": "Rezeptname war bereits erkannt; das reine Gerichtsfoto wurde als unvollständiges Rezept importiert.",
                }
            return {
                "ok": True,
                "action": "still_pending",
                "analysis": next_suggestion,
                "message": "Foto gespeichert, aber darauf wurde kein verwertbarer Rezepttext erkannt.",
            }

        analysis = self._analyze_recipe(extracted)
        structured = self._extract_recipe_data(extracted)
        complete = self._recipe_data_complete(analysis, structured)
        analysis = self._prepare_recipe_analysis(analysis, complete=complete)
        next_suggestion = {
            **suggestion,
            "name": analysis.name,
            "type": analysis.type,
            "category": analysis.category or "Allgemein",
            "confidence": analysis.confidence,
            "analysis_state": "complete" if complete else "incomplete",
            "ingredients": structured.ingredients,
            "steps": structured.steps,
            "servings": structured.servings,
            "tags": structured.tags,
            "extraction_method": structured.method,
            "warnings": structured.warnings,
            "photo_scan_state": "complete" if complete else "incomplete",
            "photo_scan_error": None,
        }
        self.db.pending_add(
            url=url,
            content_type="recipe",
            description=extracted[:5000],
            video_path=entry.get("video_path"),
            frame_path=entry.get("frame_path"),
            ai_suggestion=next_suggestion,
        )

        if self._recipe_name_found(analysis):
            saved = self.resolve_pending(url, {
                "action": "save",
                "name": analysis.name,
                "type": analysis.type,
                "category": analysis.category or "Allgemein",
                "description": extracted[:5000],
                "ingredients": structured.ingredients,
                "steps": structured.steps,
                "servings": structured.servings,
                "verified": False,
            })
            if not saved.get("ok"):
                return saved
            return {
                **saved,
                "action": "auto_saved",
                "analysis": next_suggestion,
                "description": extracted[:5000],
                "needs_manual_care": not complete,
                "message": (
                    "Foto erkannt; das vollständige Rezept wurde mit Bild einsortiert."
                    if complete
                    else "Rezeptname im Foto erkannt; das unvollständige Rezept wurde mit Bild importiert und zur manuellen Pflege markiert."
                ),
            }

        return {
            "ok": True,
            "action": "still_pending",
            "analysis": next_suggestion,
            "description": extracted[:5000],
            "message": "Foto erkannt; fehlende Angaben bleiben zur manuellen Ergänzung offen.",
        }

    def reanalyze_pending(self, url: str) -> Dict:
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}
        if entry.get("status") != "pending":
            existing = self.db.recipe_get_by_url(url)
            return {
                "ok": True,
                "action": "already_saved",
                "target": existing.get("folder_path") if existing else None,
                "recipe_id": int(existing["id"]) if existing else None,
                "message": "Dieser Eingang wurde bereits einsortiert.",
            }

        suggestion = entry.get("ai_suggestion") or {}
        if suggestion.get("attached_photo"):
            return self._reanalyze_pending_photo(url, entry, suggestion)
        # Link-only-Pending-Einträge aus älteren Releases besitzen noch keinen
        # ``source``-Marker. Sie dürfen nicht in den Legacy-Video-Pfad fallen:
        # dort ist absichtlich keine dauerhafte Video-Datei mehr vorhanden.
        from ..core.email_processor import is_content_url

        if suggestion.get("source") == "external-link" or is_content_url(url):
            if suggestion.get("source") != "external-link":
                suggestion = {
                    **suggestion,
                    "source": "external-link",
                    "platform": "TikTok" if "tiktok.com" in url.lower() else "Instagram",
                }
                self.db.pending_update_suggestion(url, suggestion)
            refreshed = self.process_url({
                "url": url,
                "type": entry.get("content_type") or "recipe",
                "reanalyze_existing": True,
            })
            status = refreshed.get("status")
            if status == "auto":
                return {
                    "ok": True,
                    "action": "auto_saved",
                    **refreshed,
                }
            if status == "already_processed":
                return {
                    "ok": True,
                    "action": "already_saved",
                    **refreshed,
                }
            if status == "pending":
                current = self.db.pending_get(url) or entry
                return {
                    "ok": True,
                    "action": "still_pending",
                    "analysis": current.get("ai_suggestion") or suggestion,
                    "description": current.get("description"),
                    "message": refreshed.get("message"),
                }
            return {
                "ok": False,
                "action": "still_pending",
                "error": refreshed.get("error") or "Social-Link konnte nicht erneut analysiert werden",
            }

        source = str(suggestion.get("source") or "")
        if source in {"mail-attachment", "manual-upload"}:
            return self._reanalyze_pending_attachment(url, entry, suggestion)

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
        recipe_id: Optional[int] = None
        manual_description = str(decision.get("description") or "").strip()
        description = manual_description or entry.get("description")
        suggestion = entry.get("ai_suggestion") or {}
        source = str(suggestion.get("source") or "")

        attached_photo = bool(suggestion.get("attached_photo"))
        if source == "external-link" or attached_photo:
            stored_source = "external-link" if source == "external-link" else "pending-photo"
            existing = self.db.recipe_get_by_url(url)
            if existing:
                self.db.pending_resolve(url, status="resolved")
                self._remove_pending_files(entry)
                return {
                    "ok": True,
                    "action": "already_saved",
                    "target": existing.get("folder_path"),
                    "recipe_id": int(existing["id"]),
                    "message": "Dieser Eingang wurde bereits einsortiert.",
                }
            name = str(decision.get("name") or suggestion.get("name") or "Unbekannt").strip()
            category = str(decision.get("category") or suggestion.get("category") or "Allgemein").strip()
            if entry["content_type"] == "recipe":
                recipe_type = str(decision.get("type") or suggestion.get("type") or "Sonstiges").strip()
                target = self.recipe_dir / _sanitize(recipe_type) / _sanitize(category) / _sanitize(name)
                if target.exists():
                    target = target.parent / f"{target.name}_{datetime.now():%Y%m%d_%H%M%S}"
                from ..core.safety import (
                    atomic_write_bytes,
                    atomic_write_json,
                    atomic_write_text,
                    write_manifest,
                )
                target.mkdir(parents=True, exist_ok=False)
                info = {
                    "url": url,
                    "name": name,
                    "type": recipe_type,
                    "category": category,
                    "confidence": 1.0,
                    "content_type": "recipe",
                    "source": stored_source,
                    "platform": suggestion.get("platform"),
                    "description": (description or "")[:5000],
                    "timestamp": datetime.now().isoformat(),
                    "is_manual": True,
                }
                thumb_filename = None
                frame_path = Path(str(entry.get("frame_path") or ""))
                if frame_path.is_file() and not frame_path.is_symlink():
                    try:
                        frame_path.resolve().relative_to(self.temp_dir.resolve())
                        suffix = self._safe_thumbnail_suffix(frame_path.suffix)
                        thumb_filename = f"{target.name}{suffix}"
                        atomic_write_bytes(target / thumb_filename, frame_path.read_bytes())
                    except (OSError, RuntimeError, ValueError):
                        logger.warning("Unsicheres oder fehlendes Social-Cover für %s", url)
                if description:
                    atomic_write_text(target / "description.txt", description)
                atomic_write_json(target / "info.json", info)
                try:
                    write_manifest(target, source={"kind": stored_source, "url": url})
                except Exception:
                    pass
                recipe_id = self.db.recipe_upsert(
                    url=url,
                    name=name,
                    type=recipe_type,
                    category=category,
                    folder_path=str(target),
                    description=description,
                    thumb_filename=thumb_filename,
                    video_filename=None,
                    source_added_at=time.time(),
                )
                self._apply_pending_manual_data(recipe_id, decision)
                self.db.history_add(url, content_type="recipe", name=name, target_dir=str(target))
                self._queue_recipe_image(recipe_id)
            else:
                target = self.wedding_dir / _sanitize(category or "Sonstiges") / _sanitize(name)
                if target.exists():
                    target = target.parent / f"{target.name}_{datetime.now():%Y%m%d_%H%M%S}"
                from ..core.safety import atomic_write_json, atomic_write_text, write_manifest
                target.mkdir(parents=True, exist_ok=False)
                info = {
                    "url": url,
                    "name": name,
                    "wedding_category": category,
                    "confidence": 1.0,
                    "content_type": "wedding",
                    "source": stored_source,
                    "platform": suggestion.get("platform"),
                    "description": (description or "")[:5000],
                    "timestamp": datetime.now().isoformat(),
                    "is_manual": True,
                }
                if description:
                    atomic_write_text(target / "description.txt", description)
                atomic_write_json(target / "info.json", info)
                try:
                    write_manifest(target, source={"kind": stored_source, "url": url})
                except Exception:
                    pass
                self.db.history_add(url, content_type="wedding", name=name, target_dir=str(target))
            self.db.pending_resolve(url, status="resolved")
            self._remove_pending_files(entry)
            return {
                "ok": True,
                "action": "saved",
                "target": str(target),
                "recipe_id": recipe_id if entry["content_type"] == "recipe" else None,
            }

        if not video_path or not video_path.exists():
            return {"ok": False, "error": "Importdatei fehlt (vermutlich aufgeräumt)"}

        ext = video_path.suffix.lower()
        is_attachment = source in {"mail-attachment", "manual-upload"} and ext in {
            ".pdf", ".jpg", ".jpeg", ".png",
        }

        if is_attachment:
            name = str(decision.get("name") or suggestion.get("name") or "Unbekannt").strip()
            category = str(decision.get("category") or suggestion.get("category") or "Allgemein").strip()
            if entry["content_type"] == "recipe":
                recipe_type = str(decision.get("type") or suggestion.get("type") or "Sonstiges").strip()
                target = self.recipe_dir / _sanitize(recipe_type) / _sanitize(category) / _sanitize(name)
                if target.exists():
                    target = target.parent / f"{target.name}_{datetime.now():%Y%m%d_%H%M%S}"
                info = {
                    "url": url, "name": name, "type": recipe_type,
                    "category": category, "confidence": 1.0,
                    "content_type": "recipe", "source": source,
                    "filename": suggestion.get("filename") or video_path.name,
                    "description": (description or "")[:5000],
                    "timestamp": datetime.now().isoformat(),
                }
                payload = video_path.read_bytes()
                self._save_attachment_file(target, payload, ext, info, description)
                thumb_name = f"{target.name}{ext}" if ext in {".jpg", ".jpeg", ".png"} else None
                recipe_id = self.db.recipe_upsert(
                    url=url, name=name, type=recipe_type, category=category,
                    folder_path=str(target), description=description,
                    thumb_filename=thumb_name, video_filename=None,
                    source_added_at=time.time(),
                )
                if ext == ".pdf":
                    structured = self._extract_pdf_recipe_data(description or "")
                    apply_extracted_recipe_data(
                        self.db, recipe_id, structured, actor="manual-import",
                        overwrite=False, create_version=False, update_description=True,
                    )
                self._apply_pending_manual_data(recipe_id, decision)
                self.db.history_add(url, content_type="recipe", name=name, target_dir=str(target))
                self._queue_recipe_image(recipe_id)
            else:
                target = self.wedding_dir / _sanitize(category or "Sonstiges") / _sanitize(name)
                if target.exists():
                    target = target.parent / f"{target.name}_{datetime.now():%Y%m%d_%H%M%S}"
                info = {
                    "url": url, "name": name, "wedding_category": category,
                    "confidence": 1.0, "content_type": "wedding", "source": source,
                    "filename": suggestion.get("filename") or video_path.name,
                    "description": (description or "")[:5000],
                    "timestamp": datetime.now().isoformat(),
                }
                self._save_attachment_file(target, video_path.read_bytes(), ext, info, description)
                self.db.history_add(url, content_type="wedding", name=name, target_dir=str(target))
            self.db.pending_resolve(url, status="resolved")
            self._remove_pending_files(entry)
            return {
                "ok": True,
                "action": "saved",
                "target": str(target),
                "recipe_id": recipe_id if entry["content_type"] == "recipe" else None,
            }

        if entry["content_type"] == "recipe":
            r = RecipeAnalysis(
                name=decision.get("name", "Unbekannt"),
                type=decision.get("type", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_recipe(r, url, video_path, description)
            from ..recipes.indexer import _index_one
            _index_one(self.db, target, target.parent.parent.name, target.parent.name)
            recipe = self.db.recipe_get_by_folder(str(target))
            recipe_id = int(recipe["id"]) if recipe else None
            if recipe_id is not None:
                self._apply_pending_manual_data(recipe_id, decision)
            self.db.history_add(url, content_type="recipe", name=r.name, target_dir=str(target))
            self._queue_recipe_image(recipe_id)
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
        return {
            "ok": True,
            "action": "saved",
            "target": str(target),
            "recipe_id": recipe_id if entry["content_type"] == "recipe" else None,
        }

    def _apply_pending_manual_data(self, recipe_id: int, decision: Dict) -> None:
        """Übernimmt Korrekturen aus der manuellen Importprüfung direkt in die DB."""
        ingredients = decision.get("ingredients")
        prepared = prepare_recipe_ingredients(ingredients or [])
        if prepared:
            self.db.recipe_set_extraction_result(
                recipe_id, status="ok", ingredients=prepared,
            )
            try:
                refresh_diet_auto_tags(
                    self.db,
                    recipe_id,
                    [item["canonical_name"] for item in prepared],
                )
            except Exception as exc:
                logger.warning(
                    "Rezept #%s: diet-tag-recompute failed: %s",
                    recipe_id,
                    exc,
                )

        steps = decision.get("steps")
        prepared_steps = [
            step for step in (steps or [])
            if str((step or {}).get("instruction") or "").strip()
        ]
        if prepared_steps:
            self.db.recipe_steps_set(recipe_id, prepared_steps)
        if decision.get("servings") is not None:
            self.db.recipe_set_servings(recipe_id, decision.get("servings"))
        if decision.get("verified"):
            self.db.recipe_set_verified(recipe_id, True, "manual-import")

    def _remove_pending_files(self, entry: Dict) -> None:
        paths = {entry.get("video_path"), entry.get("frame_path")}
        for p in paths:
            if not p:
                continue
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
