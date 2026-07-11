"""Safe, cancellable yt-dlp wrapper."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional, Sequence

from ..url_utils import require_supported_media_url

logger = logging.getLogger(__name__)

_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()


def _register(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)


def _unregister(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.discard(proc)


def _terminate(proc: subprocess.Popen, grace: float = 5.0) -> bool:
    if proc.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        finally:
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
    except (ProcessLookupError, OSError):
        pass
    return True


def cancel_active_downloads() -> int:
    """Terminate all currently running yt-dlp process groups."""
    with _ACTIVE_LOCK:
        processes = list(_ACTIVE)
    return sum(1 for proc in processes if _terminate(proc))


class VideoDownloader:
    def __init__(self, ytdlp_path: str, temp_dir: Path, cookies_file: Optional[str] = None,
                 timeout: int = 300, max_filesize_mb: int = 500, retries: int = 3):
        self.ytdlp_path = str(ytdlp_path)
        self.temp_dir = Path(temp_dir)
        self.cookies_file = cookies_file if cookies_file and Path(cookies_file).is_file() else None
        self.timeout = max(30, int(timeout))
        self.max_filesize_mb = max(1, int(max_filesize_mb))
        self.retries = max(0, int(retries))
        if cookies_file and not self.cookies_file:
            logger.warning("Cookie-Datei konfiguriert aber nicht gefunden: %s", cookies_file)

    def _execute(self, args: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name == "posix"),
        )
        _register(proc)
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate(proc)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(list(args), timeout, output=stdout, stderr=stderr)
            return subprocess.CompletedProcess(list(args), int(proc.returncode or 0), stdout, stderr)
        finally:
            if proc.poll() is None:
                _terminate(proc)
            _unregister(proc)

    def download(self, url: str) -> Optional[Path]:
        try:
            url = require_supported_media_url(url)
        except ValueError as exc:
            logger.warning("yt-dlp URL abgelehnt: %s", exc)
            return None

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        sub = self.temp_dir / uuid.uuid4().hex[:12]
        sub.mkdir(mode=0o700, parents=False, exist_ok=False)
        success = False
        cmd = [
            self.ytdlp_path,
            "-o", str(sub / "video.%(ext)s"),
            "--no-playlist", "--quiet", "--no-warnings",
            "--write-description",
            "--socket-timeout", "30",
            "--retries", str(self.retries),
            "--fragment-retries", str(self.retries),
            "--max-filesize", f"{self.max_filesize_mb}M",
            "--no-part",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        cmd += ["--", url]
        try:
            result = self._execute(cmd, timeout=self.timeout)
            if result.returncode != 0:
                logger.error("yt-dlp Fehler: %s", (result.stderr or "").strip()[-1000:])
                return None
            videos = (
                list(sub.glob("video.mp4"))
                or list(sub.glob("video.webm"))
                or list(sub.glob("video.mkv"))
                or list(sub.glob("video.*"))
            )
            videos = [
                path for path in videos
                if path.is_file() and path.suffix.lower() not in {".description", ".part", ".ytdl"}
            ]
            if not videos:
                logger.warning("yt-dlp: kein Video heruntergeladen für %s", url)
                return None
            success = True
            return videos[0]
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp Timeout nach %ss für %s", self.timeout, url)
            return None
        except (OSError, ValueError) as exc:
            logger.error("yt-dlp konnte nicht gestartet werden für %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.exception("yt-dlp Exception für %s: %s", url, exc)
            return None
        finally:
            if not success:
                shutil.rmtree(sub, ignore_errors=True)

    def fetch_description(self, url: str, *, timeout: int = 60) -> Optional[str]:
        try:
            url = require_supported_media_url(url)
        except ValueError as exc:
            logger.warning("Metadata-URL abgelehnt: %s", exc)
            return None
        cmd = [
            self.ytdlp_path, "--skip-download", "--no-warnings", "--no-playlist",
            "--print", "%(description)s\n%(title)s",
        ]
        if self.cookies_file:
            cmd += ["--cookies", self.cookies_file]
        cmd += ["--", url]
        try:
            result = self._execute(cmd, timeout=max(10, int(timeout)))
            if result.returncode != 0:
                logger.warning("yt-dlp metadata fail %s: %s", url, (result.stderr or "")[-300:])
                return None
            text = (result.stdout or "").strip()
            return text or None
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("yt-dlp metadata exception %s: %s", url, exc)
            return None

    @staticmethod
    def read_description(video_path: Path) -> Optional[str]:
        desc = video_path.parent / "video.description"
        if not desc.exists():
            return None
        try:
            return desc.read_text(encoding="utf-8", errors="ignore").strip() or None
        except OSError:
            return None
