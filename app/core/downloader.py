"""yt-dlp Wrapper + ffmpeg Frame-Extraktion."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, ytdlp_path: str, temp_dir: Path):
        self.ytdlp_path = ytdlp_path
        self.temp_dir = temp_dir

    def download(self, url: str) -> Optional[Path]:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # Eindeutigen Subordner pro Download
        import uuid
        sub = self.temp_dir / uuid.uuid4().hex[:8]
        sub.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [
                    self.ytdlp_path, url,
                    "-o", str(sub / "video.%(ext)s"),
                    "--no-playlist", "--quiet", "--no-warnings",
                    "--write-description",
                ],
                capture_output=True, text=True, timeout=120,
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
            # Fallback: irgendeine .description im selben Ordner
            candidates = list(video_path.parent.glob("*.description"))
            if not candidates:
                return None
            desc = candidates[0]
        try:
            text = desc.read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            return None


class FrameExtractor:
    @staticmethod
    def extract(video_path: Path, out_path: Optional[Path] = None) -> Optional[Path]:
        if out_path is None:
            out_path = video_path.parent / f"frame_{video_path.stem}.jpg"
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(video_path),
                    "-vf", "select=eq(n\\,0)", "-frames:v", "1", str(out_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0 or not out_path.exists():
                logger.error(f"ffmpeg: {result.stderr[:200]}")
                return None
            return out_path
        except Exception as e:
            logger.error(f"ffmpeg Exception: {e}")
            return None
