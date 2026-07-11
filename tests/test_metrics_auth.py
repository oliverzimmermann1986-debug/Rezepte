from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException, Request

import app.auth as auth
import app.config_store as config_store
import app.routes.api_metrics as api_metrics


def _request(*, authorization: str = "", cookie: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("latin-1")))
    if cookie:
        headers.append((b"cookie", cookie.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/metrics",
            "raw_path": b"/metrics",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
    )


def _store(tmp_path: Path, monkeypatch, *, token: str = "m" * 32):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "web": {
                    "username": "oliver",
                    "password": auth.hash_password("a-very-long-password"),
                    "secret_key": "s" * 64,
                },
                "monitoring": {"metrics_token": token},
            }
        ),
        encoding="utf-8",
    )
    store = config_store.ConfigStore(path)
    monkeypatch.setattr(config_store, "_config", store)
    return store


def test_metrics_rejects_missing_or_wrong_credentials(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_metrics._require_metrics_access(_request())
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        api_metrics._require_metrics_access(
            _request(authorization="Bearer definitely-wrong")
        )
    assert exc.value.status_code == 401


def test_metrics_accepts_bearer_token_and_session(tmp_path, monkeypatch):
    token = "metrics-" + "x" * 32
    _store(tmp_path, monkeypatch, token=token)
    api_metrics._require_metrics_access(
        _request(authorization=f"Bearer {token}")
    )

    session = auth.create_session("oliver")
    api_metrics._require_metrics_access(
        _request(cookie=f"{auth.SESSION_COOKIE}={session}")
    )
