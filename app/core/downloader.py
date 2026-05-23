"""yt-dlp Wrapper.

Frame-Extraktion und Vision-Analyse wurden entfernt - der KI-Cascade
besteht jetzt nur noch aus Ollama (fast) + Ollama (fallback). Pending-Items
werden im Web-UI über ein <video>-Element angezeigt, kein Standbild nötig.
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

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
                return None
            return videos[0]
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp Timeout")
            return None
        except Exception as e:
            logger.error(f"yt-dlp Exception: {e}")
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
