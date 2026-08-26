"""Begrenzter Video-Fallback für unvollständige Rezepte.

Videos bleiben lokale Backend-Quellen und werden nie über eine App-Route
bereitgestellt. Erst werden wenige Frames auf eingeblendeten Text geprüft;
nur wenn danach weiterhin Zutaten oder Schritte fehlen, wird die Audiospur
transkribiert. Ergebnisse werden neben dem Video gecacht, um API-Kosten und
wiederholte Verarbeitung zu vermeiden.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core.safety import atomic_write_json, resolve_directory_under, resolve_regular_file_under

logger = logging.getLogger(__name__)

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")
CACHE_VERSION = 1


@dataclass(frozen=True)
class VideoFallbackSettings:
    enabled: bool = True
    max_frames: int = 10
    max_seconds: int = 600
    transcription_model: str = "gpt-4o-mini-transcribe"
    cache_filename: str = ".video-ai-evidence.json"

    @classmethod
    def from_ai_config(cls, ai_config: Optional[dict]) -> "VideoFallbackSettings":
        raw = (ai_config or {}).get("video_fallback") or {}
        cache_name = str(raw.get("cache_filename") or cls.cache_filename)
        if Path(cache_name).name != cache_name or not cache_name.endswith(".json"):
            cache_name = cls.cache_filename
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_frames=max(1, min(10, int(raw.get("max_frames") or 10))),
            max_seconds=max(30, min(900, int(raw.get("max_seconds") or 600))),
            transcription_model=str(
                raw.get("transcription_model") or "gpt-4o-mini-transcribe"
            ).strip(),
            cache_filename=cache_name,
        )


@dataclass
class VideoAnalysisResult:
    content: Optional[dict]
    used_video: bool = False
    frame_text_count: int = 0
    transcribed: bool = False
    reason: Optional[str] = None
    evidence_text: str = ""


def _empty_content() -> dict:
    return {"ingredients": [], "steps": [], "servings": None, "tags": []}


def _is_complete(content: Optional[dict]) -> bool:
    return bool(content and content.get("ingredients") and content.get("steps"))


def _merge_missing(primary: Optional[dict], enrichment: Optional[dict]) -> Optional[dict]:
    if primary is None and enrichment is None:
        return None
    out = dict(primary or _empty_content())
    extra = enrichment or {}
    for key in ("ingredients", "steps"):
        if not out.get(key) and extra.get(key):
            out[key] = extra[key]
    if out.get("servings") is None and extra.get("servings") is not None:
        out["servings"] = extra["servings"]
    out["tags"] = sorted(set(out.get("tags") or []) | set(extra.get("tags") or []))
    return out


def find_recipe_video(recipe: dict, recipe_root: Path) -> Optional[Path]:
    """Findet nur reguläre Videodateien innerhalb des geprüften Rezeptordners."""
    try:
        folder = resolve_directory_under(Path(recipe.get("folder_path") or ""), recipe_root)
    except (OSError, ValueError):
        return None

    names: list[str] = []
    configured = str(recipe.get("video_filename") or "").strip()
    if configured:
        names.append(configured)
    try:
        names.extend(
            child.name
            for child in folder.iterdir()
            if child.suffix.lower() in VIDEO_SUFFIXES and child.name not in names
        )
    except OSError:
        return None

    for name in names:
        if Path(name).name != name or Path(name).suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            return resolve_regular_file_under(folder / name, folder, recipe_root)
        except (OSError, ValueError):
            continue
    return None


def _fingerprint(video: Path) -> dict:
    stat = video.stat()
    return {"name": video.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_cache(video: Path, settings: VideoFallbackSettings) -> dict:
    cache = video.parent / settings.cache_filename
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("version") == CACHE_VERSION and data.get("video") == _fingerprint(video):
            # Cache-Dateien aus der ersten Version hatten noch keine expliziten
            # Attempt-Flags. Das vorhandene Feld bedeutet aber, dass der Lauf
            # bereits stattgefunden hat – auch bei leerem Ergebnis.
            data.setdefault("frames_attempted", "frame_texts" in data)
            data.setdefault("transcription_attempted", "transcript" in data)
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {
        "version": CACHE_VERSION,
        "video": _fingerprint(video),
        "frame_texts": [],
        "frames_attempted": False,
        "transcript": None,
        "transcription_attempted": False,
    }


def _save_cache(video: Path, settings: VideoFallbackSettings, data: dict) -> None:
    try:
        atomic_write_json(video.parent / settings.cache_filename, data)
    except OSError as exc:
        logger.warning("Video-KI-Cache für %s nicht schreibbar: %s", video, exc)


def _probe_duration(video: Path, max_seconds: int) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        duration = float((result.stdout or "").strip())
        if result.returncode == 0 and math.isfinite(duration) and duration > 0:
            return min(duration, float(max_seconds))
    except (FileNotFoundError, subprocess.TimeoutExpired, TypeError, ValueError):
        pass
    return min(10.0, float(max_seconds))


def _frame_timestamps(duration: float, max_frames: int) -> list[float]:
    # Kurze Social-Videos blenden Zutaten oft nur für wenige Sekunden ein.
    # Vier-Sekunden-Abstände treffen diese Tafeln deutlich zuverlässiger als
    # die frühere Acht-Sekunden-Abtastung, bleiben aber hart auf zehn Frames
    # und damit auf kalkulierbare Vision-Kosten begrenzt.
    count = min(max_frames, max(1, int(math.ceil(duration / 4.0))))
    if duration <= 0.5:
        return [0.0]
    return [
        round(min(duration - 0.1, duration * (index + 0.5) / count), 3)
        for index in range(count)
    ]


def _extract_frame_texts(analyzer, video: Path, settings: VideoFallbackSettings) -> list[str]:
    duration = _probe_duration(video, settings.max_seconds)
    texts: list[str] = []
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="recipe-video-frames-") as tmp:
        temp_dir = Path(tmp)
        for index, seconds in enumerate(_frame_timestamps(duration, settings.max_frames), start=1):
            target = temp_dir / f"frame-{index:02d}.jpg"
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error", "-ss", str(seconds),
                        "-i", str(video), "-frames:v", "1", "-vf",
                        "scale='min(960,iw)':-2", "-q:v", "3", str(target),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("Videoframe-Extraktion nicht möglich: %s", exc)
                break
            if result.returncode != 0 or not target.is_file():
                continue
            text = analyzer.extract_text_from_video_frame_bytes(
                target.read_bytes(),
                "image/jpeg",
                f"{video.name} bei {seconds:.1f} Sekunden",
            )
            normalized = " ".join((text or "").lower().split())
            if text and normalized and normalized not in seen:
                seen.add(normalized)
                texts.append(text.strip())
    return texts


def _extract_transcript(analyzer, video: Path, settings: VideoFallbackSettings) -> Optional[str]:
    with tempfile.TemporaryDirectory(prefix="recipe-video-audio-") as tmp:
        audio = Path(tmp) / "audio.mp3"
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                    "-t", str(settings.max_seconds), "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "48k", str(audio),
                ],
                capture_output=True,
                text=True,
                timeout=max(90, settings.max_seconds + 30),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("Audio-Extraktion nicht möglich: %s", exc)
            return None
        if result.returncode != 0 or not audio.is_file() or audio.stat().st_size <= 0:
            return None
        return analyzer.transcribe_audio(audio, model=settings.transcription_model)


def _combined_text(description: str, frame_texts: list[str], transcript: Optional[str]) -> str:
    parts: list[str] = []
    if description.strip():
        parts.append("QUELLTEXT / CAPTION:\n" + description.strip())
    if frame_texts:
        parts.append(
            "EINGEBLENDETER TEXT AUS VIDEOFRAMES:\n" + "\n---\n".join(frame_texts)
        )
    if transcript:
        parts.append("GESPROCHENER TEXT AUS DER AUDIOSPUR:\n" + transcript.strip())
    return "\n\n".join(parts)[:30000]


def analyze_recipe_video_file(
    analyzer,
    video: Path,
    *,
    ai_config: Optional[dict] = None,
    existing_tags: Optional[list[str]] = None,
    existing_canonical: Optional[list[str]] = None,
    description: Optional[str] = None,
) -> VideoAnalysisResult:
    """Analysiert eine explizite Videodatei für den Erst- oder Nachimport.

    Der Aufrufer ist für die sichere Herkunft des Pfads verantwortlich. Der
    bestehende Rezept-Fallback löst den Pfad weiterhin selbst innerhalb des
    konfigurierten Rezeptwurzelverzeichnisses auf.
    """
    settings = VideoFallbackSettings.from_ai_config(ai_config)
    source_text = (description or "").strip()
    content: Optional[dict] = None
    if len(source_text) >= 20:
        content = analyzer.analyze_recipe_content(
            source_text,
            existing_tags=existing_tags,
            existing_canonical=existing_canonical,
        )
        if _is_complete(content):
            return VideoAnalysisResult(
                content=content,
                reason="caption_complete",
                evidence_text=source_text,
            )

    if not settings.enabled:
        return VideoAnalysisResult(
            content=content,
            reason="video_fallback_disabled",
            evidence_text=source_text,
        )
    if not video.is_file():
        return VideoAnalysisResult(
            content=content,
            reason="video_missing",
            evidence_text=source_text,
        )

    cache = _load_cache(video, settings)
    frame_texts = [str(item).strip() for item in cache.get("frame_texts") or [] if str(item).strip()]
    if not frame_texts and not cache.get("frames_attempted"):
        frame_texts = _extract_frame_texts(analyzer, video, settings)
        cache["frame_texts"] = frame_texts
        cache["frames_attempted"] = True
        _save_cache(video, settings, cache)

    if frame_texts:
        frame_evidence = _combined_text(source_text, frame_texts, None)
        frame_content = analyzer.analyze_recipe_content(
            frame_evidence,
            existing_tags=existing_tags,
            existing_canonical=existing_canonical,
        )
        content = _merge_missing(content, frame_content)
        if _is_complete(content):
            return VideoAnalysisResult(
                content=content,
                used_video=True,
                frame_text_count=len(frame_texts),
                reason="frames_complete",
                evidence_text=frame_evidence,
            )

    transcript = (cache.get("transcript") or "").strip() or None
    if not transcript and not cache.get("transcription_attempted"):
        transcript = _extract_transcript(analyzer, video, settings)
        cache["transcript"] = transcript
        cache["transcription_attempted"] = True
        _save_cache(video, settings, cache)

    if transcript:
        combined_evidence = _combined_text(source_text, frame_texts, transcript)
        audio_content = analyzer.analyze_recipe_content(
            combined_evidence,
            existing_tags=existing_tags,
            existing_canonical=existing_canonical,
        )
        content = _merge_missing(content, audio_content)
    else:
        combined_evidence = _combined_text(source_text, frame_texts, None)

    return VideoAnalysisResult(
        content=content,
        used_video=bool(frame_texts or transcript),
        frame_text_count=len(frame_texts),
        transcribed=bool(transcript),
        reason="video_complete" if _is_complete(content) else "video_incomplete",
        evidence_text=combined_evidence,
    )


def analyze_recipe_with_video_fallback(
    analyzer,
    recipe: dict,
    *,
    recipe_root: Path,
    ai_config: Optional[dict] = None,
    existing_tags: Optional[list[str]] = None,
    existing_canonical: Optional[list[str]] = None,
    description: Optional[str] = None,
) -> VideoAnalysisResult:
    """Analysiert Caption, dann Frames und zuletzt bei Bedarf die Audiospur."""
    source_text = (
        description if description is not None else recipe.get("description") or ""
    ).strip()
    video = find_recipe_video(recipe, recipe_root)
    if video is None:
        content: Optional[dict] = None
        if len(source_text) >= 20:
            content = analyzer.analyze_recipe_content(
                source_text,
                existing_tags=existing_tags,
                existing_canonical=existing_canonical,
            )
        return VideoAnalysisResult(
            content=content,
            reason="caption_complete" if _is_complete(content) else "video_missing",
            evidence_text=source_text,
        )
    return analyze_recipe_video_file(
        analyzer,
        video,
        ai_config=ai_config,
        existing_tags=existing_tags,
        existing_canonical=existing_canonical,
        description=source_text,
    )
