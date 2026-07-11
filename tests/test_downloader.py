import stat
import threading
import time
from pathlib import Path

from app.core.downloader import VideoDownloader, cancel_active_downloads


def make_fake_ytdlp(path: Path, *, sleep: bool = False):
    body = '''#!/usr/bin/env python3
import pathlib, sys, time
if {sleep!r}:
    time.sleep(30)
out = None
for i, arg in enumerate(sys.argv):
    if arg == "-o" and i + 1 < len(sys.argv):
        out = sys.argv[i+1]
if out:
    video = pathlib.Path(out.replace("%(ext)s", "mp4"))
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    (video.parent / "video.description").write_text("A useful recipe description")
print("description")
'''.format(sleep=sleep)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_download_success_and_invalid_url_cleanup(tmp_path: Path):
    binary = tmp_path / "yt-dlp"
    make_fake_ytdlp(binary)
    temp = tmp_path / "temp"
    downloader = VideoDownloader(str(binary), temp, timeout=30)
    video = downloader.download("https://www.tiktok.com/@x/video/1")
    assert video and video.read_bytes() == b"video"
    assert downloader.read_description(video) == "A useful recipe description"
    assert downloader.download("file:///etc/passwd") is None


def test_cancel_active_download_process(tmp_path: Path):
    binary = tmp_path / "yt-dlp"
    make_fake_ytdlp(binary, sleep=True)
    downloader = VideoDownloader(str(binary), tmp_path / "temp", timeout=60)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(downloader.download("https://www.instagram.com/reel/abc/")),
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    stopped = 0
    while time.time() < deadline and stopped == 0:
        stopped = cancel_active_downloads()
        if stopped == 0:
            time.sleep(0.05)
    thread.join(timeout=5)
    assert stopped >= 1
    assert not thread.is_alive()
    assert result == [None]
