"""Server-Sent-Events Endpoint für Live-Updates.

Ersetzt das Frontend-Polling von /api/jobs/status/current und
/api/jobs/*/progress. Eine offene HTTPS-Connection statt 2+ Req/Sek.

Format ist die Standard-SSE-Notation:
    event: <name>
    data: <json>
    \\n\\n

Drei Event-Typen:
  - status:           {scraper, reanalyze, pending_count}
  - scraper_progress: vom scraper_progress-Endpoint

Heartbeat alle 25 s als Comment ('`: keepalive`'), damit Cloudflare-Tunnel
& Reverse-Proxys die Connection nicht wegen Idle-Timeout kappen.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..auth import request_user, require_auth
from ..db import get_db
from . import api_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"], dependencies=[Depends(require_auth)])


# Wie oft Status-Snapshot gesendet wird. Klein genug für gefühlt
# Live-Updates, groß genug damit der Server nicht ständig DB-Pings macht.
STATUS_INTERVAL = 2.0          # Sekunden
HEARTBEAT_INTERVAL = 25.0      # Sekunden (CF-Tunnel idle-timeout ~100s)


def _format(event: str, data) -> str:
    """Baut eine SSE-Event-Nachricht. Mehrzeilige JSON werden zu data:-Zeilen."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n"


async def _stream(request: Request) -> AsyncIterator[bytes]:
    db = get_db()
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()

    # Initial-Snapshot sofort senden damit das UI nicht 2 s warten muss
    try:
        initial = await run_in_threadpool(_status_snapshot, db)
        yield _format("status", initial).encode()
    except Exception:
        pass

    while True:
        # Frühes-Stopp: Client disconnected
        if await request.is_disconnected():
            logger.debug("SSE client disconnected, closing stream")
            return

        # Router-Dependencies werden nur beim Verbindungsaufbau ausgewertet.
        # Erneute Prüfung beendet den Stream nach Logout, Sperre oder Rotation.
        if not await run_in_threadpool(request_user, request):
            yield _format("auth_revoked", {"reason": "session_invalid"}).encode()
            return

        try:
            snapshot = await run_in_threadpool(_status_snapshot, db)
            yield _format("status", snapshot).encode()

            # Progress-Events nur wenn was läuft
            if snapshot.get("scraper") or snapshot.get("reanalyze"):
                try:
                    p = await run_in_threadpool(api_jobs.scraper_progress)
                    yield _format("scraper_progress", p).encode()
                except Exception as e:
                    logger.debug(f"scraper_progress fail: {e}")

            # Heartbeat (Comment-Line) gegen idle-Timeout der Reverse-Proxies
            now = loop.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                yield b": keepalive\n\n"
                last_heartbeat = now

        except Exception as e:
            logger.warning(f"SSE stream error: {e}")
            # Continue trying - EventSource auf Client reconnected sowieso

        await asyncio.sleep(STATUS_INTERVAL)


def _status_snapshot(db) -> dict:
    return {
        "scraper": bool(db.job_running("scraper")),
        "reanalyze": bool(db.job_running("reanalyze")),
        "pending_count": db.pending_count(),
    }


@router.get("")
async def events(request: Request):
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # X-Accel-Buffering disabled für nginx / Reverse-Proxies
            "X-Accel-Buffering": "no",
        },
    )
