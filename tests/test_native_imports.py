from pathlib import Path

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
        files={"file": ("Mein_Rezept.jpg", b"jpeg-data", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["ok"] is True
    assert captured["attachment"]["ext"] == ".jpg"
    assert captured["attachment"]["data"] == b"jpeg-data"
    assert captured["url"].startswith("manual-upload://")


def test_native_file_upload_rejects_unsupported_type(client):
    response = client.post(
        "/api/pending/import-file",
        files={"file": ("rezept.txt", b"text", "text/plain")},
    )
    assert response.status_code == 415


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
        {"action": "save", "name": "Omas Kuchen", "type": "Backen", "category": "Kuchen"},
    )

    assert result["ok"] is True
    assert not source.exists()
    history = test_db.history_get(url)
    assert history["name"] == "Omas Kuchen"
    target = Path(history["target_dir"])
    assert (target / f"{target.name}.jpg").read_bytes() == b"image-data"
    recipes = test_db.recipe_list(search="Omas Kuchen")
    assert recipes[0]["name"] == "Omas Kuchen"
