from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import threading

import pymupdf
import pytest
from PIL import Image

from app.core.analyzer import RecipeAnalysis
from app.core.email_processor import normalize_content_url
from app.jobs.scraper import ScraperJob
from app.recipes.pdf_recipe_extract import ExtractedRecipeData
from app.recipes.video_recipe_extract import VideoAnalysisResult


def _jpeg_bytes(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    document.new_page(width=240, height=180)
    data = document.tobytes()
    document.close()
    return data


def test_native_file_upload_uses_attachment_pipeline(client, monkeypatch):
    import app.routes.api_pending as api_pending

    captured = {}

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            captured["attachment"] = attachment
            captured["url"] = synth_url
            return {"status": "pending", "name": "Unbekannt"}

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    jpeg = _jpeg_bytes()
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("Mein_Rezept.jpg", jpeg, "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["ok"] is True
    assert captured["attachment"]["ext"] == ".jpg"
    assert captured["attachment"]["data"] == jpeg
    assert captured["url"].startswith("manual-upload://")


def test_native_file_upload_returns_conflict_when_scraper_is_busy(client, monkeypatch):
    import app.routes.api_pending as api_pending

    @contextmanager
    def busy_lock(_name):
        yield None

    monkeypatch.setattr(api_pending, "file_lock_or_none", busy_lock)
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("Rezept.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 409
    assert "Import läuft bereits" in response.json()["detail"]


def test_pending_photo_upload_targets_existing_item_and_starts_vision(client, test_db, monkeypatch):
    import app.routes.api_pending as api_pending

    url = "https://www.tiktok.com/@koch/video/photo-scan"
    test_db.pending_add(
        url,
        "recipe",
        ai_suggestion={"name": "TikTok-Rezept prüfen", "source": "external-link"},
    )
    captured = {}

    class FakeJob:
        def attach_pending_photo(self, item_url, data, suffix, filename):
            captured.update(
                url=item_url,
                data=data,
                suffix=suffix,
                filename=filename,
            )
            return {
                "ok": True,
                "action": "still_pending",
                "message": "Foto erkannt",
            }

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    jpeg = _jpeg_bytes("blue")
    response = client.post(
        "/api/pending/scan-photo",
        params={"url": url},
        files={"file": ("Rezept Foto.jpg", jpeg, "image/jpeg")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "still_pending"
    assert captured == {
        "url": url,
        "data": jpeg,
        "suffix": ".jpg",
        "filename": "Rezept Foto.jpg",
    }


def test_pending_photo_upload_requires_open_recipe_item(client):
    response = client.post(
        "/api/pending/scan-photo",
        params={"url": "https://www.tiktok.com/@koch/video/missing"},
        files={"file": ("rezept.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert response.status_code == 404
    assert "Prüfeintrag" in response.json()["detail"]


def test_file_import_offloads_blocking_pipeline_to_threadpool():
    source = Path(__file__).resolve().parents[1] / "app" / "routes" / "api_pending.py"
    code = source.read_text(encoding="utf-8")
    assert "await run_in_threadpool(" in code
    assert "with file_lock_or_none(\"scraper\")" in code


def test_native_file_upload_rejects_unsupported_type(client):
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("rezept.txt", b"text", "text/plain")},
    )
    assert response.status_code == 415


def test_native_file_upload_rejects_renamed_or_mismatched_content(client):
    renamed = client.post(
        "/api/pending/import-file",
        files={"file": ("rezept.pdf", b"not-a-pdf", "application/pdf")},
    )
    assert renamed.status_code == 415

    mismatched = client.post(
        "/api/pending/import-file",
        files={"file": ("rezept.jpg", b"%PDF-1.7\n", "image/jpeg")},
    )
    assert mismatched.status_code == 415


def test_native_file_upload_rejects_when_work_disk_is_full(client, monkeypatch):
    import app.routes.api_pending as api_pending

    class Usage:
        free = 1

    monkeypatch.setattr(api_pending.shutil, "disk_usage", lambda _path: Usage())
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("rezept.jpg", _jpeg_bytes(), "image/jpeg")},
    )

    assert response.status_code == 507
    assert "Zu wenig freier Speicher" in response.json()["detail"]


def test_file_import_is_idempotent_across_header_and_form(client, test_db, monkeypatch):
    import app.routes.api_pending as api_pending

    calls = []

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            calls.append(synth_url)
            test_db.pending_add(
                synth_url,
                "recipe",
                ai_suggestion={"name": "Kartoffelsuppe", "filename": attachment["filename"]},
            )
            return {"status": "pending", "name": "Kartoffelsuppe", "url": synth_url}

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    jpeg = _jpeg_bytes()

    first = client.post(
        "/api/pending/import-file",
        headers={"Idempotency-Key": "upload-123"},
        files={"file": ("suppe.jpg", jpeg, "image/jpeg")},
    )
    replay = client.post(
        "/api/pending/import-file",
        data={"client_request_id": "upload-123"},
        files={"file": ("  SUPPE.JPG  ", jpeg, "image/jpeg")},
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["duplicate"] is True
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("second_filename", "second_color", "second_type"),
    [
        ("eins.jpg", "blue", "recipe"),
        ("anderer-name.jpg", "red", "recipe"),
        ("eins.jpg", "red", "wedding"),
    ],
)
def test_file_import_rejects_request_id_reuse_for_other_semantics(
    client,
    test_db,
    monkeypatch,
    second_filename,
    second_color,
    second_type,
):
    import app.routes.api_pending as api_pending

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            test_db.pending_add(synth_url, "recipe", ai_suggestion={"name": "Import"})
            return {"status": "pending", "name": "Import", "url": synth_url}

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    first = client.post(
        "/api/pending/import-file",
        headers={"Idempotency-Key": "same-request"},
        files={"file": ("eins.jpg", _jpeg_bytes("red"), "image/jpeg")},
    )
    conflict = client.post(
        "/api/pending/import-file",
        headers={"Idempotency-Key": "same-request"},
        params={"type": second_type},
        files={"file": (second_filename, _jpeg_bytes(second_color), "image/jpeg")},
    )

    assert first.status_code == 200, first.text
    assert conflict.status_code == 409
    assert "andere Anfrage" in conflict.json()["detail"]


def test_parallel_identical_file_imports_are_processed_once(client, test_db, monkeypatch):
    import app.routes.api_pending as api_pending
    from app.main import app
    from fastapi.testclient import TestClient

    calls = []
    entrants = threading.Barrier(2)
    processing_lock = threading.Lock()

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            calls.append(synth_url)
            test_db.pending_add(synth_url, "recipe", ai_suggestion={"name": "Parallel"})
            return {"status": "pending", "name": "Parallel", "url": synth_url}

    @contextmanager
    def serialized_lock(_name):
        entrants.wait(timeout=5)
        with processing_lock:
            yield object()

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    monkeypatch.setattr(api_pending, "file_lock_or_none", serialized_lock)
    jpeg = _jpeg_bytes("purple")

    def upload():
        # Je Thread ein eigener Client: ein geteilter TestClient serialisiert
        # Requests intern und würde den eigentlichen Parallelfall verdecken.
        return TestClient(app).post(
            "/api/pending/import-file",
            headers={"Idempotency-Key": "parallel-upload"},
            files={"file": ("parallel.jpg", jpeg, "image/jpeg")},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result() for future in (pool.submit(upload), pool.submit(upload))]

    assert [response.status_code for response in responses] == [200, 200]
    assert len(calls) == 1
    assert sorted(response.json()["idempotent_replay"] for response in responses) == [False, True]


def test_file_import_uses_short_content_hash_window_without_request_id(
    client, test_db, monkeypatch
):
    import app.routes.api_pending as api_pending

    calls = []

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            calls.append(synth_url)
            test_db.pending_add(synth_url, "recipe", ai_suggestion={"name": "Hash-Import"})
            return {"status": "pending", "name": "Hash-Import", "url": synth_url}

    @contextmanager
    def available_lock(_name):
        yield object()

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    monkeypatch.setattr(api_pending, "file_lock_or_none", available_lock)
    jpeg = _jpeg_bytes("green")
    first = client.post(
        "/api/pending/import-file",
        files={"file": ("hash.jpg", jpeg, "image/jpeg")},
    )
    replay = client.post(
        "/api/pending/import-file",
        files={"file": ("hash.jpg", jpeg, "image/jpeg")},
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert "force=true" in replay.json()["message"]
    assert len(calls) == 1


def test_pending_file_preview_only_serves_safe_supported_media(
    client, test_db, tmp_path, monkeypatch
):
    import app.routes.api_pending as api_pending

    pending_root = tmp_path / "pending"
    pending_root.mkdir()
    source = pending_root / "scan.jpg"
    source.write_bytes(_jpeg_bytes())
    url = "manual-upload://preview/scan.jpg"
    test_db.pending_add(
        url,
        "recipe",
        video_path=str(source),
        ai_suggestion={"filename": "Crème-Brûlée Überraschung.jpg"},
    )

    class FakeConfig:
        def get(self, section, key=None, default=None):
            if (section, key) == ("paths", "temp_dir"):
                return str(tmp_path)
            return default

    monkeypatch.setattr(api_pending, "get_config", lambda: FakeConfig())
    response = client.get("/api/pending/file", params={"url": url})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "private, no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline; filename*=utf-8''")
    assert "Cr%C3%A8me-Br%C3%BBl%C3%A9e%20%C3%9Cberraschung.jpg" in disposition
    assert response.content == source.read_bytes()


def test_pending_pdf_preview_remains_available(client, test_db, tmp_path, monkeypatch):
    import app.routes.api_pending as api_pending

    pending_root = tmp_path / "pending"
    pending_root.mkdir()
    source = pending_root / "scan.pdf"
    source.write_bytes(_pdf_bytes())
    url = "manual-upload://preview/scan.pdf"
    test_db.pending_add(
        url,
        "recipe",
        video_path=str(source),
        ai_suggestion={"filename": "Mein Scan.pdf"},
    )

    class FakeConfig:
        def get(self, section, key=None, default=None):
            if (section, key) == ("paths", "temp_dir"):
                return str(tmp_path)
            return default

    monkeypatch.setattr(api_pending, "get_config", lambda: FakeConfig())
    response = client.get("/api/pending/file", params={"url": url})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == source.read_bytes()


def test_pending_routes_separate_submission_from_admin_management(client):
    from app.auth import require_admin, require_auth
    from app.routes import api_pending

    routes = {
        (route.path, method): route
        for route in api_pending.router.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith("/api/pending")
    }
    for path in ("/api/pending/import-url", "/api/pending/import-file"):
        calls = {
            dependency.call
            for dependency in routes[(path, "POST")].dependant.dependencies
        }
        assert require_auth in calls
        assert require_admin not in calls

    submission_paths = {"/api/pending/import-url", "/api/pending/import-file"}
    for (path, _method), route in routes.items():
        if path in submission_paths:
            continue
        calls = {
            dependency.call
            for dependency in route.dependant.dependencies
        }
        assert require_auth in calls
        assert require_admin in calls


def test_pending_video_route_is_absent(client):
    from app.routes import api_pending

    assert all(route.path != "/api/pending/video" for route in api_pending.router.routes)
    response = client.get(
        "/api/pending/video",
        params={"url": "manual-upload://legacy/video.mp4"},
    )
    assert response.status_code == 404


def test_failed_download_can_be_retried_and_discarded_with_json_body(client, test_db):
    url = "https://www.tiktok.com/@koch/video/123"
    test_db.download_failure_record(url, "Download fehlgeschlagen")

    retry = client.post("/api/pending/failed/retry", json={"url": url})
    assert retry.status_code == 200
    failed = test_db.download_failures_list()
    assert failed[0]["attempts"] == 0

    discard = client.post("/api/pending/failed/discard", json={"url": url})
    assert discard.status_code == 200
    assert test_db.download_failures_list() == []
    assert test_db.history_get(url)["name"] == "(verworfen)"


def test_pending_image_import_can_be_named_and_saved(test_db, tmp_path):
    source = tmp_path / "pending" / "attachment.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-data")
    url = "manual-upload://abc/attachment.jpg"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="Ein handschriftliches Rezept",
        video_path=str(source),
        ai_suggestion={
            "name": "Unbekannt",
            "type": "Sonstiges",
            "category": "Allgemein",
            "source": "manual-upload",
            "filename": "attachment.jpg",
        },
    )

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.temp_dir = tmp_path / "pending"

    result = job.resolve_pending(
        url,
        {
            "action": "save",
            "name": "Omas Kuchen",
            "type": "Backen",
            "category": "Kuchen",
            "description": "Familienrezept mit Äpfeln",
            "ingredients": [
                {"name": "Äpfel", "amount": 3, "unit": "Stück"},
                {"name": "Mehl", "amount": 250, "unit": "g"},
            ],
            "steps": [{"instruction": "Alles verrühren", "timer_seconds": 300}],
            "servings": 8,
            "verified": True,
        },
    )

    assert result["ok"] is True
    assert not source.exists()
    history = test_db.history_get(url)
    assert history["name"] == "Omas Kuchen"
    target = Path(history["target_dir"])
    assert (target / f"{target.name}.jpg").read_bytes() == b"image-data"
    recipes = test_db.recipe_list(search="Omas Kuchen")
    assert recipes[0]["name"] == "Omas Kuchen"
    recipe_id = recipes[0]["id"]
    saved = test_db.recipe_get(recipe_id)
    assert saved["description"] == "Familienrezept mit Äpfeln"
    assert saved["servings"] == 8
    assert saved["user_verified"] == 1
    assert [item["name"] for item in test_db.recipe_ingredients_get(recipe_id)] == ["Äpfel", "Mehl"]
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Alles verrühren"


def test_pending_editor_rejects_fractional_timer_before_processing(client):
    response = client.post(
        "/api/pending",
        json={
            "url": "manual-upload://missing/test.jpg",
            "action": "save",
            "steps": [{"instruction": "Warten", "timer_seconds": 2.5}],
        },
    )

    assert response.status_code == 422


def test_social_url_validation_uses_exact_hosts_and_single_posts():
    assert normalize_content_url(
        "https://www.tiktok.com/@koch/video/123?utm_source=test#comments"
    ) == "https://www.tiktok.com/@koch/video/123"
    assert normalize_content_url("https://www.instagram.com/reel/ABC123/?igsh=secret") == (
        "https://www.instagram.com/reel/ABC123/"
    )
    assert normalize_content_url("https://vm.tiktok.com/abc123/") == (
        "https://vm.tiktok.com/abc123/"
    )
    assert normalize_content_url("https://instagram.com.evil.example/reel/123") is None
    assert normalize_content_url("https://instagram.com@evil.example/reel/123") is None
    assert normalize_content_url("http://www.instagram.com/reel/123") is None
    assert normalize_content_url("https://www.instagram.com/koch") is None
    assert normalize_content_url("https://www.tiktok.com/@koch") is None


def test_native_social_import_is_visible_before_background_analysis(
    client, test_db, monkeypatch,
):
    from app.routes import api_pending

    queued = {}

    def fake_enqueue(kind, payload, *, dedupe_key=None):
        queued.update(kind=kind, payload=payload, dedupe_key=dedupe_key)
        return 73

    monkeypatch.setattr(api_pending, "enqueue", fake_enqueue)
    response = client.post(
        "/api/pending/import-url",
        json={"url": "https://www.instagram.com/reel/ABC123/?igsh=share", "type": "recipe"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == 73
    assert response.json()["status"] == "pending"
    pending = test_db.pending_get("https://www.instagram.com/reel/ABC123/")
    assert pending["status"] == "pending"
    assert pending["ai_suggestion"]["analysis_state"] == "queued"
    assert queued["kind"] == "share_ingest"
    assert queued["payload"]["url"] == "https://www.instagram.com/reel/ABC123/"


def test_social_metadata_ai_imports_named_incomplete_recipe_without_video(test_db, tmp_path):
    url = "https://www.tiktok.com/@koch/video/987"
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.confidence_threshold = 0.75
    job.downloader = type("NoMediaDownloader", (), {
        "download": lambda *_args: (_ for _ in ()).throw(
            AssertionError("Social-Medien dürfen nicht heruntergeladen werden")
        ),
    })()
    job._fetch_description_via_ytdlp = lambda _url: (
        "Tomatensuppe. Zutaten: 4 Tomaten und Salz. Alles pürieren."
    )
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Tomatensuppe", "Hauptgericht", "Suppe", 0.94,
    )
    job._extract_recipe_data = lambda _text: ExtractedRecipeData(
        ingredients=[{"name": "Tomaten", "amount": 4, "unit": "Stück"}],
        steps=[],
        method="ai",
    )

    result = job.process_url({"url": url, "type": "recipe"})

    assert result["status"] == "auto"
    assert result["needs_manual_care"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["name"] == "Tomatensuppe"
    assert test_db.recipe_ingredients_get(recipe["id"])[0]["name"] == "Tomaten"
    assert test_db.recipe_steps_get(recipe["id"]) == []
    assert recipe["video_filename"] is None


def test_social_metadata_ai_saves_complete_recipe_without_video(test_db, tmp_path):
    url = "https://www.instagram.com/reel/COMPLETE/"
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.confidence_threshold = 0.75
    job.downloader = type("NoMediaDownloader", (), {})()
    job._fetch_description_via_ytdlp = lambda _url: (
        "Pasta. Zutaten: 200 g Nudeln, 4 Tomaten. Nudeln kochen und Tomaten einkochen."
    )
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Schnelle Tomatenpasta", "Hauptgericht", "Pasta", 0.96,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(
        text=text,
        ingredients=[
            {"name": "Nudeln", "canonical_name": "nudeln", "amount": 200, "unit": "g"},
            {"name": "Tomaten", "canonical_name": "tomate", "amount": 4, "unit": "Stück"},
        ],
        steps=[{"instruction": "Nudeln kochen und Tomaten einkochen.", "timer_seconds": None}],
        servings=2,
        method="ai",
    )

    result = job.process_url({"url": url, "type": "recipe"})

    assert result["status"] == "auto"
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["video_filename"] is None
    assert recipe["servings"] == 2
    assert len(test_db.recipe_ingredients_get(recipe["id"])) == 2
    target = Path(result["target"])
    assert not list(target.glob("*.mp4"))
    assert (target / "description.txt").exists()


def test_social_video_frames_and_audio_enrich_first_import(test_db, tmp_path, monkeypatch):
    import app.jobs.scraper as scraper_module

    url = "https://www.tiktok.com/@koch/video/FRAMEAUDIO"
    temp_dir = tmp_path / "temp"

    class Downloader:
        def download(self, _url):
            folder = temp_dir / "downloaded"
            folder.mkdir(parents=True)
            video = folder / "video.mp4"
            video.write_bytes(b"temporary-video")
            return video

    captured = {}

    def fake_video_analysis(_analyzer, video, **kwargs):
        captured.update(video=video, description=kwargs["description"])
        return VideoAnalysisResult(
            content={
                "ingredients": [
                    {"name": "Kartoffel", "amount": 500, "unit": "g"},
                    {"name": "Brühe", "amount": 1, "unit": "l"},
                ],
                "steps": [{"instruction": "Alles 20 Minuten kochen.", "timer_seconds": 1200}],
                "servings": 2,
                "tags": ["suppe"],
            },
            used_video=True,
            frame_text_count=3,
            transcribed=True,
            reason="video_complete",
            evidence_text=(
                "Kartoffelsuppe. EINGEBLENDETER TEXT: 500 g Kartoffeln, 1 l Brühe. "
                "GESPROCHENER TEXT: Alles 20 Minuten kochen."
            ),
        )

    monkeypatch.setattr(scraper_module, "analyze_recipe_video_file", fake_video_analysis)
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.downloader = Downloader()
    job.temp_dir = temp_dir
    job.recipe_dir = tmp_path / "recipes"
    job.confidence_threshold = 0.75
    job.analyzer = object()
    job._fetch_description_via_ytdlp = lambda _url: "Kartoffelsuppe ohne Mengenangaben."
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Kartoffelsuppe", "Hauptgericht", "Suppe", 0.96,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(text=text, method="ai")

    result = job.process_url({"url": url, "type": "recipe"})

    assert result["status"] == "auto"
    assert result["needs_manual_care"] is False
    assert result["video_frames_with_text"] == 3
    assert result["audio_transcribed"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["video_filename"] is None
    assert [item["name"] for item in test_db.recipe_ingredients_get(recipe["id"])] == [
        "Kartoffel", "Brühe",
    ]
    assert test_db.recipe_steps_get(recipe["id"])[0]["timer_seconds"] == 1200
    assert captured["description"].startswith("Kartoffelsuppe")
    assert not captured["video"].parent.exists()


def test_manual_image_is_transcribed_and_structured_immediately(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, _data, _mime, _context):
            return (
                "Kartoffelsuppe für 4 Portionen. Zutaten: 500 g Kartoffeln, 1 l Brühe. "
                "Kartoffeln schneiden. Danach 20 Minuten kochen."
            )

        def analyze_recipe(self, _description):
            return RecipeAnalysis("Kartoffelsuppe", "Hauptgericht", "Suppe", 0.97)

        def analyze_recipe_content(self, _description, **_kwargs):
            return {
                "ingredients": [
                    {"name": "Kartoffel", "amount": 500, "unit": "g", "raw": "500 g Kartoffeln"},
                    {"name": "Brühe", "amount": 1, "unit": "l", "raw": "1 l Brühe"},
                ],
                "steps": [
                    {"instruction": "Kartoffeln schneiden.", "timer_seconds": None},
                    {"instruction": "20 Minuten kochen.", "timer_seconds": 1200},
                ],
                "servings": 4,
                "tags": ["suppe"],
            }

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.temp_dir = tmp_path / "temp"
    job.analyzer = FakeAnalyzer()
    job.analyzer_enabled = True
    job.min_desc_len = 20
    job.confidence_threshold = 0.75
    job.pdf_keep_original = True

    result = job.process_attachment(
        {
            "filename": "kartoffelsuppe.jpg",
            "ext": ".jpg",
            "type": "recipe",
            "source": "manual-upload",
            "data": b"jpeg-placeholder",
            "subject": "kartoffelsuppe",
            "body_excerpt": "",
        },
        "manual-upload://vision/kartoffelsuppe.jpg",
    )

    assert result["status"] == "auto"
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["description"].startswith("Kartoffelsuppe für 4 Portionen")
    assert recipe["servings"] == 4
    assert [item["name"] for item in test_db.recipe_ingredients_get(recipe["id"])] == [
        "Kartoffel", "Brühe",
    ]
    assert len(test_db.recipe_steps_get(recipe["id"])) == 2


def test_image_only_import_saves_named_recipe_for_manual_care(test_db, tmp_path):
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.temp_dir = tmp_path / "temp"
    job.analyzer = object()
    job.analyzer_enabled = True
    job.min_desc_len = 20
    job.confidence_threshold = 0.75
    job.pdf_keep_original = True
    job._extract_recipe_data = lambda text: ExtractedRecipeData(text=text, method="none")
    job._analyze_image_via_openai = lambda *_args, **_kwargs: RecipeAnalysis(
        "Schokoladenkuchen", "Nachspeise", "Kuchen", 0.83,
    )

    result = job.process_attachment(
        {
            "filename": "schokoladenkuchen.jpg",
            "ext": ".jpg",
            "type": "recipe",
            "source": "manual-upload",
            "data": b"food-photo",
            "subject": "Kuchenfoto",
            "body_excerpt": "",
        },
        "manual-upload://image-only/schokoladenkuchen.jpg",
    )

    assert result["status"] == "auto"
    assert result["needs_manual_care"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["name"] == "Schokoladenkuchen"
    assert recipe["video_filename"] is None
    assert test_db.recipe_ingredients_get(recipe["id"]) == []
    assert test_db.recipe_steps_get(recipe["id"]) == []


def test_manual_image_pending_can_be_reanalyzed_with_structured_ai_data(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, _data, _mime, _context):
            return "Zutaten: 500 g Kartoffeln. Kartoffeln schneiden."

    url = "manual-upload://reanalyze/kartoffeln.jpg"
    temp_dir = tmp_path / "temp"
    pending_dir = temp_dir / "pending"
    pending_dir.mkdir(parents=True)
    source = pending_dir / "kartoffeln.jpg"
    source.write_bytes(b"jpeg-placeholder")
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="Unleserlicher Erstversuch",
        video_path=str(source),
        ai_suggestion={
            "name": "Unbekannt",
            "source": "manual-upload",
            "filename": "kartoffeln.jpg",
            "ingredients": [],
            "steps": [],
        },
    )

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.temp_dir = temp_dir
    job.analyzer = FakeAnalyzer()
    job.analyzer_enabled = True
    job.min_desc_len = 20
    job.confidence_threshold = 0.75
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Kartoffelgericht", "Hauptgericht", "Kartoffeln", 0.94,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(
        text=text,
        ingredients=[{"name": "Kartoffel", "amount": 500, "unit": "g"}],
        steps=[],
        method="ai",
    )

    result = job.reanalyze_pending(url)

    assert result["ok"] is True
    assert result["action"] == "still_pending"
    assert result["description"].startswith("Zutaten: 500 g Kartoffeln")
    assert result["analysis"]["ingredients"][0]["name"] == "Kartoffel"
    refreshed = test_db.pending_get(url)
    assert refreshed["video_path"] == str(source.resolve())
    assert refreshed["ai_suggestion"]["ingredients"][0]["amount"] == 500


def test_complete_manual_image_reanalysis_saves_recipe_without_video(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, _data, _mime, _context):
            return "Zutaten: 500 g Kartoffeln. Zubereitung: Kartoffeln 20 Minuten kochen."

    url = "manual-upload://reanalyze/complete.jpg"
    temp_dir = tmp_path / "temp"
    pending_dir = temp_dir / "pending"
    pending_dir.mkdir(parents=True)
    source = pending_dir / "complete.jpg"
    source.write_bytes(b"jpeg-placeholder")
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="Unvollständig",
        video_path=str(source),
        ai_suggestion={
            "name": "Unbekannt",
            "source": "manual-upload",
            "filename": "complete.jpg",
        },
    )

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.temp_dir = temp_dir
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.analyzer = FakeAnalyzer()
    job.analyzer_enabled = True
    job.min_desc_len = 20
    job.confidence_threshold = 0.75
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Kartoffelgericht", "Hauptgericht", "Kartoffeln", 0.94,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(
        text=text,
        ingredients=[{"name": "Kartoffel", "amount": 500, "unit": "g"}],
        steps=[{"instruction": "Kartoffeln 20 Minuten kochen.", "timer_seconds": 1200}],
        servings=2,
        method="ai",
    )

    result = job.reanalyze_pending(url)

    assert result["ok"] is True
    assert result["action"] == "auto_saved"
    assert test_db.pending_get(url)["status"] == "resolved"
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["video_filename"] is None
    assert recipe["thumb_filename"] == "Kartoffelgericht.jpg"
    assert Path(recipe["folder_path"], recipe["thumb_filename"]).read_bytes() == b"jpeg-placeholder"
    assert recipe["servings"] == 2
    assert test_db.recipe_ingredients_get(recipe["id"])[0]["name"] == "Kartoffel"
    assert test_db.recipe_steps_get(recipe["id"])[0]["timer_seconds"] == 1200
    assert not source.exists()


def test_attached_photo_scans_legacy_pending_and_becomes_recipe_image(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, data, mime, context):
            assert data == b"photo-payload"
            assert mime == "image/jpeg"
            assert context == "handschrift.jpg"
            return "Zutaten: 500 g Kartoffeln. Zubereitung: Kartoffeln 20 Minuten kochen."

    url = "legacy://missing-video/photo-rescue"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="Alter unvollständiger Import",
        video_path=str(tmp_path / "temp" / "missing.mp4"),
        ai_suggestion={"name": "Unbekannt", "source": "legacy-video"},
    )
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.temp_dir = tmp_path / "temp"
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.analyzer = FakeAnalyzer()
    job.confidence_threshold = 0.75
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Kartoffelsuppe", "Hauptgericht", "Suppe", 0.97,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(
        text=text,
        ingredients=[{"name": "Kartoffel", "amount": 500, "unit": "g"}],
        steps=[{"instruction": "Kartoffeln 20 Minuten kochen.", "timer_seconds": 1200}],
        servings=2,
        method="ai",
    )

    result = job.attach_pending_photo(
        url,
        b"photo-payload",
        ".jpg",
        "handschrift.jpg",
    )

    assert result["ok"] is True
    assert result["action"] == "auto_saved"
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["thumb_filename"] == "Kartoffelsuppe.jpg"
    assert Path(recipe["folder_path"], recipe["thumb_filename"]).read_bytes() == b"photo-payload"
    assert test_db.recipe_ingredients_get(recipe["id"])[0]["name"] == "Kartoffel"
    assert test_db.recipe_steps_get(recipe["id"])[0]["timer_seconds"] == 1200
    assert test_db.pending_get(url)["status"] == "resolved"


def test_named_incomplete_pending_photo_is_imported_for_manual_care(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, data, mime, context):
            return "Cremige Pilzsuppe"

    url = "legacy://missing-video/named-photo"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        ai_suggestion={"name": "Unbekannt", "source": "legacy-video"},
    )
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.temp_dir = tmp_path / "temp"
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.analyzer = FakeAnalyzer()
    job.confidence_threshold = 0.75
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Cremige Pilzsuppe", "Hauptgericht", "Suppe", 0.91,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(text=text, method="ai")

    result = job.attach_pending_photo(url, b"photo-payload", ".jpg", "pilzsuppe.jpg")

    assert result["ok"] is True
    assert result["action"] == "auto_saved"
    assert result["needs_manual_care"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["name"] == "Cremige Pilzsuppe"
    assert recipe["ingredients_status"] == "pending"
    assert test_db.recipe_ingredients_get(recipe["id"]) == []
    assert test_db.recipe_steps_get(recipe["id"]) == []
    assert test_db.pending_get(url)["status"] == "resolved"


def test_named_dish_photo_without_text_is_imported_for_manual_care(test_db, tmp_path):
    class FakeAnalyzer:
        def extract_description_from_image_bytes(self, data, mime, context):
            return ""

    url = "legacy://missing-video/named-dish-photo"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="Gerichtsfoto ohne eingeblendeten Text",
        ai_suggestion={
            "name": "Räucherlachs-Bagel",
            "type": "Hauptgericht",
            "category": "Fisch",
            "confidence": 0.86,
            "source": "external-link",
        },
    )
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.temp_dir = tmp_path / "temp"
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.analyzer = FakeAnalyzer()
    job.confidence_threshold = 0.75

    result = job.attach_pending_photo(url, b"dish-photo", ".jpg", "bagel.jpg")

    assert result["action"] == "auto_saved"
    assert result["needs_manual_care"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["name"] == "Räucherlachs-Bagel"
    assert recipe["thumb_filename"] == "Räucherlachs-Bagel.jpg"
    assert Path(recipe["folder_path"], recipe["thumb_filename"]).read_bytes() == b"dish-photo"
    assert test_db.recipe_ingredients_get(recipe["id"]) == []
    assert test_db.recipe_steps_get(recipe["id"]) == []
    assert test_db.pending_get(url)["status"] == "resolved"


def test_social_reanalysis_preserves_cover_and_is_idempotent(test_db, tmp_path):
    url = "https://www.tiktok.com/@koch/video/cover123"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        ai_suggestion={
            "name": "TikTok-Rezept prüfen",
            "source": "external-link",
            "platform": "TikTok",
        },
    )

    class MetadataOnlyDownloader:
        def refresh_metadata(self, _url):
            return {
                "description_text": "Tomatenpasta mit vier Tomaten. Tomaten einkochen.",
                "thumbnail_bytes": b"jpeg-thumbnail",
                "thumbnail_suffix": ".jpg",
            }

        def download(self, _url):
            return None

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.downloader = MetadataOnlyDownloader()
    job.temp_dir = tmp_path / "temp"
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.confidence_threshold = 0.75
    job._analyze_recipe = lambda _text: RecipeAnalysis(
        "Tomatenpasta", "Hauptgericht", "Pasta", 0.96,
    )
    job._extract_recipe_data = lambda text: ExtractedRecipeData(
        text=text,
        ingredients=[{"name": "Tomaten", "amount": 4, "unit": "Stück"}],
        steps=[],
        method="ai",
    )

    refreshed = job.reanalyze_pending(url)

    assert refreshed["action"] == "auto_saved"
    assert refreshed["needs_manual_care"] is True
    recipe = test_db.recipe_get(refreshed["recipe_id"])
    assert recipe["thumb_filename"] == "Tomatenpasta.jpg"
    assert Path(recipe["folder_path"], recipe["thumb_filename"]).read_bytes() == b"jpeg-thumbnail"
    assert recipe["video_filename"] is None
    assert test_db.recipe_steps_get(recipe["id"]) == []
    assert test_db.pending_get(url)["status"] == "resolved"

    repeated = job.reanalyze_pending(url)
    assert repeated["action"] == "already_saved"
    with test_db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recipes WHERE url=?", (url,)).fetchone()[0] == 1


def test_legacy_social_pending_without_source_uses_link_reanalysis(test_db):
    url = "https://vm.tiktok.com/ZLegacyPending/"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="ich liebe dieses Gericht #rezept",
        ai_suggestion={
            "name": "Unbekannt",
            "type": "Hauptgericht",
            "category": "Fleisch",
            "confidence": 0.85,
        },
    )
    job = object.__new__(ScraperJob)
    job.db = test_db
    calls = []

    def process_url(item):
        calls.append(item)
        return {"status": "pending", "message": "Noch unvollständig"}

    job.process_url = process_url

    result = job.reanalyze_pending(url)

    assert result == {
        "ok": True,
        "action": "still_pending",
        "analysis": {
            "name": "Unbekannt",
            "type": "Hauptgericht",
            "category": "Fleisch",
            "confidence": 0.85,
            "source": "external-link",
            "platform": "TikTok",
        },
        "description": "ich liebe dieses Gericht #rezept",
        "message": "Noch unvollständig",
    }
    assert calls == [{"url": url, "type": "recipe", "reanalyze_existing": True}]
    assert test_db.pending_get(url)["ai_suggestion"]["source"] == "external-link"


def test_cart_add_endpoint_returns_json(client):
    response = client.post("/api/cart/add", json={"name": "Kartoffel"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True


def test_social_import_without_analyzer_stays_pending_and_clears_old_failure(test_db):
    class FailingDownloader:
        def download(self, _url):
            raise AssertionError("Social-Medien dürfen nicht heruntergeladen werden")

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.downloader = FailingDownloader()
    test_db.download_failure_record(
        "https://www.tiktok.com/@koch/video/123",
        "Historischer Downloadfehler",
    )

    result = job.process_url({
        "url": "https://www.tiktok.com/@koch/video/123?share=1",
        "type": "recipe",
    })

    assert result["status"] == "pending"
    assert result["platform"] == "TikTok"
    pending = test_db.pending_get("https://www.tiktok.com/@koch/video/123")
    assert pending["video_path"] is None
    assert pending["ai_suggestion"]["source"] == "external-link"
    assert test_db.download_failures_list() == []


def test_pending_social_link_can_be_completed_without_media(test_db, tmp_path):
    url = "https://www.instagram.com/reel/ABC123/"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        ai_suggestion={
            "name": "Instagram-Rezept prüfen",
            "type": "Sonstiges",
            "category": "Allgemein",
            "source": "external-link",
            "platform": "Instagram",
        },
    )

    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.temp_dir = tmp_path / "pending"

    result = job.resolve_pending(
        url,
        {
            "action": "save",
            "name": "Schnelle Tomatenpasta",
            "type": "Hauptgericht",
            "category": "Pasta",
            "description": "Quelle bleibt bei Instagram.",
            "ingredients": [{"name": "Tomaten", "amount": 4, "unit": "Stück"}],
            "steps": [{"instruction": "Tomaten einkochen"}],
            "verified": True,
        },
    )

    assert result["ok"] is True
    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["url"] == url
    assert recipe["video_filename"] is None
    assert Path(recipe["folder_path"], "info.json").is_file()
    assert test_db.pending_get(url)["status"] == "resolved"


def test_empty_native_arrays_preserve_extracted_ingredients_and_steps(test_db, tmp_path):
    recipe_id = test_db.recipe_upsert(
        url="manual-upload://preserve/extracted.pdf",
        name="Extrahiertes Rezept",
        type="Hauptgericht",
        category="Allgemein",
        folder_path=str(tmp_path / "recipe"),
        description="Extrahierter Quelltext",
        thumb_filename=None,
        video_filename=None,
        source_added_at=None,
    )
    test_db.recipe_set_extraction_result(
        recipe_id,
        status="ok",
        ingredients=[{
            "name": "Tomaten",
            "canonical_name": "tomate",
            "amount": 4,
            "unit": "Stück",
            "raw": "4 Tomaten",
        }],
    )
    test_db.recipe_steps_set(
        recipe_id,
        [{"instruction": "Tomaten einkochen", "timer_seconds": 600}],
    )

    job = object.__new__(ScraperJob)
    job.db = test_db
    job._apply_pending_manual_data(
        recipe_id,
        {"ingredients": [], "steps": [], "servings": None, "verified": False},
    )

    assert [item["name"] for item in test_db.recipe_ingredients_get(recipe_id)] == ["Tomaten"]
    assert test_db.recipe_steps_get(recipe_id)[0]["instruction"] == "Tomaten einkochen"


def test_empty_native_description_preserves_pending_source_text(test_db, tmp_path):
    source = tmp_path / "pending" / "scan.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-data")
    url = "manual-upload://preserve/scan.jpg"
    test_db.pending_add(
        url=url,
        content_type="recipe",
        description="OCR-Quelltext mit Zutaten",
        video_path=str(source),
        ai_suggestion={
            "name": "Scan prüfen",
            "type": "Hauptgericht",
            "category": "Allgemein",
            "source": "manual-upload",
            "filename": "scan.jpg",
        },
    )
    job = object.__new__(ScraperJob)
    job.db = test_db
    job.recipe_dir = tmp_path / "recipes"
    job.wedding_dir = tmp_path / "wedding"
    job.temp_dir = tmp_path / "pending"

    result = job.resolve_pending(
        url,
        {
            "action": "save",
            "name": "Geretteter Scan",
            "type": "Hauptgericht",
            "category": "Allgemein",
            "description": "",
            "ingredients": [],
            "steps": [],
            "verified": False,
        },
    )

    recipe = test_db.recipe_get(result["recipe_id"])
    assert recipe["description"] == "OCR-Quelltext mit Zutaten"
    assert Path(recipe["folder_path"], "description.txt").read_text(encoding="utf-8") == (
        "OCR-Quelltext mit Zutaten"
    )


def test_manual_pending_ingredients_refresh_diet_tags(test_db, tmp_path):
    recipe_id = test_db.recipe_upsert(
        url="manual-upload://tags/recipe.jpg",
        name="Tag-Rezept",
        type="Hauptgericht",
        category="Allgemein",
        folder_path=str(tmp_path / "recipe"),
        description="",
        thumb_filename=None,
        video_filename=None,
        source_added_at=None,
    )
    test_db.recipe_auto_tags_set(recipe_id, ["schnell"])
    job = object.__new__(ScraperJob)
    job.db = test_db

    job._apply_pending_manual_data(
        recipe_id,
        {
            "ingredients": [
                {"name": "Tomate", "amount": 2, "unit": "Stück"},
                {"name": "Zwiebel", "amount": 1, "unit": "Stück"},
            ],
            "steps": [],
            "verified": False,
        },
    )

    tags = {tag["name"] for tag in test_db.recipe_tags_get(recipe_id)}
    assert tags == {"schnell", "vegan"}
