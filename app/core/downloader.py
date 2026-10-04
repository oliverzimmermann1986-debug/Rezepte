"""Begrenzter yt-dlp-Wrapper für die lokale Quellenverarbeitung.

Die anschließende Klassifizierung ist ein separater OpenAI-Pfad. Dieser Wrapper
startet nur den Downloader und trifft selbst keine Aussage über Datenübertragung.
"""
from __future__ import annotations

import logging
import os
import signal
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
MAX_VIDEO_DOWNLOAD_BYTES = 100 * 1024 * 1024


def _discard_download(folder: Path, root: Path) -> None:
    """Löscht nur den eigenen Downloadordner, auch nach verzögertem Unlock."""
    folder, root = folder.resolve(), root.resolve()
    if folder == root or not folder.is_relative_to(root):
        raise ValueError('Unsicherer temporärer Downloadpfad')
    for attempt in range(5):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 4:
                logger.warning('Temporärer Download konnte nicht entfernt werden: %s', folder)
                return
            time.sleep(.1 * (attempt + 1))


def _run_bounded_download(command, folder: Path, *, timeout: float = 180,
                          max_bytes: int = MAX_VIDEO_DOWNLOAD_BYTES):
    """Bricht auch Streams ohne bekannte Dateigröße ab und begrenzt Fehlerlogs."""
    with tempfile.TemporaryFile(dir=folder.parent) as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=errors, start_new_session=os.name == 'posix')
        deadline = time.monotonic() + timeout
        completed = False
        try:
            while True:
                size = os.fstat(errors.fileno()).st_size
                for path in folder.rglob('*'):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        # yt-dlp benennt gerade .part in die fertige Datei um.
                        continue
                if size > max_bytes:
                    raise ValueError('Video-Download überschreitet das Limit von 100 MiB')
                if process.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(0.05)
            errors.seek(max(0, os.fstat(errors.fileno()).st_size - 2000))
            completed = True
            return subprocess.CompletedProcess(command, process.returncode, '',
                                                errors.read().decode('utf-8', errors='replace'))
        finally:
            if not completed:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


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
            "--max-filesize", str(MAX_VIDEO_DOWNLOAD_BYTES),
            "--write-description",
            # Cover direkt mitladen — sonst startet jedes Rezept ohne Thumbnail
            # und landet im Audit unter "Kein Bild" (nur Re-Scrape holte es bisher).
            "--write-thumbnail", "--convert-thumbnails", "jpg",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        try:
            result = _run_bounded_download(cmd, sub)
            if result.returncode != 0:
                logger.error(f"yt-dlp Fehler: {result.stderr.strip()}")
                _discard_download(sub, self.temp_dir)
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
                _discard_download(sub, self.temp_dir)
                return None
            return videos[0]
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp Timeout")
            _discard_download(sub, self.temp_dir)
            return None
        except Exception as e:
            logger.error(f"yt-dlp Exception: {e}")
            _discard_download(sub, self.temp_dir)
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
            # TikTok-Kurzlinks sind nicht stabil: mehrere vm.tiktok.com-Links
            # können auf denselben Beitrag zeigen. Die aufgelöste Beitrags-URL
            # dient der Import-Pipeline deshalb als kanonische Identität.
            "--print", "CODEX_CANONICAL_URL=%(webpage_url)s",
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
                _discard_download(sub, self.temp_dir)
                return None
        except subprocess.TimeoutExpired:
            logger.warning(f"yt-dlp refresh_metadata Timeout für {url}")
            _discard_download(sub, self.temp_dir)
            return None
        except Exception as e:
            logger.warning(f"yt-dlp refresh_metadata Exception: {e}")
            _discard_download(sub, self.temp_dir)
            return None

        out: Dict[str, Any] = {}
        for line in (result.stdout or "").splitlines():
            marker = "CODEX_CANONICAL_URL="
            if line.startswith(marker):
                canonical_url = line[len(marker):].strip()
                if canonical_url and canonical_url.lower() != "na":
                    out["canonical_url"] = canonical_url
                break

        # description-File suchen (yt-dlp schreibt thumb.description trotz -o)
        desc_files = list(sub.glob("*.description"))
        thumb_files = list(sub.glob("*.jpg")) + list(sub.glob("*.jpeg")) + list(sub.glob("*.webp")) + list(sub.glob("*.png"))
        if not desc_files and not thumb_files and not out:
            _discard_download(sub, self.temp_dir)
            return None

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
            _discard_download(sub, self.temp_dir)
