"""
Scraper-Job (TikTok/Instagram → Rezepte/Hochzeit Ordner).

Neu vs. altem Script:
  - 2 separate IMAP-Konten (kein Betreff-Klassifizierung)
  - Pending wird NICHT mehr per Telegram-Reply aufgelöst, sondern im Web-UI
  - State liegt in SQLite (nicht mehr JSON-Files)
  - Wird als Funktion gestartet (synchron) - Aufrufer (FastAPI/Timer) startet Thread
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config_store import get_config
from ..db import get_db
from ..core.analyzer import (
    OllamaAnalyzer, OpenAIVisionAnalyzer,
    RecipeAnalysis, WeddingAnalysis,
)
from ..core.downloader import VideoDownloader, FrameExtractor
from ..core.email_processor import MailAccount, EmailRouter
from ..core.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


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
                       frame_path: Optional[Path], description: Optional[str],
                       info: Dict) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    file_base = target_dir.name
    if video_path and video_path.exists():
        shutil.copy2(video_path, target_dir / f"{file_base}{video_path.suffix}")
    if frame_path and frame_path.exists():
        shutil.copy2(frame_path, target_dir / f"{file_base}.jpg")
    if description:
        (target_dir / "description.txt").write_text(description, encoding="utf-8")
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

        # AI
        ollama_cfg = cfg.get("ai", "ollama", default={}) or {}
        self.ollama_enabled = bool(ollama_cfg.get("enabled", True))
        url = ollama_cfg.get("url", "http://localhost:11434")
        timeout = int(ollama_cfg.get("timeout", 60))
        self.ollama = OllamaAnalyzer(
            url, ollama_cfg.get("model", "qwen2.5:7b-instruct"), timeout
        ) if self.ollama_enabled else None
        fb = ollama_cfg.get("fallback_model", "").strip() if ollama_cfg.get("fallback_model") else ""
        self.ollama_fallback = (
            OllamaAnalyzer(url, fb, timeout) if (self.ollama_enabled and fb) else None
        )

        openai_cfg = cfg.get("ai", "openai", default={}) or {}
        self.vision = None
        if openai_cfg.get("enabled") and openai_cfg.get("api_key"):
            try:
                self.vision = OpenAIVisionAnalyzer(
                    openai_cfg["api_key"],
                    openai_cfg.get("model", "gpt-4o-mini"),
                )
            except Exception as e:
                logger.warning(f"OpenAI init: {e}")

        self.confidence_threshold = float(cfg.get("ai", "confidence_threshold", default=0.75))
        self.fallback_threshold = float(cfg.get("ai", "fallback_threshold", default=0.5))
        self.min_desc_len = int(cfg.get("ai", "description_min_length", default=20))

        # Downloader
        self.downloader = VideoDownloader(
            cfg.get("ytdlp", "binary", default="/opt/scrapper/venv/bin/yt-dlp"),
            self.temp_dir,
        )

        # Telegram
        tg = cfg.get("telegram", default={}) or {}
        self.tg_enabled = bool(tg.get("enabled", True))
        self.recipe_bot = TelegramNotifier(
            tg.get("recipe_bot_token", ""), tg.get("recipe_chat_id", ""), label="recipe",
        )
        self.wedding_bot = TelegramNotifier(
            tg.get("wedding_bot_token", "") or tg.get("recipe_bot_token", ""),
            tg.get("wedding_chat_id", "") or tg.get("recipe_chat_id", ""),
            label="wedding",
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

        # Wedding-Spezial: alle Items zwingend in Pending?
        self.wedding_always_pending = bool(
            (mail_cfg.get("wedding") or {}).get("always_pending", False)
        )

        # Kategorien
        self.wedding_categories = cfg.get(
            "wedding_categories",
            default=["Deko", "Foto", "Basteln", "Einladung", "Standesamt", "Sonstiges"],
        )

    # ---------------- Analyse ----------------
    def _analyze_recipe(self, description: Optional[str], video: Path
                         ) -> Tuple[RecipeAnalysis, Optional[Path]]:
        # Cascade: fast Modell → fallback Modell → Vision
        best = None
        if self.ollama and _has_usable_description(description, self.min_desc_len):
            r = self.ollama.analyze_recipe(description)
            logger.info(f"Ollama fast: name={r.name} typ={r.type} conf={r.confidence:.2f}")
            if not r.needs_manual_input(self.confidence_threshold):
                return r, None
            best = r
            # fast unsicher → fallback-Modell
            if self.ollama_fallback:
                r2 = self.ollama_fallback.analyze_recipe(description)
                logger.info(f"Ollama fallback: name={r2.name} typ={r2.type} conf={r2.confidence:.2f}")
                if not r2.needs_manual_input(self.fallback_threshold):
                    return r2, None
                if r2.confidence > (best.confidence if best else 0):
                    best = r2
        # Vision-Fallback wenn beide Ollama-Versuche unsicher
        if self.vision:
            frame = FrameExtractor.extract(video)
            if frame:
                v = self.vision.analyze_recipe(frame)
                logger.info(f"Vision: name={v.name} conf={v.confidence:.2f}")
                if best and best.confidence > v.confidence:
                    return best, frame
                return v, frame
        if best:
            return best, None
        return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0), None

    def _analyze_wedding(self, description: Optional[str], video: Path
                          ) -> Tuple[WeddingAnalysis, Optional[Path]]:
        best = None
        # Wenn always_pending konfiguriert ist: KI nur für Vorschlag, aber confidence cap auf 0
        force_pending = self.wedding_always_pending
        if self.ollama and _has_usable_description(description, self.min_desc_len):
            w = self.ollama.analyze_wedding(description, self.wedding_categories)
            logger.info(f"Ollama fast (wedding): name={w.name} cat={w.category} conf={w.confidence:.2f}")
            if force_pending:
                # Vorschlag behalten, aber als unsicher markieren damit es in Pending landet
                w_keep = WeddingAnalysis(name=w.name, category=w.category, confidence=min(w.confidence, 0.49))
                return w_keep, None
            if not w.needs_manual_input(self.confidence_threshold):
                return w, None
            best = w
            if self.ollama_fallback:
                w2 = self.ollama_fallback.analyze_wedding(description, self.wedding_categories)
                logger.info(f"Ollama fallback (wedding): name={w2.name} cat={w2.category} conf={w2.confidence:.2f}")
                if not w2.needs_manual_input(self.fallback_threshold):
                    return w2, None
                if w2.confidence > (best.confidence if best else 0):
                    best = w2
        if self.vision:
            frame = FrameExtractor.extract(video)
            if frame:
                v = self.vision.analyze_wedding(frame, self.wedding_categories)
                if best and best.confidence > v.confidence:
                    return best, frame
                return v, frame
        if best:
            return best, None
        return WeddingAnalysis("Unbekannt", None, 0.0), None

    # ---------------- Persistierung ----------------
    def _save_recipe(self, r: RecipeAnalysis, url: str, video: Path,
                     frame: Optional[Path], description: Optional[str]) -> Path:
        type_n = _sanitize(r.type)
        cat_n = _sanitize(r.category or "Sonstiges")
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
        _save_video_files(target, video, frame, description, info)
        return target

    def _save_wedding(self, w: WeddingAnalysis, url: str, video: Path,
                      frame: Optional[Path], description: Optional[str],
                      default_cat: str = "Sonstiges") -> Path:
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
        _save_video_files(target, video, frame, description, info)
        return target

    # ---------------- URL-Verarbeitung ----------------
    def process_url(self, item: Dict) -> Dict:
        """Verarbeitet eine URL. Return: {'status': 'auto'|'pending'|'error', ...}"""
        url = item["url"]
        content_type = item["type"]
        result: Dict = {"url": url, "type": content_type, "status": "error"}

        video = self.downloader.download(url)
        if not video:
            result["error"] = "download failed"
            return result
        description = self.downloader.read_description(video)

        try:
            if content_type == "recipe":
                r, frame = self._analyze_recipe(description, video)
                if r.needs_manual_input(self.confidence_threshold):
                    # → Pending
                    pending_video = self._stash_for_pending(video, frame)
                    self.db.pending_add(
                        url=url, content_type="recipe",
                        description=description,
                        video_path=str(pending_video["video"]) if pending_video.get("video") else None,
                        frame_path=str(pending_video["frame"]) if pending_video.get("frame") else None,
                        ai_suggestion={
                            "name": r.name, "type": r.type,
                            "category": r.category, "confidence": r.confidence,
                        },
                    )
                    result.update({"status": "pending", "name": r.name})
                    if self.recipe_bot.enabled:
                        self.recipe_bot.send(
                            f"❓ Unklar – im Web zuordnen\n"
                            f"<b>{r.name}</b>\n"
                            f"{r.type} / {r.category} ({r.confidence:.0%})\n"
                            f"🔗 {url}"
                        )
                else:
                    target = self._save_recipe(r, url, video, frame, description)
                    self.db.history_add(url, content_type="recipe", name=r.name,
                                         target_dir=str(target))
                    result.update({"status": "auto", "name": r.name, "target": str(target)})
                    if self.recipe_bot.enabled:
                        self.recipe_bot.send(
                            f"✅ Rezept\n<b>{r.name}</b>\n"
                            f"{r.type} / {r.category or 'N/A'} ({r.confidence:.0%})"
                        )
            else:  # wedding
                default_cat = item.get("default_category") or "Sonstiges"
                w, frame = self._analyze_wedding(description, video)
                if w.needs_manual_input(self.confidence_threshold):
                    pending_video = self._stash_for_pending(video, frame)
                    self.db.pending_add(
                        url=url, content_type="wedding",
                        description=description,
                        video_path=str(pending_video["video"]) if pending_video.get("video") else None,
                        frame_path=str(pending_video["frame"]) if pending_video.get("frame") else None,
                        ai_suggestion={
                            "name": w.name, "category": w.category or default_cat,
                            "confidence": w.confidence,
                        },
                    )
                    result.update({"status": "pending", "name": w.name})
                    if self.wedding_bot.enabled:
                        self.wedding_bot.send(
                            f"❓ Hochzeit unklar – im Web zuordnen\n"
                            f"<b>{w.name}</b>\n"
                            f"Kategorie: {w.category or default_cat} ({w.confidence:.0%})\n"
                            f"🔗 {url}"
                        )
                else:
                    target = self._save_wedding(w, url, video, frame, description, default_cat)
                    self.db.history_add(url, content_type="wedding", name=w.name,
                                         target_dir=str(target))
                    result.update({"status": "auto", "name": w.name, "target": str(target)})
                    if self.wedding_bot.enabled:
                        self.wedding_bot.send(
                            f"💒 Hochzeit\n<b>{w.name}</b>\n"
                            f"{w.category or default_cat} ({w.confidence:.0%})"
                        )
        finally:
            # Temp aufräumen, außer Pending hat Files übernommen
            self._cleanup_temp(video)

        return result

    def _stash_for_pending(self, video: Path, frame: Optional[Path]) -> Dict[str, Path]:
        """Kopiert video/frame in temp_dir/pending/ damit sie das Cleanup überleben."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        pending_root = self.temp_dir / "pending"
        pending_root.mkdir(parents=True, exist_ok=True)
        out: Dict[str, Path] = {}
        if video and video.exists():
            dst = pending_root / f"{ts}_video{video.suffix}"
            shutil.copy2(video, dst)
            out["video"] = dst
        if frame and frame.exists():
            dst = pending_root / f"{ts}_frame.jpg"
            shutil.copy2(frame, dst)
            out["frame"] = dst
        return out

    def _cleanup_temp(self, video: Path) -> None:
        # Lösche nur den Download-Subordner, nicht das ganze temp_dir
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
            "fetched": 0, "new": 0, "auto": 0, "pending": 0, "errors": 0,
            "recipe_auto": 0, "recipe_pending": 0,
            "wedding_auto": 0, "wedding_pending": 0,
        }

        items = self.router.fetch_all()
        summary["fetched"] = len(items)

        # Pending-URLs überspringen (warten auf Web-Resolve)
        pending_urls = {p["url"] for p in self.db.pending_list("pending")}

        new_items = [
            it for it in items
            if not self.db.history_has(it["url"]) and it["url"] not in pending_urls
        ]
        summary["new"] = len(new_items)
        logger.info(f"Neue URLs: {len(new_items)}")

        for item in new_items:
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
                logger.exception(f"URL fehlgeschlagen {item['url']}: {e}")
                summary["errors"] += 1

        summary["duration_sec"] = round(time.time() - start, 1)
        summary["total_pending"] = self.db.pending_count()
        logger.info(f"Job-Summary: {summary}")
        return summary

    # ---------------- History bearbeiten ----------------
    def move_history_item(self, url: str, *, new_name: str, new_type: str = None,
                            new_category: str = None) -> Dict:
        """Verschiebt/Umbenennt einen schon einsortierten Eintrag im FS und updated DB.
        Alter Parent-Ordner wird gelöscht falls leer.
        """
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

        # Falls Ziel gleich Quelle, nichts zu tun
        if new_dir.resolve() == old_dir.resolve():
            return {"ok": True, "action": "noop", "target": str(new_dir)}

        # Falls Ziel existiert -> Suffix
        if new_dir.exists():
            from datetime import datetime as _dt
            new_dir = new_dir.parent / f"{sanitized_name}_{_dt.now():%Y%m%d_%H%M%S}"

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

        # Datei-Basisnamen umbenennen damit zum Ordner passt
        for ext in (".mp4", ".webm", ".mkv", ".jpg"):
            for f in new_dir.glob(f"*{ext}"):
                # Nur die Hauptmedia rename, nicht z.B. preview_*.jpg
                if f.stem != sanitized_name:
                    target = new_dir / f"{sanitized_name}{ext}"
                    if not target.exists():
                        try:
                            f.rename(target)
                        except Exception as e:
                            logger.warning(f"rename {f}: {e}")

        # DB updaten
        self.db.history_update(url, name=new_name, target_dir=str(new_dir))

        # Leere Parent-Ordner aufräumen
        self._cleanup_empty_parents(old_dir)

        return {"ok": True, "action": "moved", "target": str(new_dir)}

    def delete_history_item(self, url: str) -> Dict:
        """Löscht den Eintrag aus FS und Historie."""
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
        """Steigt von removed_dir hoch und löscht leere Verzeichnisse, bis zur Wurzel."""
        # Begrenze auf recipe_dir/wedding_dir um nicht zu hoch zu steigen
        parent = removed_dir.parent
        for _ in range(4):  # max 4 Ebenen hoch
            try:
                if not parent.exists():
                    break
                # Nur unterhalb der bekannten Roots aufräumen
                rels = [self.recipe_dir, self.wedding_dir]
                inside = any(str(parent).startswith(str(r)) and str(parent) != str(r) for r in rels)
                if not inside:
                    break
                # Versuch zu löschen wenn leer
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
        """Lässt ein Pending-Item nochmal durch die Cascade laufen.
        Bei Erfolg: automatisch einsortieren. Sonst: nur ai_suggestion updaten."""
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        description = entry.get("description")
        video_path = Path(entry["video_path"]) if entry.get("video_path") else None
        if not video_path or not video_path.exists():
            return {"ok": False, "error": "Video-Datei fehlt (vermutlich aufgeräumt)"}

        frame_path = Path(entry["frame_path"]) if entry.get("frame_path") else None
        content_type = entry.get("content_type") or "recipe"

        if content_type == "recipe":
            r, new_frame = self._analyze_recipe(description, video_path)
            frame_to_use = new_frame or frame_path
            suggestion = {
                "name": r.name, "type": r.type,
                "category": r.category, "confidence": r.confidence,
            }
            if not r.needs_manual_input(self.confidence_threshold):
                # Direkt einsortieren
                target = self._save_recipe(r, url, video_path, frame_to_use, description)
                self.db.history_add(url, content_type="recipe", name=r.name, target_dir=str(target))
                self.db.pending_resolve(url, status="resolved")
                self._remove_pending_files(entry)
                if self.recipe_bot.enabled:
                    self.recipe_bot.send(
                        f"✅ Rezept (KI-Reanalyse)\n<b>{r.name}</b>\n"
                        f"{r.type} / {r.category or 'N/A'} ({r.confidence:.0%})"
                    )
                return {"ok": True, "action": "auto_saved", "target": str(target),
                        "analysis": suggestion}
            # Suggestion aktualisieren
            self.db.pending_update_suggestion(url, suggestion)
            return {"ok": True, "action": "still_pending", "analysis": suggestion}
        else:  # wedding
            w, new_frame = self._analyze_wedding(description, video_path)
            frame_to_use = new_frame or frame_path
            default_cat = "Sonstiges"
            suggestion = {
                "name": w.name, "category": w.category or default_cat,
                "confidence": w.confidence,
            }
            if not w.needs_manual_input(self.confidence_threshold):
                target = self._save_wedding(w, url, video_path, frame_to_use, description, default_cat)
                self.db.history_add(url, content_type="wedding", name=w.name, target_dir=str(target))
                self.db.pending_resolve(url, status="resolved")
                self._remove_pending_files(entry)
                if self.wedding_bot.enabled:
                    self.wedding_bot.send(
                        f"💒 Hochzeit (KI-Reanalyse)\n<b>{w.name}</b>\n"
                        f"{w.category or default_cat} ({w.confidence:.0%})"
                    )
                return {"ok": True, "action": "auto_saved", "target": str(target),
                        "analysis": suggestion}
            self.db.pending_update_suggestion(url, suggestion)
            return {"ok": True, "action": "still_pending", "analysis": suggestion}

    def resolve_pending(self, url: str, decision: Dict) -> Dict:
        """
        decision = {
          'action': 'save' | 'skip',
          'name': str, 'type'/'category': str, ...
        }
        """
        entry = self.db.pending_get(url)
        if not entry:
            return {"ok": False, "error": "Pending-Eintrag nicht gefunden"}

        if decision.get("action") == "skip":
            self.db.pending_resolve(url, status="skipped")
            self.db.history_add(url, content_type=entry["content_type"], name="(skipped)")
            self._remove_pending_files(entry)
            return {"ok": True, "action": "skipped"}

        video_path = Path(entry["video_path"]) if entry.get("video_path") else None
        frame_path = Path(entry["frame_path"]) if entry.get("frame_path") else None
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
            target = self._save_recipe(r, url, video_path, frame_path, description)
            self.db.history_add(url, content_type="recipe", name=r.name, target_dir=str(target))
            if self.recipe_bot.enabled:
                self.recipe_bot.send(
                    f"✅ Rezept manuell zugeordnet\n<b>{r.name}</b>\n"
                    f"{r.type} / {r.category or 'N/A'}"
                )
        else:
            w = WeddingAnalysis(
                name=decision.get("name", "Unbekannt"),
                category=decision.get("category"),
                confidence=1.0,
                is_manual=True,
            )
            target = self._save_wedding(w, url, video_path, frame_path, description,
                                          default_cat="Sonstiges")
            self.db.history_add(url, content_type="wedding", name=w.name, target_dir=str(target))
            if self.wedding_bot.enabled:
                self.wedding_bot.send(
                    f"💒 Hochzeit manuell zugeordnet\n<b>{w.name}</b>\n"
                    f"{w.category or 'Sonstiges'}"
                )

        self.db.pending_resolve(url, status="resolved")
        self._remove_pending_files(entry)
        return {"ok": True, "action": "saved", "target": str(target)}

    def _remove_pending_files(self, entry: Dict) -> None:
        for key in ("video_path", "frame_path"):
            p = entry.get(key)
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass


def run_job() -> Dict:
    """Entry-Point für systemd oder Web-Trigger."""
    return ScraperJob().run()
