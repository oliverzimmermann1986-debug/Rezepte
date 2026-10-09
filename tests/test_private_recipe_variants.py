"""A normal account can copy visible recipes without changing their source."""
import json
from pathlib import Path

import pytest
from itsdangerous import URLSafeTimedSerializer

from app import accounts, auth
from app.config_store import get_config
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope


@pytest.fixture
def variant_accounts(client, test_db, monkeypatch, tmp_path):
    from app.main import app
    config = get_config()
    previous_get = config.get
    root = tmp_path / "recipes"
    root.mkdir()
    monkeypatch.setattr(config, "get", lambda *keys, default=None: str(root) if keys == ("paths", "recipe_dir") else previous_get(*keys, default=default))
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("private-variant-tests-" * 3))
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides.pop(auth.require_auth, None)
    app.dependency_overrides.pop(auth.require_admin, None)
    app.dependency_overrides.pop(auth.require_import, None)
    users = {}
    for name, role in (("owner", "admin"), ("alice", "user"), ("bob", "user")):
        user_id = test_db.user_create(name, "not-a-password", role=role)
        users[name] = accounts.view(test_db, user_id)["id"]

    def login(name):
        token = auth.create_guest_session() if name == "guest" else auth.create_session(name)
        client.headers["Authorization"] = "Bearer " + token

    yield client, test_db, users, login
    app.dependency_overrides.clear()
    app.dependency_overrides.update(overrides)


def make_recipe(db, name="Original", owner=None, status="ok"):
    root = Path(get_config().get("paths", "recipe_dir"))
    base = root if owner is None else root / ".households" / str(owner)
    folder = base / "Hauptgericht" / "Test" / name
    folder.mkdir(parents=True)
    (folder / "description.txt").write_text("Originalbeschreibung", encoding="utf-8")
    (folder / "info.json").write_text(json.dumps({"name": name, "owner_account_id": owner}), encoding="utf-8")
    (folder / "source.mp4").write_bytes(b"video must not be copied")
    recipe_id = db.recipe_upsert(url=None, name=name, type="Hauptgericht", category="Test",
                                folder_path=str(folder), owner_account_id=owner,
                                description="Originalbeschreibung", thumb_filename=None,
                                video_filename="source.mp4", source_added_at=None)
    db.recipe_set_extraction_result(recipe_id, ingredients=[{"name": "Hafer", "amount": 100, "unit": "g"}], status=status)
    db.recipe_steps_set(recipe_id, [{"instruction": "Vermischen."}])
    db.recipe_set_servings(recipe_id, 2)
    return recipe_id, folder


def test_global_copy_is_private_editable_and_preserves_original(variant_accounts):
    client, db, users, login = variant_accounts
    recipe_id, original_folder = make_recipe(db)
    original = db.recipe_snapshot(recipe_id)
    original_files = {p.name: p.read_bytes() for p in original_folder.iterdir()}
    login("alice")
    response = client.post(f"/api/recipes/{recipe_id}/duplicate", json={
        "new_name": "Meine Variante", "owner_account_id": users["bob"], "visibility": "global",
    })
    assert response.status_code == 200, response.text
    copied_id = response.json()["recipe_id"]
    copied = db.recipe_get(copied_id)
    assert copied["owner_account_id"] == users["alice"]
    assert copied["ingredients_status"] == "ok"
    private_root = Path(get_config().get("paths", "recipe_dir")) / ".households" / str(users["alice"])
    assert Path(copied["folder_path"]).is_relative_to(private_root)
    info = json.loads((Path(copied["folder_path"]) / "info.json").read_text(encoding="utf-8"))
    assert info["owner_account_id"] == users["alice"] and info["variant_of"] == recipe_id
    assert not (Path(copied["folder_path"]) / "source.mp4").exists()
    assert client.put(f"/api/recipes/{copied_id}/steps", json={"steps": [{"instruction": "Privat ändern."}]}).status_code == 200
    assert client.put(f"/api/recipes/{recipe_id}/steps", json={"steps": [{"instruction": "Verboten."}]}).status_code == 403
    assert db.recipe_snapshot(recipe_id) == original
    assert {p.name: p.read_bytes() for p in original_folder.iterdir()} == original_files
    login("bob")
    assert client.get(f"/api/recipes/{copied_id}").status_code == 404
    assert client.post(f"/api/recipes/{copied_id}/duplicate", json={"new_name": "Gestohlen"}).status_code == 404


def test_same_variant_name_is_isolated_between_households(variant_accounts):
    client, db, users, login = variant_accounts
    recipe_id, _ = make_recipe(db)
    copies = []
    for name in ("alice", "bob"):
        login(name)
        response = client.post(f"/api/recipes/{recipe_id}/duplicate", json={"new_name": "Mein Rezept"})
        assert response.status_code == 200, response.text
        copied = db.recipe_get(response.json()["recipe_id"])
        assert copied["owner_account_id"] == users[name]
        copies.append(copied)
    assert copies[0]["folder_path"] != copies[1]["folder_path"]
    assert copies[0]["id"] != copies[1]["id"]


@pytest.mark.parametrize("status", ["pending", "running", "failed", "no_data"])
def test_manual_variant_never_requeues_source_extraction(variant_accounts, status):
    client, db, users, login = variant_accounts
    recipe_id, _ = make_recipe(db, status=status)
    login("alice")
    response = client.post(f"/api/recipes/{recipe_id}/duplicate", json={"new_name": "Manuelle Kopie"})
    assert response.status_code == 200, response.text
    copied = db.recipe_get(response.json()["recipe_id"])
    assert copied["ingredients_status"] == "skipped"
    assert copied["id"] not in {item["id"] for item in db.recipes_pending_extraction()}
    assert db.recipe_get(recipe_id)["ingredients_status"] == status
    assert db.background_task_list() == []


def test_unauthorized_and_guest_copy_have_no_side_effects(variant_accounts):
    client, db, users, login = variant_accounts
    recipe_id, _ = make_recipe(db)
    before = db.recipe_count()
    assert client.post(f"/api/recipes/{recipe_id}/duplicate", json={"new_name": "Nicht erlaubt"}).status_code == 401
    login("guest")
    assert client.post(f"/api/recipes/{recipe_id}/duplicate", json={"new_name": "Nicht erlaubt"}).status_code == 403
    assert db.recipe_count() == before
    assert not (Path(get_config().get("paths", "recipe_dir")) / ".households").exists()


def test_private_copy_keeps_household_and_admin_global_copy_stays_global(variant_accounts):
    client, db, users, login = variant_accounts
    private_id, _ = make_recipe(db, "Privat", users["alice"])
    login("alice")
    response = client.post(f"/api/recipes/{private_id}/duplicate", json={"new_name": "Private Variante"})
    assert response.status_code == 200, response.text
    assert db.recipe_get(response.json()["recipe_id"])["owner_account_id"] == users["alice"]
    global_id, _ = make_recipe(db)
    login("owner")
    response = client.post(f"/api/recipes/{global_id}/duplicate", json={"new_name": "Globale Variante"})
    assert response.status_code == 200, response.text
    assert db.recipe_get(response.json()["recipe_id"])["owner_account_id"] is None


@pytest.mark.parametrize("failing_method", ["recipe_clone_content", "recipe_variant_finalize"])
def test_private_variant_rolls_back_new_files_and_row_on_failure(variant_accounts, monkeypatch, failing_method):
    from app.recipes.manage import safe_duplicate_recipe, sanitize_filename
    _, db, users, _ = variant_accounts
    recipe_id, original_folder = make_recipe(db)
    original_files = {p.name: p.read_bytes() for p in original_folder.iterdir()}
    scoped = HouseholdDatabase(db, HouseholdScope(users["alice"]))
    before = db.recipe_count()

    def fail(*args, **kwargs):
        raise RuntimeError("injected clone failure")

    monkeypatch.setattr(scoped, failing_method, fail)
    with pytest.raises(RuntimeError, match="injected clone failure"):
        safe_duplicate_recipe(scoped, recipe_id, new_name="Abgebrochene Variante")
    assert db.recipe_count() == before
    target = Path(get_config().get("paths", "recipe_dir")) / ".households" / str(users["alice"]) / "Hauptgericht" / "Test" / sanitize_filename("Abgebrochene Variante")
    assert not target.exists()
    assert original_folder.is_dir()
    assert {p.name: p.read_bytes() for p in original_folder.iterdir()} == original_files


def test_published_private_variant_recovers_after_process_interruption(variant_accounts, monkeypatch):
    from app.recipes.manage import safe_duplicate_recipe
    _, db, users, _ = variant_accounts
    recipe_id, original_folder = make_recipe(db)
    original_files = {p.name: p.read_bytes() for p in original_folder.iterdir()}
    scoped = HouseholdDatabase(db, HouseholdScope(users["alice"]))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("process stopped after publish")

    with monkeypatch.context() as patch:
        patch.setattr(scoped, "recipe_variant_finalize", interrupted)
        with pytest.raises(KeyboardInterrupt, match="process stopped"):
            safe_duplicate_recipe(scoped, recipe_id, new_name="CrashCopy")

    target = Path(get_config().get("paths", "recipe_dir")) / ".households" / str(users["alice"]) / "Hauptgericht" / "Test" / "CrashCopy"
    pending = scoped.recipe_get_by_folder(str(target), include_pending=True)
    assert pending and pending["ingredients_status"] == "variant_pending"
    assert scoped.recipe_get_by_folder(str(target)) is None
    other = HouseholdDatabase(db, HouseholdScope(users["bob"]))
    assert other.recipe_get_by_folder(str(target), include_pending=True) is None
    with pytest.raises(RuntimeError, match="existiert bereits"):
        safe_duplicate_recipe(scoped, recipe_id, new_name="CrashCopy")
    recovered = scoped.recipe_get_by_folder(str(target))
    assert recovered and recovered["id"] == pending["id"]
    assert recovered["ingredients_status"] == "ok"
    assert db.recipe_count() == 2
    assert {p.name: p.read_bytes() for p in original_folder.iterdir()} == original_files
