from pathlib import Path

import pytest

from app.path_utils import build_under, ensure_within, safe_component
from app.url_utils import is_supported_media_url, require_supported_media_url


def test_safe_component_blocks_traversal_reserved_and_long_names():
    assert safe_component("../..") == "Unbekannt"
    assert safe_component("CON") == "_CON"
    assert "/" not in safe_component("a/b\\c")
    assert len(safe_component("ä" * 200).encode("utf-8")) <= 96


def test_build_under_and_ensure_within(tmp_path: Path):
    target = build_under(tmp_path, ["../../etc", "A/B", ".."])
    assert target.is_relative_to(tmp_path)
    with pytest.raises(ValueError):
        ensure_within(tmp_path.parent / "outside", tmp_path)


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@user/video/123",
    "https://vm.tiktok.com/abc/",
    "https://www.instagram.com/reel/abc/",
])
def test_supported_media_urls(url: str):
    assert is_supported_media_url(url)
    assert require_supported_media_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "https://tiktok.com.evil.example/x",
    "https://user:pass@instagram.com/reel/x",
    "https://instagram.com:444/reel/x",
    "--config=/tmp/evil",
    "http://127.0.0.1/admin",
])
def test_rejects_non_media_urls(url: str):
    assert not is_supported_media_url(url)
    with pytest.raises(ValueError):
        require_supported_media_url(url)
