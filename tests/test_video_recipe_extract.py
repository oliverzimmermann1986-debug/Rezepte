from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import requests

from app.core.analyzer import OpenAIAnalyzer
from app.recipes.video_recipe_extract import (
    VideoAnalysisResult,
    analyze_recipe_with_video_fallback,
)


def _content(*, ingredients=None, steps=None, servings=None, tags=None):
    return {
        "ingredients": ingredients or [],
        "steps": steps or [],
        "servings": servings,
        "tags": tags or [],
    }


def test_audio_upload_uses_multipart_without_json_content_type(tmp_path, monkeypatch):
    import app.core.analyzer as analyzer_module

    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "Zwiebeln schneiden und anbraten."}

    def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(analyzer_module, "server_configured_request", fake_request)
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake audio")
    analyzer = OpenAIAnalyzer("test-key")

    result = analyzer.transcribe_audio(audio)

    assert result == "Zwiebeln schneiden und anbraten."
    assert captured["url"].endswith("/audio/transcriptions")
    assert "files" in captured
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert "Content-Type" not in captured["headers"]


def test_server_configured_transport_forwards_multipart(monkeypatch):
    import app.core.webhook as webhook

    captured = {}
    marker = object()

    def fake_pinned(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return marker

    monkeypatch.setattr(webhook, "pinned_https_request", fake_pinned)
    files = {"file": ("audio.mp3", b"audio", "audio/mpeg")}

    response = webhook.server_configured_request(
        "POST",
        "https://api.openai.com/v1/audio/transcriptions",
        files=files,
        data={"model": "gpt-4o-mini-transcribe"},
    )

    assert response is marker
    assert captured["files"] is files
    assert captured["data"]["model"] == "gpt-4o-mini-transcribe"


def test_openai_rate_limit_is_retried_with_server_delay(monkeypatch):
    import app.core.analyzer as analyzer_module

    analyzer = OpenAIAnalyzer("test-key")
    limited = requests.Response()
    limited.status_code = 429
    limited._content = b'{"error":{"message":"Please try again in 250ms."}}'
    limited.headers["content-type"] = "application/json"
    success = requests.Response()
    success.status_code = 200
    responses = iter([limited, success])
    delays = []

    monkeypatch.setattr(analyzer, "request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(analyzer_module.time, "sleep", delays.append)

    response = analyzer._request_with_retry("POST", "/chat/completions", json={})

    assert response is success
    assert delays == [1.5]


def test_video_fallback_reads_frames_then_audio_and_caches(tmp_path, monkeypatch):
    import app.recipes.video_recipe_extract as video_extract

    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Test" / "Altes Rezept"
    folder.mkdir(parents=True)
    video = folder / "source.mp4"
    video.write_bytes(b"fake video")
    recipe = {
        "id": 7,
        "name": "Altes Rezept",
        "folder_path": str(folder),
        "video_filename": video.name,
        "description": "Kurze Caption mit zu wenig Rezeptdaten.",
    }
    ffmpeg_calls = []

    def fake_run(args, **_kwargs):
        if args[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="16.0\n", stderr="")
        ffmpeg_calls.append(args)
        target = Path(args[-1])
        target.write_bytes(b"generated media")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video_extract.subprocess, "run", fake_run)

    class Analyzer:
        def analyze_recipe_content(self, text, **_kwargs):
            if "GESPROCHENER TEXT" in text:
                return _content(
                    steps=[{"instruction": "Alles zehn Minuten köcheln.", "timer_seconds": 600}],
                    tags=["einfach"],
                )
            if "EINGEBLENDETER TEXT" in text:
                return _content(
                    ingredients=[{"name": "Kartoffel", "amount": 500, "unit": "g"}],
                    tags=["deutsch"],
                )
            return _content()

        def extract_text_from_video_frame_bytes(self, *_args):
            return "500 g Kartoffeln"

        def transcribe_audio(self, _path, *, model):
            assert model == "gpt-4o-mini-transcribe"
            return "Alles zehn Minuten köcheln."

    first = analyze_recipe_with_video_fallback(
        Analyzer(),
        recipe,
        recipe_root=root,
        ai_config={"video_fallback": {"max_frames": 3}},
    )

    assert first.content["ingredients"][0]["name"] == "Kartoffel"
    assert first.content["steps"][0]["timer_seconds"] == 600
    assert first.used_video is True
    assert first.frame_text_count == 1
    assert first.transcribed is True
    assert (folder / ".video-ai-evidence.json").is_file()

    calls_after_first = len(ffmpeg_calls)
    second = analyze_recipe_with_video_fallback(
        Analyzer(),
        recipe,
        recipe_root=root,
        ai_config={"video_fallback": {"max_frames": 3}},
    )
    assert second.content["steps"]
    assert len(ffmpeg_calls) == calls_after_first


def test_empty_frame_and_audio_results_are_not_retried(tmp_path, monkeypatch):
    import app.recipes.video_recipe_extract as video_extract

    root = tmp_path / "recipes"
    folder = root / "Typ" / "Kategorie" / "Ohne Belege"
    folder.mkdir(parents=True)
    (folder / "source.mp4").write_bytes(b"fake")
    recipe = {
        "folder_path": str(folder),
        "video_filename": "source.mp4",
        "description": "Ausreichend lange Beschreibung ohne Rezeptinhalt.",
    }
    calls = {"frames": 0, "audio": 0}

    monkeypatch.setattr(
        video_extract,
        "_extract_frame_texts",
        lambda *_args: calls.__setitem__("frames", calls["frames"] + 1) or [],
    )
    monkeypatch.setattr(
        video_extract,
        "_extract_transcript",
        lambda *_args: calls.__setitem__("audio", calls["audio"] + 1) or None,
    )

    class Analyzer:
        def analyze_recipe_content(self, *_args, **_kwargs):
            return _content()

    for _ in range(2):
        analyze_recipe_with_video_fallback(Analyzer(), recipe, recipe_root=root)

    assert calls == {"frames": 1, "audio": 1}

    analyze_recipe_with_video_fallback(
        Analyzer(),
        recipe,
        recipe_root=root,
        force_refresh=True,
    )

    assert calls == {"frames": 2, "audio": 2}


def test_manual_extract_fills_missing_steps_without_replacing_ingredients(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    import app.routes.api_recipes as api_recipes

    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Test" / "Bewahrt"
    folder.mkdir(parents=True)
    (folder / "source.mp4").write_bytes(b"video")
    test_db.recipe_upsert(
        url="https://www.tiktok.com/@test/video/1",
        name="Bewahrt",
        type="Hauptgericht",
        category="Test",
        folder_path=str(folder),
        description="Eine ausreichend lange, aber unvollständige Beschreibung.",
        thumb_filename=None,
        video_filename="source.mp4",
        source_added_at=1.0,
    )
    recipe = test_db.recipe_get_by_folder(str(folder))
    recipe_id = int(recipe["id"])
    test_db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=[{
            "name": "Kartoffel",
            "canonical_name": "kartoffel",
            "amount": 500,
            "unit": "g",
            "raw": "500 g Kartoffeln",
        }],
        steps=[],
        servings=4,
        auto_tags=["deutsch"],
        status="error",
    )
    with test_db.conn() as connection:
        connection.execute(
            "UPDATE recipe_ingredients SET calories=385 WHERE recipe_id=?",
            (recipe_id,),
        )

    class Config:
        def get(self, *keys, default=None):
            if keys == ("paths", "recipe_dir"):
                return str(root)
            if keys == ("ai",):
                return {"openai": {"api_key": "test"}}
            return default

    monkeypatch.setattr(api_recipes, "get_config", lambda: Config())
    monkeypatch.setattr(api_recipes, "_recipe_root", lambda: root)
    monkeypatch.setattr(api_recipes, "build_analyzer", lambda _cfg: object())
    monkeypatch.setattr(
        api_recipes,
        "analyze_recipe_with_video_fallback",
        lambda *_args, **_kwargs: VideoAnalysisResult(
            content=_content(
                ingredients=[{"name": "Falscher Ersatz", "amount": 1, "unit": "Stück"}],
                steps=[{"instruction": "Kartoffeln weich kochen.", "timer_seconds": 1200}],
                servings=2,
                tags=["einfach"],
            ),
            used_video=True,
            frame_text_count=1,
            transcribed=True,
            reason="video_complete",
        ),
    )

    response = client.post(f"/api/recipes/{recipe_id}/extract")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    ingredients = test_db.recipe_ingredients_get(recipe_id)
    assert [item["name"] for item in ingredients] == ["Kartoffel"]
    assert ingredients[0]["calories"] == 385
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Kartoffeln weich kochen."
    assert test_db.recipe_get(recipe_id)["servings"] == 4
