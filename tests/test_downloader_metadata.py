from pathlib import Path

from app.core.downloader import VideoDownloader


def test_refresh_metadata_returns_canonical_webpage_url(monkeypatch, tmp_path):
    def fake_run(command, **_kwargs):
        output_template = Path(command[command.index("-o") + 1])
        output_template.with_name("thumb.description").write_text(
            "Kartoffel-Bowl",
            encoding="utf-8",
        )

        class Result:
            returncode = 0
            stderr = ""
            stdout = (
                "CODEX_CANONICAL_URL=https://www.tiktok.com/@koch/video/"
                "7666167423783030049?_r=1&_t=tracking\n"
            )

        return Result()

    monkeypatch.setattr("app.core.downloader.subprocess.run", fake_run)
    downloader = VideoDownloader("yt-dlp", tmp_path / "temp")

    result = downloader.refresh_metadata("https://vm.tiktok.com/Kurzlink/")

    assert result == {
        "canonical_url": (
            "https://www.tiktok.com/@koch/video/"
            "7666167423783030049?_r=1&_t=tracking"
        ),
        "description_text": "Kartoffel-Bowl",
    }
