"""Request-local authentication and contention regressions, with real SQLite."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer
from starlette.requests import Request

from app import accounts, auth
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope, scope_for_request


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _request(token):
    return Request({"type": "http", "method": "GET", "path": "/api/config",
                    "headers": [(b"authorization", ("Bearer " + token).encode())],
                    "scheme": "https", "query_string": b"", "server": ("test", 443)})


@pytest.fixture
def admin_session(test_db, monkeypatch):
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(auth, "_serializer", lambda: URLSafeTimedSerializer("request-performance-test" * 3))
    uid = test_db.user_create("request-admin", "unused", role="admin")
    accounts.view(test_db, uid)
    return test_db, auth.create_session("request-admin")


@pytest.mark.anyio
async def test_auth_and_household_dependencies_share_one_user_lookup(admin_session, monkeypatch):
    db, token = admin_session
    calls = []
    original = db.user_get_by_name
    def lookup(name):
        calls.append(name)
        return original(name)
    monkeypatch.setattr(db, "user_get_by_name", lookup)
    request = _request(token)
    assert (await asyncio.to_thread(scope_for_request, request)).is_admin
    await auth.require_auth(request)
    assert (await auth.require_admin(request))["role"] == "admin"
    assert calls == ["request-admin"]
    db.user_revoke_sessions("request-admin")
    with pytest.raises(HTTPException) as error:
        await auth.require_auth(_request(token))
    assert error.value.status_code == 401


@pytest.mark.parametrize("dependency", [auth.require_auth, auth.require_admin])
@pytest.mark.anyio
async def test_auth_database_lookup_runs_outside_event_loop(admin_session, monkeypatch, dependency):
    db, token = admin_session
    event_loop_thread = threading.get_ident()
    threads = []
    original = db.user_get_by_name
    def lookup(name):
        threads.append(threading.get_ident())
        return original(name)
    monkeypatch.setattr(db, "user_get_by_name", lookup)
    await dependency(_request(token))
    assert threads and event_loop_thread not in threads


@pytest.mark.anyio
async def test_open_event_stream_rechecks_revoked_cached_session(admin_session, monkeypatch):
    from app.routes import api_events
    db, token = admin_session
    request = _request(token)
    await auth.require_admin(request)
    async def connected():
        return False
    monkeypatch.setattr(request, "is_disconnected", connected)
    monkeypatch.setattr(api_events, "_status_snapshot", lambda _db: {})
    stream = api_events._stream(request)
    assert b"event: status" in await anext(stream)
    db.user_revoke_sessions("request-admin")
    assert b"event: auth_revoked" in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.parametrize("tenant", [False, True])
def test_cart_without_due_rules_reads_while_another_writer_holds_lock(test_db, tenant):
    uid = test_db.user_create("cart-reader", "unused", role="user")
    aid = accounts.view(test_db, uid)["id"]
    db = HouseholdDatabase(test_db, HouseholdScope(aid, user_id=uid)) if tenant else test_db
    with test_db.conn() as writer:
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(db.recurring_run_due, due_on=date.today()).result(timeout=2) == []


@pytest.mark.parametrize("active_timer", ["scrapper-job.timer", "scrapper-db-backup.timer"])
def test_restore_refuses_running_timer_before_touching_database(tmp_path, monkeypatch, active_timer):
    import shutil
    import subprocess
    from app import cli
    src = tmp_path / "backup.db"
    target = tmp_path / "live.db"
    src.write_bytes(b"backup sentinel")
    target.write_bytes(b"live sentinel")
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(cli, "get_config", lambda: SimpleNamespace(get=lambda *a, **k: str(target)))
    monkeypatch.setattr(shutil, "which", lambda _name: "/test/systemctl")
    checked = []
    def check(command, **_kwargs):
        checked.append(command[-1])
        return SimpleNamespace(returncode=0 if command[-1] == active_timer else 3)
    monkeypatch.setattr(subprocess, "run", check)
    assert cli._cmd_db_restore([str(src)]) == 1
    assert active_timer in checked
    assert target.read_bytes() == b"live sentinel"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backup.db", "live.db"]
