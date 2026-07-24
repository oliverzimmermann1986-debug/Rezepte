"""yt-dlp Wrapper.

Frame-Extraktion und Vision-Analyse wurden entfernt - der KI-Cascade
besteht jetzt nur noch aus Ollama (fast) + Ollama (fallback). Pending-Items
werden im Web-UI über ein <video>-Element angezeigt, kein Standbild nötig.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, ytdlp_path: str, temp_dir: Path, cookies_file: Optional[str] = None):
        self.ytdlp_path = ytdlp_path
        self.temp_dir = temp_dir
        # Optionaler Cookie-Jar (Netscape-Format, exportiert via Browser-
        # Extension). Erlaubt yt-dlp Zugriff auf private/eingeloggte Inhalte.
        self.cookies_file = cookies_file if cookies_file and Path(cookies_file).exists() else None
        if cookies_file and not self.cookies_file:
            logger.warning(f"Cookie-Datei konfiguriert aber nicht gefunden: {cookies_file}")

    def download(self, url: str) -> Optional[Path]:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        sub = self.temp_dir / uuid.uuid4().hex[:8]
        sub.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ytdlp_path, url,
            "-o", str(sub / "video.%(ext)s"),
            "--no-playlist", "--quiet", "--no-warnings",
            "--write-description",
            # Cover direkt mitladen — sonst startet jedes Rezept ohne Thumbnail
            # und landet im Audit unter "Kein Bild" (nur Re-Scrape holte es bisher).
            "--write-thumbnail", "--convert-thumbnails", "jpg",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                logger.error(f"yt-dlp Fehler: {result.stderr.strip()}")
                shutil.rmtree(sub, ignore_errors=True)
                return None
            videos = (
                list(sub.glob("video.mp4"))
                or list(sub.glob("video.webm"))
                or list(sub.glob("video.mkv"))
                or list(sub.glob("video.*"))
            )
            videos = [v for v in videos if v.suffix.lower() not in (".description", ".part")]
            if not videos:
                logger.warning(f"yt-dlp: kein Video heruntergeladen für {url}")
                shutil.rmtree(sub, ignore_errors=True)
                return None
            return videos[0]
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp Timeout")
            shutil.rmtree(sub, ignore_errors=True)
            return None
        except Exception as e:
            logger.error(f"yt-dlp Exception: {e}")
            shutil.rmtree(sub, ignore_errors=True)
            return None

    @staticmethod
    def read_description(video_path: Path) -> Optional[str]:
        desc = video_path.with_suffix(".description")
        if not desc.exists():
            candidates = list(video_path.parent.glob("*.description"))
            if not candidates:
                return None
            desc = candidates[0]
        try:
            text = desc.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            return None

    def refresh_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Re-Scrape: holt Beschreibung + Thumbnail ohne Video-Download.
        Returnt Beschreibung und optional begrenzte Thumbnail-Bytes. Der
        temporäre yt-dlp-Ordner wird vor dem Return immer entfernt.

        Schneller als download() weil nur Metadata gepulled werden (~2-5s
        statt ~30s für Video-DL). Geeignet für 'frische Caption + Bild'-
        Refresh ohne den Folder zu invalidieren."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        sub = self.temp_dir / uuid.uuid4().hex[:8]
        sub.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ytdlp_path, url,
            "-o", str(sub / "thumb.%(ext)s"),
            "--no-playlist", "--quiet", "--no-warnings",
            "--skip-download",
            "--write-description",
            "--write-thumbnail",
            "--convert-thumbnails", "jpg",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning(f"yt-dlp refresh_metadata fehler: {result.stderr.strip()[:300]}")
                shutil.rmtree(sub, ignore_errors=True)
                return None
        except subprocess.TimeoutExpired:
            logger.warning(f"yt-dlp refresh_metadata Timeout für {url}")
            shutil.rmtree(sub, ignore_errors=True)
            return None
        except Exception as e:
            logger.warning(f"yt-dlp refresh_metadata Exception: {e}")
            shutil.rmtree(sub, ignore_errors=True)
            return None

        # description-File suchen (yt-dlp schreibt thumb.description trotz -o)
        desc_files = list(sub.glob("*.description"))
        thumb_files = list(sub.glob("*.jpg")) + list(sub.glob("*.jpeg")) + list(sub.glob("*.webp")) + list(sub.glob("*.png"))
        if not desc_files and not thumb_files:
            shutil.rmtree(sub, ignore_errors=True)
            return None

        out: Dict[str, Any] = {}
        try:
            if desc_files:
                txt = desc_files[0].read_text(encoding="utf-8").strip()
                if txt:
                    out["description_text"] = txt[:200_000]
            if thumb_files:
                thumb = thumb_files[0]
                size = thumb.stat().st_size
                if size <= 10 * 1024 * 1024:
                    out["thumbnail_bytes"] = thumb.read_bytes()
                    out["thumbnail_suffix"] = thumb.suffix.lower()
                else:
                    logger.warning(
                        "yt-dlp Thumbnail zu groß (%s Bytes), wird verworfen",
                        size,
                    )
            return out or None
        except Exception as e:
            logger.warning(f"Metadata read fehler: {e}")
            return None
        finally:
            shutil.rmtree(sub, ignore_errors=True)
