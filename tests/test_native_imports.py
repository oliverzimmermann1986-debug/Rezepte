from pathlib import Path

from app.core.email_processor import normalize_content_url
from app.jobs.scraper import ScraperJob


def test_native_file_upload_uses_attachment_pipeline(client, monkeypatch):
    import app.routes.api_pending as api_pending

    captured = {}

    class FakeJob:
        def process_attachment(self, attachment, synth_url):
            captured["attachment"] = attachment
            captured["url"] = synth_url
            return {"status": "pending", "name": "Unbekannt"}

    monkeypatch.setattr(api_pending, "get_scraper_job", lambda: FakeJob())
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("Mein_Rezept.jpg", b"\xff\xd8\xffjpeg-data", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["ok"] is True
    assert captured["attachment"]["ext"] == ".jpg"
    assert captured["attachment"]["data"] == b"\xff\xd8\xffjpeg-data"
    assert captured["url"].startswith("manual-upload://")


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


def test_social_import_is_link_only_and_never_calls_downloader(test_db):
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
