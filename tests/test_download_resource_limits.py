"""Use real synthetic processes to prove unknown-size and timeout limits."""
from pathlib import Path
import subprocess
import sys

import pytest

from app.core import downloader


def test_unknown_size_download_is_stopped_and_only_its_own_directory_is_removed(tmp_path, monkeypatch):
    outside = tmp_path/'unrelated.txt'
    outside.write_bytes(b'preserve')
    original = downloader._run_bounded_download
    processes = []
    popen = downloader.subprocess.Popen
    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(downloader.subprocess, 'Popen', track)
    def simulate(command, folder):
        assert command[command.index('--max-filesize')+1] == str(100*1024*1024)
        code = "from pathlib import Path\nimport sys,time\nf=open(Path(sys.argv[1])/'video.mp4.part','wb',buffering=0)\nfor _ in range(500):\n f.write(b'x'*8192)\n time.sleep(.01)\n"
        return original([sys.executable,'-c',code,str(folder)],folder,max_bytes=32768,timeout=5)
    monkeypatch.setattr(downloader, '_run_bounded_download', simulate)
    assert downloader.VideoDownloader('synthetic-ytdlp',tmp_path/'downloads').download('https://example.test/video') is None
    assert list((tmp_path/'downloads').iterdir()) == []
    assert outside.read_bytes() == b'preserve'
    assert processes and all(process.poll() is not None for process in processes)


def test_bounded_download_allows_a_small_successful_process(tmp_path):
    folder = tmp_path/'download'
    folder.mkdir()
    code = "from pathlib import Path\nimport sys\n(Path(sys.argv[1])/'video.mp4').write_bytes(b'synthetic video')"
    result = downloader._run_bounded_download([sys.executable,'-c',code,str(folder)],folder,max_bytes=1024)
    assert result.returncode == 0 and (folder/'video.mp4').read_bytes() == b'synthetic video'


def test_bounded_download_stops_a_hanging_process(tmp_path):
    folder = tmp_path/'download'
    folder.mkdir()
    with pytest.raises(subprocess.TimeoutExpired):
        downloader._run_bounded_download([sys.executable,'-c','import time;time.sleep(30)'],folder,timeout=.1)
