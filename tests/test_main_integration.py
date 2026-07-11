from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

import app.auth as auth
from app.db import Database


def test_login_health_and_metrics_end_to_end(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    example = yaml.safe_load(
        (project_root / "config" / "config.example.yaml").read_text(encoding="utf-8")
    )
    example["web"].update(
        {
            "username": "oliver",
            "password": auth.hash_password("integration-password-123"),
            "secret_key": "s" * 64,
            "bind_host": "127.0.0.1",
            "bind_port": 8000,
        }
    )
    example["monitoring"]["metrics_token"] = "metrics-" + ("x" * 32)
    example["ai"]["ollama"]["enabled"] = False
    example["mail"]["recipe"]["enabled"] = False
    example["mail"]["wedding"]["enabled"] = False
    for key in ("recipe_dir", "wedding_dir", "temp_dir", "logs_dir"):
        target = tmp_path / key
        target.mkdir(parents=True, exist_ok=True)
        example["paths"][key] = str(target)
    example["external_hdd"]["enabled"] = False

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(example, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    db_path = tmp_path / "scrapper.db"
    recipe_dir = Path(example["paths"]["recipe_dir"]) / "Hauptgericht" / "Pasta" / "Testrezept"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "rezept.jpg").write_bytes(b"image")
    Database(db_path).history_add(
        "https://example.test/recipe", content_type="recipe", name="Testrezept",
        target_dir=str(recipe_dir), recipe_type="Hauptgericht", category="Pasta",
        description="Tomate und Basilikum", source="test",
    )

    script = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app, follow_redirects=False) as client:
    payload = '/\"><script>alert(1)</script>'
    login_page = client.get('/login', params={'next': payload})
    assert login_page.status_code == 200
    assert '<script>alert(1)</script>' not in login_page.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in login_page.text

    assert client.get('/healthz').status_code == 200
    assert client.get('/healthz/deep').status_code == 303
    assert client.get('/metrics').status_code == 401

    # Open-Redirect-Schutz: '/\evil.com' wird von Browsern zu '//evil.com'
    # normalisiert und muss deshalb auf '/' zurückfallen.
    evil = client.post('/login', data={
        'username': 'oliver',
        'password': 'integration-password-123',
        'next': '/\\evil.com',
    })
    assert evil.status_code == 303
    assert evil.headers['location'] == '/'

    logged_in = client.post('/login', data={
        'username': 'oliver',
        'password': 'integration-password-123',
        'next': '/',
    })
    assert logged_in.status_code == 303
    assert client.get('/healthz/deep').status_code == 200
    metrics = client.get('/metrics')
    assert metrics.status_code == 200
    assert 'scrapper_pending_count' in metrics.text
    recipes = client.get('/api/recipes', params={'q': 'Basilikum'})
    assert recipes.status_code == 200
    payload = recipes.json()
    assert payload['total'] == 1 and payload['items'][0]['name'] == 'Testrezept'
    media = client.get(payload['items'][0]['media_url'])
    assert media.status_code == 200

with TestClient(app, follow_redirects=False) as token_client:
    assert token_client.get('/metrics', headers={
        'Authorization': 'Bearer metrics-' + ('x' * 32),
    }).status_code == 200
'''
    env = os.environ.copy()
    env.update(
        {
            "SCRAPPER_CONFIG": str(config_path),
            "SCRAPPER_DB_PATH": str(db_path),
            "PYTHONPATH": str(project_root),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
