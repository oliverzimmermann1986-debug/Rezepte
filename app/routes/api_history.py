"""History-API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import require_auth
from ..db import get_db

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


@router.get("")
def list_history(limit: int = Query(200, ge=1, le=2000)):
    return get_db().history_list(limit=limit)
