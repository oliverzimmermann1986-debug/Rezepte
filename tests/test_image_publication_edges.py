"""Image rollback and fallbacks must respect process boundaries and drafts."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import threading
import time

from PIL import Image
import pytest

from app.recipes import image_generation, image_cache
from app import auth
from app.routes import api_recipes
from app.recipes.image_publish import publish_image
from tests.test_tenants import _recipe, households  # noqa: F401


@pytest.mark.parametrize("width", [None, 400])
def test_thumbnail_fallback_does_not_publish_hidden_draft_or_rollback(households, width):
    client, db, _, login = households
    rid = _recipe(db, "HiddenDraft", "https://recipes.example/hidden-draft")
    folder = Path(db.recipe_get(rid)["folder_path"])
    Image.new("RGB", (20, 20), "red").save(folder / ".generated-uncommitted.jpg")
    Image.new("RGB", (20, 20), "blue").save(folder / ".thumb-rollback-retained.jpg")
    login("guest")
    response = client.get(f"/api/recipes/{rid}/thumb", params={} if width is None else {"w": width})
    assert response.status_code == 404, "An uncommitted draft must not appear as the recipe cover"


def test_original_backup_does_not_promote_hidden_draft(households):
    _, db, _, _ = households
    rid = _recipe(db, "HiddenBackup", "https://recipes.example/hidden-backup")
    folder = Path(db.recipe_get(rid)["folder_path"])
    Image.new("RGB", (20, 20), "red").save(folder / ".generated-uncommitted.jpg")
    assert image_generation.backup_recipe_image(db.recipe_get(rid), "hidden-draft-backup") is None
    assert db.recipe_image_backup_list(rid) == []


def test_failed_publication_cannot_undo_another_process_commit(tmp_path):
    target, failed, successful = (tmp_path / name for name in ("thumb.jpg", "failed.jpg", "successful.jpg"))
    target.write_bytes(b"original")
    failed.write_bytes(b"failed-image")
    successful.write_bytes(b"successful-image")
    code = ("from pathlib import Path\nimport sys\nfrom app.recipes.image_publish import publish_image\n"
            "try:\n with publish_image(Path(sys.argv[1]),Path(sys.argv[2])):\n"
            "  print('published',flush=True)\n  sys.stdin.readline()\n"
            "  raise RuntimeError('synthetic database commit failure')\n"
            "except RuntimeError:\n print('rolled-back',flush=True)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(failed), str(target)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = threading.Event()
    def commit():
        started.set()
        with publish_image(successful, target):
            pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert child.stdout.readline().strip() == "published"
            future = pool.submit(commit)
            assert started.wait(5)
            time.sleep(0.3)
            blocked_until_rollback = not future.done()
        finally:
            stdout, stderr = child.communicate("release\n", timeout=10)
        assert child.returncode == 0, stderr
        assert "rolled-back" in stdout
        future.result(timeout=10)
    assert target.read_bytes() == b"successful-image", "A failed process must not roll back another process's committed image"
    assert blocked_until_rollback, "Publication must wait for another process's compensation to finish"


def test_image_upload_does_not_block_other_http_requests(households, monkeypatch):
    client, db, users, _ = households
    rid = _recipe(db, "ResponsiveUpload", "https://recipes.example/responsive-upload", owner=users["anna"][1])
    entered, release = threading.Event(), threading.Event()
    normalize = api_recipes.normalize_image

    def pause_normalization(*args, **values):
        entered.set()
        assert release.wait(10)
        return normalize(*args, **values)

    monkeypatch.setattr(api_recipes, "normalize_image", pause_normalization)
    data = BytesIO()
    Image.new("RGB", (20, 20), "red").save(data, format="JPEG")
    with ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(client.post, f"/api/recipes/{rid}/upload-thumbnail",
                             headers={"Authorization": "Bearer " + auth.create_session("anna")},
                             files={"file": ("cover.jpg", data.getvalue(), "image/jpeg")})
        try:
            assert entered.wait(5)
            health = pool.submit(client.get, "/healthz")
            try:
                response = health.result(timeout=1)
                responsive = response.status_code == 200
            except FutureTimeout:
                responsive = False
        finally:
            release.set()
        assert upload.result(timeout=10).status_code == 200
        assert health.result(timeout=5).status_code == 200
    assert responsive, "Image decoding must run outside the HTTP event loop"


def test_visible_cover_and_backup_remain_available_next_to_hidden_drafts(households):
    client, db, _, login = households
    rid = _recipe(db, "VisibleFallback", "https://recipes.example/visible-fallback")
    folder = Path(db.recipe_get(rid)["folder_path"])
    visible = folder / "original.jpg"
    Image.new("RGB", (20, 20), "red").save(visible)
    Image.new("RGB", (20, 20), "blue").save(folder / ".generated-uncommitted.jpg")
    login("guest")
    response = client.get(f"/api/recipes/{rid}/thumb?w=400")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        red, _, blue = image.getpixel((0, 0))
        assert red > blue
    backup_id = image_generation.backup_recipe_image(db.recipe_get(rid), "visible-fallback-backup")
    assert db.recipe_image_backup_get(backup_id)["original_filename"] == "original.jpg"


def test_hidden_pdf_draft_is_not_rendered_as_a_cover(households, monkeypatch):
    client, db, _, login = households
    rid = _recipe(db, "HiddenPDF", "https://recipes.example/hidden-pdf")
    (Path(db.recipe_get(rid)["folder_path"]) / ".upload-draft.pdf").write_bytes(b"%PDF-synthetic")
    monkeypatch.setattr(api_recipes, "ensure_pdf_first_page", lambda *args, **values: pytest.fail("Do not render an unpublished PDF"))
    login("guest")
    assert client.get(f"/api/recipes/{rid}/thumb").status_code == 404


def test_publication_lock_is_reentrant_and_released_after_failure(tmp_path):
    with pytest.raises(ValueError, match="synthetic"):
        with image_cache.image_publication_lock(tmp_path, wait_seconds=0.1):
            with image_cache.image_publication_lock(tmp_path, wait_seconds=0.1):
                raise ValueError("synthetic failure")
    with image_cache.image_publication_lock(tmp_path, wait_seconds=0.1):
        pass


def test_busy_publication_does_not_touch_the_existing_image(tmp_path, monkeypatch):
    target, staged = tmp_path / "thumb.jpg", tmp_path / "new.jpg"
    target.write_bytes(b"original")
    staged.write_bytes(b"new")
    calls = []
    @contextmanager
    def refused(*args, **values):
        calls.append(True)
        yield None if len(calls) == 1 else object()
    monkeypatch.setattr(image_cache, "file_lock_path_or_none", refused)
    with pytest.raises(image_cache.ImagePublicationBusyError):
        with publish_image(staged, target):
            pytest.fail("No publication without the process lock")
    assert target.read_bytes() == b"original" and staged.read_bytes() == b"new"
    with publish_image(staged, target):
        pass
    assert len(calls) == 2 and target.read_bytes() == b"new"
