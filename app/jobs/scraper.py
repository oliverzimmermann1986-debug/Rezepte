"""
Scraper-Job (TikTok/Instagram -> Rezepte/Hochzeit Ordner).

Vereinfachte KI-Cascade (kein Vision-Fallback mehr):
  Ollama-fast -> Ollama-fallback -> Pending (manuell im Web-UI)

Pending-Items werden im Web-UI über ein <video>-Element angezeigt -
keine Standbild-Extraktion mehr nötig.
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
from ..core.analyzer import OllamaAnalyzer, RecipeAnalysis, WeddingAnalysis, build_analyzer
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
                       description: Optional[str], info: Dict) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    file_base = target_dir.name
    if video_path and video_path.exists():
        shutil.copy2(video_path, target_dir / f"{file_base}{video_path.suffix}")
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
        target = self.recipe_dir / type_n / cat_n / name_n
        if target.exists():
            target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
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
        target = self.wedding_dir / cat / name_n
        if target.exists():
            target = target.parent / f"{name_n}_{datetime.now():%Y%m%d_%H%M%S}"
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

    def _stash_for_pending(self, video: Path) -> Optional[Path]:
        """Kopiert das Video nach temp_dir/pending/ damit es das Cleanup überlebt."""
        if not video or not video.exists():
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        pending_root = self.temp_dir / "pending"
        pending_root.mkdir(parents=True, exist_ok=True)
        dst = pending_root / f"{ts}_video{video.suffix}"
        shutil.copy2(video, dst)
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

        # Ollama-Health-Check vor dem Loop. Wenn Ollama tot ist landen sonst
        # ALLE URLs in Pending (weil analyze_* leer zurückkommt) - das wollen
        # wir verhindern und stattdessen den Job sofort als 'error' beenden,
        # damit keine 50 Pending-Items entstehen und keine Videos sinnlos
        # gedownloaded werden.
        if self.ollama_enabled and self.ollama and not self.ollama.health():
            msg = (f"Ollama nicht erreichbar oder Modell '{self.ollama.model}' fehlt - "
                   f"Job abgebrochen damit nicht alle URLs in Pending landen")
            logger.error(msg)
            summary["error"] = msg
            summary["duration_sec"] = round(time.time() - start, 1)
            raise RuntimeError(msg)

        items = self.router.fetch_all()
        summary["fetched"] = len(items)

        pending_urls = {p["url"] for p in self.db.pending_list("pending")}
        new_items = [
            it for it in items
            if not self.db.history_has(it["url"]) and it["url"] not in pending_urls
        ]
        summary["new"] = len(new_items)
        logger.info(f"Neue URLs: {len(new_items)}")

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

    # ---------------- History bearbeiten ----------------
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
