"""Real-session authorization before imports, AI calls and their side effects."""
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app import auth, import_budget, tenancy
from app.config_store import get_config
from app.core import downloader
from app.jobs import task_queue
from app.recipes import image_generation, indexer
from app.routes import api_pending, api_recipes, api_share, api_shopping
from tests.test_tenants import households as households, _recipe


OPERATIONS = [
    ("/api/pending/import-url", {"url": "https://recipes.example/new"}, False, tenancy, "import_database"),
    ("/api/pending/import-file", None, True, api_pending, "_validate_upload_payload"),
    ("/api/pending/scan-photo?url=https://recipes.example/pending", None, True, api_pending, "_validate_upload_payload"),
    ("/api/pending/reanalyze", {"url": "https://recipes.example/pending"}, False, api_pending, "_pending_import"),
    ("/api/pending", {"url": "https://recipes.example/pending", "action": "save", "name": "Manual"}, False, api_pending, "_pending_import"),
    ("/api/recipes/{id}/generate-image", {}, False, image_generation, "ensure_image_generation_configured"),
    ("/api/recipes/{id}/translate", {"target_language": "de", "text": "Recipe text"}, False, api_recipes, "build_analyzer"),
    ("/api/recipes/{id}/nutrition", {}, False, api_recipes, "build_analyzer"),
    ("/api/recipes/{id}/extract", {}, False, api_recipes, "build_analyzer"),
    ("/api/recipes/{id}/rescrape", {}, False, downloader, "VideoDownloader"),
    ("/api/cart/optimize/preview", {}, False, api_shopping, "build_analyzer"),
]


def _post(client, path, payload, upload, recipe_id):
    if upload:
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
        return client.post(path, data={"ai_processing_consent": "openai-recipe-v1"}, files={"file": ("recipe.jpg", buffer.getvalue(), "image/jpeg")})
    return client.post(path.format(id=recipe_id), json={**(payload or {}), "ai_processing_consent": "openai-recipe-v1"})


def _files():
    result = {}
    for key in ("recipe_dir", "temp_dir"):
        root = Path(get_config().get("paths", key))
        result[key] = {str(path.relative_to(root)): path.stat().st_size for path in root.rglob("*") if path.is_file()}
    return result


def _work_state(db):
    with db.conn() as connection:
        return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("background_tasks", "pending", "import_budget_usage", "recipe_versions")}


@pytest.mark.parametrize("method", ["password", "apple", "google"])
@pytest.mark.parametrize("operation", OPERATIONS, ids=[row[0] for row in OPERATIONS])
def test_user_cannot_start_import_or_ai_before_any_work(households, monkeypatch, method, operation):
    client, db, users, _ = households
    rid = _recipe(db, "PermissionRecipe", "https://www.tiktok.com/@cook/video/7123456789012345678", owner=users["anna"][1])
    client.headers["Authorization"] = "Bearer " + auth.create_session("anna", auth_method=method)

    def forbidden(*args, **kwargs):
        pytest.fail("A denied operation reached an AI, import, network or file-processing entry point")

    for module, attribute in {(row[3], row[4]) for row in OPERATIONS}:
        monkeypatch.setattr(module, attribute, forbidden)
    for module, attribute in ((api_pending, "enqueue"), (task_queue, "enqueue"),
                              (api_pending, "get_scraper_job"), (import_budget, "reserve_import")):
        monkeypatch.setattr(module, attribute, forbidden)
    files, work = _files(), _work_state(db)
    response = _post(client, *operation[:3], rid)
    assert response.status_code == 403, response.text
    expected_permission = "Importberechtigung" if operation[0].startswith("/api/pending") else "Administratorrechte"
    assert expected_permission in response.json()["detail"]
    assert _files() == files and _work_state(db) == work


@pytest.mark.parametrize("operation", OPERATIONS, ids=[row[0] for row in OPERATIONS])
def test_admin_reaches_the_authorized_operation(households, monkeypatch, operation):
    client, db, users, login = households
    rid = _recipe(db, "AdminPermissionRecipe", "https://www.tiktok.com/@cook/video/7123456789012345678", owner=users["operator"][1])
    db.recipe_set_extraction_result(rid, status="ok", ingredients=[
        {"name": name, "canonical_name": name.lower(), "amount": 100, "unit": "g"}
        for name in ("Hafer", "Milch", "Zucker")
    ])
    login("operator")
    assert client.post("/api/cart/add", json={"name": "Milch", "amount": 1, "unit": "l"}).status_code == 200
    reached = []

    def probe(*args, **kwargs):
        reached.append(True)
        raise HTTPException(418, "Synthetic authorization probe; no external work")

    monkeypatch.setattr(operation[3], operation[4], probe)
    response = _post(client, *operation[:3], rid)
    assert reached == [True], response.text
    # Some established handlers wrap setup errors as 500; both outcomes prove
    # the real admin authorization passed before the synthetic work boundary.
    assert response.status_code in {418, 500}


@pytest.mark.parametrize("username", ["anna", "guest", "operator"])
def test_recipe_reads_never_start_background_extraction(households, monkeypatch, username):
    client, _, _, login = households
    calls = []
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", lambda: calls.append("extract"))
    monkeypatch.setattr(indexer, "sync_filesystem", lambda *args: pytest.fail("A read started filesystem import"))
    login(username)
    assert client.get("/api/recipes").status_code == 200
    assert calls == []


def assert_provider_user_can_edit_but_cannot_start_jobs(client, db, username):
    """Shared assertion after actual signed Apple/Google callback and exchange."""
    from app import accounts

    user = db.user_get_by_name(username)
    assert user["role"] == "user"
    account_id = accounts.view(db, user["id"])["id"]
    rid = _recipe(db, "ProviderManualRecipe", "https://recipes.example/provider-manual", owner=account_id)
    response = client.put(f"/api/recipes/{rid}/steps", json={"steps": [{"instruction": "Manuell geändert."}]})
    assert response.status_code == 200, response.text
    assert db.recipe_steps_get(rid)[0]["instruction"] == "Manuell geändert."
    assert len(db.recipe_versions_list(recipe_id=rid)) == 1
    assert client.get(f"/api/recipes/{rid}").json()["can_edit"] is True
    assert client.get("/api/account/imports").status_code == 403
    for path, payload in ((f"/api/recipes/{rid}/generate-image", {}),
                          (f"/api/recipes/{rid}/translate", {"target_language": "de", "text": "Test"}),
                          ("/api/pending/import-url", {"url": "https://recipes.example/new"})):
        assert client.post(path, json=payload).status_code == 403
    assert client.post("/api/jobs/scraper/run", json={}).status_code == 404
    assert client.get("/api/users").status_code == 403
    assert db.user_get_by_name(username)["role"] == "user"


def test_user_session_does_not_replace_a_machine_import_token(households, monkeypatch):
    client, db, _, login = households
    login("anna")
    monkeypatch.setattr(api_share, "_share_enabled", lambda: True)
    monkeypatch.setattr(api_share, "enqueue", lambda *args, **kwargs: pytest.fail("Ordinary session started machine import"))
    response = client.post("/api/share", json={"ai_processing_consent": "openai-recipe-v1", "url": "https://recipes.example/machine-import"})
    assert response.status_code == 401
    assert _work_state(db) == {"background_tasks": [], "pending": [], "import_budget_usage": [], "recipe_versions": []}
