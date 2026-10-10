"""Action permission through real routes and the persistent queue, no external work."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from io import BytesIO
from types import SimpleNamespace

from fastapi import HTTPException
from PIL import Image
import pytest

from app import ai_consent, import_budget
from app.db import Database
from app.jobs import task_queue
from app.recipes import image_generation
from app.routes import api_admin, api_audit, api_history, api_pending, api_recipes, api_share, api_shopping
from tests.test_tenants import households as households, _recipe
from app.tenant_db import HouseholdDatabase
from app.tenancy import CURRENT_HOUSEHOLD, HouseholdScope, household_context


CONSENT = ai_consent.CONSENT_VERSION


class ProviderConfig:
    base_url = "https://api.openai.com/v1"

    def get(self, *keys, default=None):
        return self.base_url if keys == ("ai", "openai", "base_url") else default


@pytest.fixture(autouse=True)
def isolated_provider_and_workers(monkeypatch):
    """Disable only worker threads, never add consent or bypass its dependencies."""
    provider = ProviderConfig()
    monkeypatch.setattr(ai_consent, "get_config", lambda: provider)
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)
    yield provider
    assert ai_consent.CURRENT_AI_CONSENT.get() is None


@pytest.fixture
def admin(households):
    client, db, users, login = households
    rid = _recipe(db, "ConsentProbe", "https://recipes.example/consent-probe", owner=users["operator"][1])
    login("operator")
    return client, db, rid


def forbidden(*args, **kwargs):
    pytest.fail("Work was reached before explicit AI permission")


def forbid_work(monkeypatch):
    for module, name in (
        (api_pending, "enqueue"), (api_pending, "get_scraper_job"),
        (api_pending, "_validate_upload_payload"), (api_pending, "_pending_import"),
        (api_recipes, "build_analyzer"), (api_recipes, "ensure_extraction_running"),
        (task_queue, "enqueue"), (image_generation, "ensure_image_generation_configured"),
        (api_shopping, "build_analyzer"), (api_history, "get_scraper_job"),
        (api_admin, "_pdf_preflight"), (api_audit, "run_audit"),
        (api_audit, "_openai_config_for_audit"), (api_share, "enqueue"),
        (import_budget, "reserve_import"),
    ):
        monkeypatch.setattr(module, name, forbidden)


def work_state(db):
    with db.conn() as connection:
        return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("background_tasks", "pending", "import_budget_usage", "recipe_versions")}


JSON_ACTIONS = [
    ("/api/pending/import-url", {"url": "https://recipes.example/new"}),
    ("/api/pending/reanalyze", {"url": "https://recipes.example/pending"}),
    ("/api/pending/reanalyze-all", {}),
    ("/api/pending", {"url": "https://recipes.example/pending", "action": "save", "name": "Synthetic"}),
    ("/api/recipes/{id}/generate-image", {}),
    ("/api/recipes/images/backfill", {}),
    ("/api/recipes/{id}/translate", {"target_language": "de", "text": "Synthetic recipe"}),
    ("/api/recipes/{id}/nutrition", {}),
    ("/api/recipes/compute-nutrition-bulk", {}),
    ("/api/recipes/{id}/extract", {}),
    ("/api/recipes/{id}/rescrape", {}),
    ("/api/recipes/recover-empty", {}),
    ("/api/history/reanalyze", {"url": "https://recipes.example/pending", "dry_run": True}),
    ("/api/history/reanalyze-all", {"dry_run": True}),
    ("/api/cart/optimize/preview", {}),
    ("/api/audit/ai-sanity", {}),
    ("/api/admin/pdf/process", {"dry_run": True}),
]


@pytest.mark.parametrize("path,payload", JSON_ACTIONS, ids=[row[0] for row in JSON_ACTIONS])
@pytest.mark.parametrize("marker", [None, "wrong-version"])
def test_json_action_without_matching_permission_stops_before_work(admin, monkeypatch, path, payload, marker):
    client, db, rid = admin
    forbid_work(monkeypatch)
    before = work_state(db)
    body = dict(payload)
    if marker is not None:
        body["ai_processing_consent"] = marker
    response = client.post(path.format(id=rid), json=body)
    assert response.status_code == 428, response.text
    assert response.json()["detail"]["code"] == "AI_CONSENT_REQUIRED"
    assert work_state(db) == before


@pytest.mark.parametrize("marker", [True, 1, [], {}, "", " openai-recipe-v1", "openai-recipe-v1 "])
def test_only_the_exact_consent_value_is_accepted(admin, marker, monkeypatch):
    client, _, _ = admin
    forbid_work(monkeypatch)
    response = client.post("/api/pending/import-url", json={
        "url": "https://recipes.example/new", "ai_processing_consent": marker,
    })
    assert response.status_code == 428, response.text


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.mark.parametrize("path", ["/api/pending/import-file", "/api/pending/scan-photo?url=https://recipes.example/pending"])
@pytest.mark.parametrize("marker", [None, "wrong-version", CONSENT])
def test_multipart_permission_survives_form_parsing_and_precedes_file_processing(admin, monkeypatch, path, marker):
    client, _, _ = admin
    reached = []

    def validate(*args):
        reached.append(ai_consent.CURRENT_AI_CONSENT.get())
        raise HTTPException(418, "Synthetic file boundary")

    monkeypatch.setattr(api_pending, "_validate_upload_payload", validate)
    monkeypatch.setattr(api_pending, "get_scraper_job", forbidden)
    response = client.post(path, data={} if marker is None else {"ai_processing_consent": marker},
                           files={"file": ("recipe.jpg", jpeg(), "image/jpeg")})
    assert response.status_code == (418 if marker == CONSENT else 428), response.text
    assert reached == ([CONSENT] if marker == CONSENT else [])


def test_duplicate_multipart_permission_is_rejected(admin, monkeypatch):
    client, _, _ = admin
    monkeypatch.setattr(api_pending, "_validate_upload_payload", forbidden)
    response = client.post("/api/pending/import-file", files=[
        ("file", ("recipe.jpg", jpeg(), "image/jpeg")),
        ("ai_processing_consent", (None, CONSENT)),
        ("ai_processing_consent", (None, CONSENT)),
    ])
    assert response.status_code == 428, response.text


@pytest.mark.parametrize("base_url", [
    "https://other.example/v1", "http://api.openai.com/v1", "https://api.openai.com.evil.example/v1",
    "https://api.openai.com/v1?destination=other", "https://account@api.openai.com/v1",
    "https://api.openai.com:444/v1", "https://api.openai.com/other",
])
def test_openai_permission_does_not_authorize_another_provider(admin, monkeypatch, isolated_provider_and_workers, base_url):
    client, db, _ = admin
    isolated_provider_and_workers.base_url = base_url
    forbid_work(monkeypatch)
    before = work_state(db)
    response = client.post("/api/pending/import-url", json={
        "url": "https://recipes.example/new", "ai_processing_consent": CONSENT,
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "AI_PROVIDER_UNSUPPORTED"
    assert work_state(db) == before


@pytest.mark.parametrize("action,marker,expected", [("save", None, 428), ("save", CONSENT, 418), ("skip", None, 418)])
def test_pending_save_needs_permission_but_skip_remains_local(admin, monkeypatch, action, marker, expected):
    client, _, _ = admin
    seen = []

    def pending(*args):
        seen.append(ai_consent.CURRENT_AI_CONSENT.get())
        raise HTTPException(418, "Synthetic pending boundary")

    monkeypatch.setattr(api_pending, "_pending_import", pending)
    payload = {"url": "https://recipes.example/pending", "action": action, "name": "Synthetic"}
    if marker:
        payload["ai_processing_consent"] = marker
    response = client.post("/api/pending", json=payload)
    assert response.status_code == expected, response.text
    assert seen == ([] if expected == 428 else [marker])


@pytest.mark.parametrize("extract,marker,expected", [
    (None, None, 428), (True, None, 428), (True, CONSENT, 418),
    (False, None, 418), ("false", None, 418), ("f", None, 418), ("n", None, 418),
])
def test_pdf_default_extraction_and_dry_run_need_permission_but_local_processing_does_not(admin, monkeypatch, extract, marker, expected):
    client, _, _ = admin
    seen = []

    def preflight(**kwargs):
        seen.append(ai_consent.CURRENT_AI_CONSENT.get())
        raise HTTPException(418, "Synthetic PDF boundary")

    monkeypatch.setattr(api_admin, "_pdf_preflight", preflight)
    payload = {"dry_run": True}
    if extract is not None:
        payload["extract_recipe_data"] = extract
    if marker:
        payload["ai_processing_consent"] = marker
    response = client.post("/api/admin/pdf/process", json=payload)
    assert response.status_code == expected, response.text
    assert seen == ([] if expected == 428 else [marker])


@pytest.mark.parametrize("with_ai,marker,expected", [(True, None, 428), (True, CONSENT, 418), (False, None, 418)])
def test_audit_get_checks_permission_only_when_ai_requested(admin, monkeypatch, with_ai, marker, expected):
    client, _, _ = admin
    seen = []

    def audit(*args, **kwargs):
        seen.append(ai_consent.CURRENT_AI_CONSENT.get())
        raise HTTPException(418, "Synthetic audit boundary")

    monkeypatch.setattr(api_audit, "run_audit", audit)
    monkeypatch.setattr(api_audit, "_openai_config_for_audit", lambda: {})
    params = {"with_ai": str(with_ai).lower(), "refresh": "true"}
    if marker:
        params["ai_processing_consent"] = marker
    response = client.get("/api/audit", params=params)
    assert response.status_code == expected, response.text
    assert seen == ([] if expected == 428 else [marker])


@pytest.mark.parametrize("with_ai", ["t", "y", "TRUE", "on", "1"])
def test_audit_boolean_aliases_cannot_bypass_permission(admin, monkeypatch, with_ai):
    client, _, _ = admin
    monkeypatch.setattr(api_audit, "run_audit", forbidden)
    response = client.get("/api/audit", params={"with_ai": with_ai, "refresh": "true"})
    assert response.status_code == 428, response.text


@pytest.mark.parametrize("values", [(CONSENT, CONSENT), ("wrong", CONSENT), (CONSENT, "wrong")])
def test_duplicate_audit_query_permission_is_rejected(admin, monkeypatch, values):
    client, _, _ = admin
    monkeypatch.setattr(api_audit, "run_audit", forbidden)
    response = client.get("/api/audit", params=[("with_ai", "true"), ("refresh", "true"),
        *(('ai_processing_consent', value) for value in values)])
    assert response.status_code == 428, response.text


def test_recipe_list_never_grants_or_starts_ai_for_admin(admin, monkeypatch):
    client, _, _ = admin
    monkeypatch.setattr(api_recipes, "ensure_extraction_running", forbidden)
    response = client.get("/api/recipes")
    assert response.status_code == 200, response.text


def test_accepted_import_persists_permission_without_leaking_to_next_request(admin, monkeypatch):
    client, db, _ = admin
    monkeypatch.setattr(task_queue, "_dispatch_with_consent", forbidden)
    response = client.post("/api/pending/import-url", json={
        "url": "https://recipes.example/explicit-import", "ai_processing_consent": CONSENT,
    })
    assert response.status_code == 200, response.text
    jobs = db.background_task_list()
    assert len(jobs) == 1
    assert jobs[0]["payload"]["ai_processing_consent"] == CONSENT
    assert ai_consent.CURRENT_AI_CONSENT.get() is None
    denied = client.post("/api/pending/import-url", json={"url": "https://recipes.example/another-import"})
    assert denied.status_code == 428
    assert len(db.background_task_list()) == 1


@pytest.mark.parametrize("marker,expected", [(None, 428), ("wrong", 428), (CONSENT, 200)])
def test_token_intake_requires_its_own_action_permission(admin, monkeypatch, marker, expected):
    client, db, _ = admin
    monkeypatch.setattr(api_share, "_check_token", lambda token: "synthetic-token-id")
    monkeypatch.setattr(api_share, "_consume_rate_limit", lambda key: None)
    body = {"url": "https://recipes.example/token-import", "token": "synthetic-only"}
    if marker:
        body["ai_processing_consent"] = marker
    response = client.post("/api/share", json=body)
    assert response.status_code == expected, response.text
    jobs = db.background_task_list()
    assert len(jobs) == (1 if marker == CONSENT else 0)
    if jobs:
        assert jobs[0]["payload"]["ai_processing_consent"] == CONSENT


def test_persisted_permission_survives_recovery_and_is_rechecked_after_provider_change(test_db, monkeypatch, isolated_provider_and_workers):
    monkeypatch.setattr(task_queue, "get_db", lambda: test_db)
    token = ai_consent.CURRENT_AI_CONSENT.set(CONSENT)
    try:
        task_id = task_queue.enqueue("share_ingest", {"url": "https://recipes.example/restart"})
    finally:
        ai_consent.CURRENT_AI_CONSENT.reset(token)
    assert test_db.background_task_claim_next(lane="imports")["id"] == task_id
    reopened = Database(test_db.path)
    assert reopened.background_tasks_recover() == 1
    recovered = reopened.background_task_claim_next(lane="imports")
    assert recovered["payload"]["ai_processing_consent"] == CONSENT
    seen = []
    monkeypatch.setattr(task_queue, "_dispatch_with_consent", lambda kind, payload: seen.append(
        (kind, ai_consent.CURRENT_AI_CONSENT.get())) or {"ok": True})
    assert task_queue._dispatch(recovered["kind"], recovered["payload"])["ok"]
    assert seen == [("share_ingest", CONSENT)]
    assert ai_consent.CURRENT_AI_CONSENT.get() is None
    isolated_provider_and_workers.base_url = "https://different.example/v1"
    with pytest.raises(HTTPException) as raised:
        task_queue._dispatch(recovered["kind"], recovered["payload"])
    assert raised.value.status_code == 409
    assert seen == [("share_ingest", CONSENT)]


def test_old_queue_entry_without_permission_cannot_inherit_another_current_action(test_db, monkeypatch):
    task_id = test_db.background_task_enqueue("share_ingest", {"url": "https://recipes.example/legacy"})
    payload = test_db.background_task_get(task_id)["payload"]
    monkeypatch.setattr(task_queue, "_dispatch_with_consent", forbidden)
    token = ai_consent.CURRENT_AI_CONSENT.set(CONSENT)
    try:
        with pytest.raises(HTTPException) as raised:
            task_queue._dispatch("share_ingest", payload)
        assert raised.value.status_code == 428
    finally:
        ai_consent.CURRENT_AI_CONSENT.reset(token)


def test_bound_background_action_keeps_permission_without_leaking_into_other_work():
    token = ai_consent.CURRENT_AI_CONSENT.set(CONSENT)
    try:
        accepted = ai_consent.consent_bound(lambda: ai_consent.CURRENT_AI_CONSENT.get())
    finally:
        ai_consent.CURRENT_AI_CONSENT.reset(token)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(accepted).result() == CONSENT
        assert executor.submit(lambda: ai_consent.CURRENT_AI_CONSENT.get()).result() is None


def isolated_indexer(monkeypatch, db):
    """Load the real worker without the broad suite's startup/lazy-worker stub."""
    from app.recipes import indexer
    spec = importlib.util.spec_from_file_location("app.recipes._consent_test_indexer", indexer.__file__)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "get_db", lambda: HouseholdDatabase(db, CURRENT_HOUSEHOLD.get()))
    # A currently busy worker queues new requests. Drain synchronously below,
    # exercising the real queue and captured Context without starting AI.
    runtime._worker_thread = SimpleNamespace(is_alive=lambda: True)
    return runtime


def accept_snapshot(runtime, account_id, recipe_ids):
    with household_context(HouseholdScope(account_id)):
        token = ai_consent.CURRENT_AI_CONSENT.set(CONSENT)
        try:
            assert runtime.ensure_extraction_running(recipe_ids=recipe_ids)
        finally:
            ai_consent.CURRENT_AI_CONSENT.reset(token)


def test_two_accepted_households_keep_separate_queued_snapshots_and_context(households, monkeypatch):
    _, db, users, _ = households
    aid, bid = users["anna"][1], users["bert"][1]
    a = _recipe(db, "SnapshotA", "https://recipes.example/snapshot-a", owner=aid)
    b = _recipe(db, "SnapshotB", "https://recipes.example/snapshot-b", owner=bid)
    for recipe_id in (a, b):
        db.recipe_set_extraction_result(recipe_id, status="pending", ingredients=[])
    runtime = isolated_indexer(monkeypatch, db)
    seen = []
    monkeypatch.setattr(runtime, "_extraction_loop", lambda snapshot: seen.append((
        CURRENT_HOUSEHOLD.get().account_id, ai_consent.CURRENT_AI_CONSENT.get(),
        [row["id"] for row in snapshot])))
    accept_snapshot(runtime, aid, [a, b])
    accept_snapshot(runtime, bid, [b])
    runtime._run_extraction_requests()
    assert seen == [(aid, CONSENT, [a]), (bid, CONSENT, [b])]
    assert CURRENT_HOUSEHOLD.get() is None
    assert ai_consent.CURRENT_AI_CONSENT.get() is None


def test_recipe_added_after_confirmation_is_not_absorbed_into_snapshot(households, monkeypatch):
    _, db, users, _ = households
    aid = users["anna"][1]
    accepted = _recipe(db, "ExplicitRecipe", "https://recipes.example/explicit", owner=aid)
    db.recipe_set_extraction_result(accepted, status="pending", ingredients=[])
    runtime = isolated_indexer(monkeypatch, db)
    seen = []
    monkeypatch.setattr(runtime, "_extraction_loop", lambda snapshot: seen.extend(row["id"] for row in snapshot))
    accept_snapshot(runtime, aid, [accepted])
    later = _recipe(db, "LaterRecipe", "https://recipes.example/later", owner=aid)
    db.recipe_set_extraction_result(later, status="pending", ingredients=[])
    runtime._run_extraction_requests()
    assert seen == [accepted]
    assert db.recipe_get(later)["ingredients_status"] == "pending"


def test_image_batch_uses_only_the_recipes_visible_when_request_was_accepted(admin, monkeypatch):
    client, db, private_id = admin
    global_id = _recipe(db, "ImageBatchGlobal", "https://recipes.example/image-batch-global")
    submitted = []

    def enqueue(kind, payload, **kwargs):
        assert ai_consent.CURRENT_AI_CONSENT.get() == CONSENT
        submitted.append((kind, dict(payload)))
        return 902

    monkeypatch.setattr(task_queue, "enqueue", enqueue)
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    response = client.post("/api/recipes/images/backfill", json={"ai_processing_consent": CONSENT})
    assert response.status_code == 202, response.text
    assert len(submitted) == 1 and submitted[0][0] == "recipe_image_backfill"
    payload = submitted[0][1]
    assert payload["recipe_ids"] == [private_id, global_id]

    later = _recipe(db, "ImageBatchLater", "https://recipes.example/image-batch-later")
    monkeypatch.setattr(image_generation, "get_db", lambda: db)
    monkeypatch.setattr(db, "recipes_for_image_backfill", forbidden)
    backed_up, generated = [], []
    monkeypatch.setattr(image_generation, "backup_recipe_image",
                        lambda recipe, batch_id: backed_up.append(recipe["id"]) or recipe["id"])
    monkeypatch.setattr(image_generation, "generate_recipe_image",
                        lambda recipe_id, **kwargs: generated.append(recipe_id) or {"ok": True})
    result = image_generation.run_image_backfill(payload)
    assert result["ok"]
    assert backed_up == generated == [private_id, global_id]
    assert later not in result["recipe_ids"]
