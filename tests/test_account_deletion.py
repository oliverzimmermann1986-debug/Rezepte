"""Destructive account deletion is exercised only against isolated synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from app import account_deletion as deletion, accounts, auth
from app.db import Database
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, household_write_guard
from tests.test_account_management import account_api, password_hash  # noqa: F401
from tests.test_tenants import _recipe


@pytest.fixture
def deletion_api(account_api, tmp_path, monkeypatch):
    client, db, uid, admin = account_api
    aid = accounts.view(db, uid)["id"]
    other = accounts.view(db, db.user_get_by_name("bert")["id"])["id"]
    roots = {name: tmp_path / name for name in ("recipes", "temporary", "trash", "images")}
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(deletion, "_roots", lambda _: roots)
    # _recipe follows the same synthetic recipe root.
    from app.config_store import get_config
    old_get = get_config().get
    monkeypatch.setattr(get_config(), "get", lambda *keys, default=None:
                        str(roots["recipes"]) if keys == ("paths", "recipe_dir") else old_get(*keys, default=default))
    return client, db, uid, aid, other, roots


def erase(client, **overrides):
    return client.request("DELETE", "/api/account/profile", json={
        "current_password": "old-pass", "delete_household": True,
        "confirmation": deletion.CONFIRMATION, **overrides,
    })


def test_complete_deletion_removes_private_rows_files_tokens_and_keeps_other_households(deletion_api):
    client, db, uid, aid, other, roots = deletion_api
    private = _recipe(db, "Private", "https://example.test/private", owner=aid)
    theirs = _recipe(db, "Other", "https://example.test/other", owner=other)
    global_id = _recipe(db, "Global", "https://example.test/global")
    own = roots["recipes"] / ".households" / str(aid)
    (own / "Private" / "recipe.pdf").write_bytes(b"private PDF")
    (own / "Private" / ".video-ai-evidence.json").write_text('{"private":"evidence"}')
    temporary = roots["temporary"] / "households" / str(aid)
    temporary.mkdir(parents=True)
    (temporary / "scan.jpg").write_bytes(b"private photo")
    scoped = HouseholdDatabase(db, HouseholdScope(aid))
    scoped.pending_add("https://example.test/pending", content_type="recipe", video_path=str(temporary / "scan.jpg"))
    scoped.history_add("https://example.test/private", content_type="recipe", target_dir=str(own / "Private"))
    invitation = accounts.invite(db, uid)
    db.recipe_share_link_create("private-share", private, expires_at=time.time()+300, owner_account_id=aid)
    db.recipe_share_link_create("global-share", global_id, expires_at=time.time()+300, owner_account_id=aid)
    db.share_intake_token_create("device", "synthetic-token-hash", "Private device", "anna")
    db.background_task_enqueue("share_ingest", {"account_id": aid, "url": "https://example.test/pending"})
    with db.conn() as c:
        c.execute("INSERT INTO shopping_cart(name,added_at,account_id) VALUES('Private milk',?,?)", (time.time(), aid))
        c.execute("INSERT INTO shopping_cart(name,added_at,account_id) VALUES('Other milk',?,?)", (time.time(), other))
        c.execute("INSERT INTO account_recipe_state(account_id,recipe_id,saved_at) VALUES(?,?,?)", (aid, global_id, time.time()))
        c.execute("INSERT INTO shopping_sync_operations VALUES(?,?,?,?,?)", (aid, "sync", "private", "private", time.time()))
        c.execute("INSERT INTO recipe_versions(recipe_id,version_no,created_at,snapshot_json) VALUES(?,1,?,?)", (private, time.time(), '{"private":true}'))
    token = client.headers["Authorization"].removeprefix("Bearer ")
    response = erase(client)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deleted"
    assert db.user_get_by_name("anna") is None and auth.session_user(token) is None
    assert not own.exists() and not temporary.exists()
    assert db.recipe_get(theirs) and db.recipe_get(global_id) and not db.recipe_get(private)
    assert (roots["recipes"] / ".households" / str(other) / "Other").is_dir()
    with db.conn() as c:
        assert not c.execute("SELECT * FROM household_purges").fetchall()
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        for table in deletion.ACCOUNT_TABLES:
            assert not c.execute(f"SELECT 1 FROM {table} WHERE account_id=?", (aid,)).fetchone(), table
        assert not c.execute("SELECT 1 FROM account_invitations WHERE id=?", (invitation["id"],)).fetchone()
        for table in ("user_sessions", "recipe_versions", "share_intake_tokens", "background_tasks", "recipe_share_links"):
            assert not c.execute(f"SELECT 1 FROM {table}").fetchone(), table
        assert c.execute("SELECT name FROM shopping_cart WHERE account_id=?", (other,)).fetchone()[0] == "Other milk"


@pytest.mark.parametrize("override,status", [
    ({"confirmation": ""}, 400), ({"confirmation": "haushalt löschen"}, 400),
    ({"delete_household": "true"}, 422), ({"account_id": 999}, 422),
    ({"current_password": "wrong"}, 403),
])
def test_confirmation_auth_and_scope_are_required(deletion_api, override, status):
    client, db, _, aid, _, _ = deletion_api
    _recipe(db, "Retain", "https://example.test/retain", owner=aid)
    response = erase(client, **override)
    assert response.status_code == status, response.text
    assert db.user_get_by_name("anna")


def test_other_member_or_last_admin_cannot_be_erased(deletion_api):
    client, db, uid, aid, _, _ = deletion_api
    invitation = accounts.invite(db, uid)
    accounts.accept(db, db.user_get_by_name("bert")["id"], invitation["token"])
    assert erase(client).status_code == 409
    assert db.user_get_by_name("anna") and len(accounts.view(db, uid)["members"]) == 2
    client.headers["Authorization"] = "Bearer " + auth.create_session("admin")
    response = erase(client)
    assert response.status_code == 400 and "letzte aktive Administrator" in response.text


@pytest.mark.parametrize("busy", ["task", "extraction", "nutrition", "image"])
def test_active_work_prevents_deletion_before_any_change(deletion_api, busy):
    client, db, _, aid, _, roots = deletion_api
    rid = _recipe(db, "Working", "https://example.test/working", owner=aid)
    with db.conn() as c:
        if busy == "task":
            c.execute("INSERT INTO background_tasks(kind,payload_json,status,created_at) VALUES('share_ingest',?,'running',?)",
                      (json.dumps({"account_id": aid}), time.time()))
        else:
            clause = {"extraction": "ingredients_status='running'", "nutrition": "nutrition_claimed_at=1", "image": "image_generation_status='running'"}[busy]
            c.execute(f"UPDATE recipes SET {clause} WHERE id=?", (rid,))
    assert erase(client).status_code == 409
    assert db.user_get_by_name("anna") and db.recipe_get(rid)
    assert (roots["recipes"] / ".households" / str(aid) / "Working").exists()


def test_filesystem_failure_is_pending_and_recovers_after_database_reopen(deletion_api, monkeypatch):
    client, db, _, aid, _, roots = deletion_api
    _recipe(db, "Retry", "https://example.test/retry", owner=aid)
    original = deletion._erase
    monkeypatch.setattr(deletion, "_erase", lambda *args: (_ for _ in ()).throw(PermissionError("private path must not leak")))
    response = erase(client)
    assert response.status_code == 202 and response.json()["status"] == "deletion_pending"
    assert "private path" not in response.text
    assert not db.user_get_by_name("anna")
    with db.conn() as c:
        assert c.execute("SELECT attempts FROM household_purges").fetchone()[0] == 1
    monkeypatch.setattr(deletion, "_erase", original)
    reopened = Database(db.path)
    deletion.recover_purges(reopened)
    assert not (roots["recipes"] / ".households" / str(aid)).exists()
    with reopened.conn() as c:
        assert not c.execute("SELECT * FROM household_purges").fetchall()


def test_sql_failure_rolls_back_user_data_and_purge_receipt_without_erasing_files(deletion_api, monkeypatch):
    client, db, _, aid, _, roots = deletion_api
    _recipe(db, "Rollback", "https://example.test/rollback", owner=aid)
    from app import oidc
    def fail(*args):
        raise RuntimeError("synthetic SQL failure")
    monkeypatch.setattr(oidc, "queue_deleted_user", fail)
    with pytest.raises(RuntimeError, match="synthetic SQL failure"):
        erase(client)
    assert db.user_get_by_name("anna")
    assert (roots["recipes"] / ".households" / str(aid) / "Rollback").is_dir()
    with db.conn() as c:
        assert not c.execute("SELECT * FROM household_purges").fetchall()
        assert not c.execute("SELECT * FROM deleted_households").fetchall()


@pytest.mark.parametrize("target", ["outside", "foreign", "root"])
def test_untrusted_file_targets_cannot_delete_outside_or_foreign_files(deletion_api, target):
    client, db, _, aid, other, roots = deletion_api
    rid = _recipe(db, "Unsafe", "https://example.test/unsafe", owner=aid)
    other_rid = _recipe(db, "Foreign", "https://example.test/foreign", owner=other)
    paths = {"outside": roots["recipes"].parent / "protected", "root": roots["recipes"],
             "foreign": Path(db.recipe_get(other_rid)["folder_path"])}
    paths[target].mkdir(exist_ok=True)
    keep = paths[target] / "keep.txt"
    keep.write_text("not owned")
    with db.conn() as c:
        c.execute("UPDATE recipes SET deleted_folder_path=? WHERE id=?", (str(paths[target]), rid))
    response = erase(client)
    assert response.status_code == 409
    assert keep.read_text() == "not owned" and db.user_get_by_name("anna")


def test_member_join_between_auth_and_transaction_prevents_household_erasure(deletion_api, monkeypatch):
    client, db, uid, _, _, _ = deletion_api
    from app.routes import api_account
    original = api_account.require_recent_auth
    invitation = accounts.invite(db, uid)
    def authenticate_and_join(request, password):
        user = original(request, password)
        accounts.accept(db, db.user_get_by_name("bert")["id"], invitation["token"])
        return user
    monkeypatch.setattr(api_account, "require_recent_auth", authenticate_and_join)
    assert erase(client).status_code == 409
    assert len(accounts.view(db, uid)["members"]) == 2


def test_household_lock_blocks_delete_during_an_import_and_stale_writes_are_rejected(deletion_api):
    client, db, uid, aid, _, _ = deletion_api
    entered, release = threading.Event(), threading.Event()
    def importing():
        with household_write_guard(db, HouseholdScope(aid, user_id=uid)):
            entered.set()
            assert release.wait(15)
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(importing)
        try:
            assert entered.wait(15)
            assert erase(client).status_code == 409
        finally:
            release.set()
        work.result(timeout=15)
    assert erase(client).status_code == 200
    with pytest.raises(sqlite3.IntegrityError, match="household_deleted"):
        db.background_task_enqueue("share_ingest", {"account_id": aid})
    with pytest.raises(sqlite3.IntegrityError, match="household_deleted"):
        db.pending_add("private-recipe://stale", content_type="recipe", owner_account_id=aid)


@pytest.mark.parametrize("hard_deleted", [False, True])
def test_soft_deleted_recipe_quarantine_and_original_image_are_erased(deletion_api, hard_deleted):
    client, db, _, aid, _, roots = deletion_api
    rid = _recipe(db, "Archived", "https://example.test/archived", owner=aid)
    original_folder = db.recipe_get(rid)["folder_path"]
    archive = roots["trash"] / "isolated-entry" / "payload"
    archive.mkdir(parents=True)
    (archive / "private.pdf").write_bytes(b"private archive")
    image = roots["images"] / "synthetic-batch" / str(rid) / "original.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"private original")
    db.recipe_soft_delete(rid)
    with db.conn() as c:
        c.execute("INSERT INTO deleted_history(deleted_at,target_dir,quarantine_path,reason,metadata) VALUES(?,?,?,?,?)",
                  (time.time(), original_folder, str(archive), "soft_delete", json.dumps({"recipe_id": rid})))
        c.execute("INSERT INTO recipe_image_backups(batch_id,recipe_id,original_filename,backup_path,original_sha256,created_at) VALUES(?,?,?,?,?,?)",
                  ("synthetic-batch", rid, "photo.jpg", image.relative_to(roots["images"]).as_posix(), "synthetic", time.time()))
        if hard_deleted:
            c.execute("DELETE FROM recipes WHERE id=?", (rid,))
    response = erase(client)
    assert response.status_code == 200, response.text
    assert not archive.parent.exists() and not image.exists()
    with db.conn() as c:
        assert not c.execute("SELECT 1 FROM deleted_history").fetchone()
        assert not c.execute("SELECT 1 FROM recipe_image_backups").fetchone()


def test_extraction_claims_respect_household_and_explicit_recipe_snapshot(deletion_api):
    _, db, _, aid, other, _ = deletion_api
    global_id = _recipe(db, "Global", "https://example.test/global")
    own = _recipe(db, "Own", "https://example.test/own", owner=aid)
    later = _recipe(db, "Later", "https://example.test/later", owner=aid)
    foreign = _recipe(db, "Foreign", "https://example.test/foreign", owner=other)
    with db.conn() as c:
        c.execute("UPDATE recipes SET ingredients_status='pending'")
    assert [row["id"] for row in db.recipes_for_image_backfill(ids_only=True)] == [global_id]
    assert db.recipes_claim_extraction(limit=10, owner="empty", recipe_ids=[]) == []
    assert [row["id"] for row in db.recipes_claim_extraction(limit=10, owner="global")] == [global_id]
    assert [row["id"] for row in db.recipes_claim_extraction(limit=10, owner="private", account_id=aid,
                                                           recipe_ids=[own, foreign, global_id])] == [own]
    assert db.recipe_get(later)["ingredients_status"] == "pending"
    assert db.recipe_get(foreign)["ingredients_status"] == "pending"


def test_purge_refuses_symlink_tree_and_preserves_external_target(deletion_api):
    client, db, _, aid, _, roots = deletion_api
    _recipe(db, "Linked", "https://example.test/link", owner=aid)
    protected = roots["recipes"].parent / "outside"
    protected.mkdir()
    keep = protected / "keep.txt"
    keep.write_text("outside")
    link = roots["recipes"] / ".households" / str(aid) / "Linked" / "escape"
    try:
        link.symlink_to(protected, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevation")
    response = erase(client)
    assert response.status_code == 202
    assert keep.read_text() == "outside"
    with db.conn() as c:
        assert c.execute("SELECT 1 FROM household_purges").fetchone()
    link.unlink()
    deletion.recover_purges(db)
    assert keep.read_text() == "outside"


def test_merged_historical_archive_keeps_explicit_ownership(deletion_api):
    _, db, uid, aid, other, roots = deletion_api
    historical_id = 9001
    with db.conn() as c:
        archive_id = c.execute("INSERT INTO deleted_history(deleted_at,target_dir,metadata) VALUES(?,?,?)",
                               (time.time(), str(roots["recipes"] / ".households" / str(aid) / "old"),
                                json.dumps({"recipe_id": historical_id, "owner_account_id": aid}))).lastrowid
    invitation = accounts.invite(db, db.user_get_by_name("bert")["id"])
    accounts.accept(db, uid, invitation["token"])
    with db.conn() as c:
        metadata = json.loads(c.execute("SELECT metadata FROM deleted_history WHERE id=?", (archive_id,)).fetchone()[0])
    assert metadata["owner_account_id"] == other


def test_recovery_rotates_past_permanent_failures(deletion_api, monkeypatch):
    _, db, _, _, _, roots = deletion_api
    for index in range(11):
        manifest = {"roots": {"temporary": str(roots["temporary"])},
                    "paths": [{"kind": "temporary", "path": str(roots["temporary"] / f"target-{index}")}]}
        with db.conn() as c:
            c.execute("INSERT INTO household_purges(id,account_id,manifest_json,created_at) VALUES(?,?,?,?)",
                      (f"synthetic-{index}", 9000+index, json.dumps(manifest), index))
    def erase_files(path, root):
        if not path.endswith("target-10"):
            raise PermissionError("blocked")
    monkeypatch.setattr(deletion, "_erase", erase_files)
    deletion.recover_purges(db, limit=10)
    deletion.recover_purges(db, limit=10)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM household_purges").fetchone()[0] == 10
        assert not c.execute("SELECT 1 FROM household_purges WHERE id='synthetic-10'").fetchone()


def test_private_recipe_is_removed_from_shared_image_retry_history(deletion_api):
    client, db, _, aid, other, _ = deletion_api
    own = _recipe(db, "OwnReceipt", "https://example.test/own-receipt", owner=aid)
    foreign = _recipe(db, "ForeignReceipt", "https://example.test/foreign-receipt", owner=other)
    state = {"recipe_ids": [own, foreign], "completed_ids": [own], "phase": "done",
             "errors": [{"recipe_id": own, "name": "private name"}, {"recipe_id": foreign, "name": "keep"}]}
    with db.conn() as c:
        c.execute("INSERT INTO maintenance_runs(kind,started_at,status,result_json) VALUES(?,?,'ok',?)",
                  ("recipe_image_backfill", time.time(), json.dumps(state)))
    assert erase(client).status_code == 200
    with db.conn() as c:
        result = json.loads(c.execute("SELECT result_json FROM maintenance_runs").fetchone()[0])
    assert result["recipe_ids"] == [foreign] and result["completed_ids"] == []
    assert result["errors"] == [{"recipe_id": foreign, "name": "keep"}]
