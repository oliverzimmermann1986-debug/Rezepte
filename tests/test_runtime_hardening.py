import asyncio
from contextlib import contextmanager


def test_sse_snapshot_is_offloaded_from_event_loop(monkeypatch):
    from app.routes import api_events

    calls = []

    class Db:
        def job_running(self, _kind):
            return False

        def pending_count(self):
            return 3

    async def fake_run_in_threadpool(function, *args):
        calls.append(function)
        return function(*args)

    class Request:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(api_events, "get_db", lambda: Db())
    monkeypatch.setattr(api_events, "run_in_threadpool", fake_run_in_threadpool)

    async def read_initial():
        stream = api_events._stream(Request())
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    payload = asyncio.run(read_initial())

    assert b'"pending_count": 3' in payload
    assert calls == [api_events._status_snapshot]


def test_selective_gzip_bypasses_event_stream():
    from app.main import SelectiveGZipMiddleware

    async def downstream(_scope, _receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({"type": "http.response.body", "body": b"x" * 1000})

    middleware = SelectiveGZipMiddleware(downstream, minimum_size=10)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events",
            "headers": [(b"accept-encoding", b"gzip")],
        },
        receive,
        send,
    ))

    headers = dict(messages[0]["headers"])
    assert b"content-encoding" not in headers


def test_vacuum_reports_busy_process_lock(test_db, monkeypatch):
    import app.jobs.locks as locks

    @contextmanager
    def busy(_name):
        yield None

    monkeypatch.setattr(locks, "file_lock_or_none", busy)

    result = test_db.vacuum()

    assert result == {
        "ok": False,
        "busy": True,
        "error": "Eine Datenbank-Bereinigung läuft bereits",
    }


def test_admin_vacuum_returns_conflict_when_locked(client, test_db, monkeypatch):
    from app.auth import require_admin
    from app.main import app

    monkeypatch.setattr(
        test_db,
        "vacuum",
        lambda: {
            "ok": False,
            "busy": True,
            "error": "Eine Datenbank-Bereinigung läuft bereits",
        },
    )

    app.dependency_overrides[require_admin] = lambda: {"username": "test"}
    try:
        response = client.post("/api/admin/maintenance/run/vacuum")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 409
