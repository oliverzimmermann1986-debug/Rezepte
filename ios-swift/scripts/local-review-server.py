"""Serve only artificial review fixtures on loopback with normal app authentication.

This harness never calls the host-restricted production review deployment tools.
It reuses their artificial recipe constants and injects a fresh temporary database
before importing the actual application. No auth dependency or TLS check is bypassed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time
from datetime import date


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))


def prepare_fixture(directory: Path, *, port: int, password: str):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise RuntimeError("The local fixture directory must be empty.")
    if len(password) < 24:
        raise RuntimeError("Use a randomly generated local fixture password.")
    public_url = f"https://localhost:{port}"
    config_path = directory / "config.yaml"
    os.environ["SCRAPPER_CONFIG"] = str(config_path)

    import bcrypt
    import yaml
    import app.db as database_module
    import app.jobs.locks as job_locks
    from app.db import Database
    from app.recipes.cart_logic import prepare_for_cart
    from tools.setup_app_review_demo import RECIPES, _ingredient_rows, REVIEW_CART_ITEMS, REVIEW_RECURRING_ITEM

    recipe_root = directory / "recipes"
    paths = {key: str(directory / folder) for key, folder in (
        ("recipe_dir", "recipes"), ("wedding_dir", "wedding"),
        ("temp_dir", "temp"), ("logs_dir", "logs"),
    )}
    for path in paths.values():
        Path(path).mkdir()
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    config = {
        "web": {"username": "app-review", "password": password_hash,
                "secret_key": secrets.token_urlsafe(48), "auth_disabled": False,
                "public_url": public_url, "trusted_proxies": [], "share_enabled": False},
        "paths": paths,
        "mail": {"recipe": {"enabled": False}, "wedding": {"enabled": False}},
        "ai": {"openai": {"api_key": ""}, "auto_translate": False,
               "video_fallback": {"enabled": False}, "image_generation": {"enabled": False}},
        "ytdlp": {"cookies_file": "", "expanded_tiktok_caption": False},
        "external_hdd": {"enabled": False}, "webhooks": [],
        "einkauf": {"api_url": "", "app_token": ""},
    }
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    config_path.chmod(0o600)
    db = Database(directory / "fixture.db")
    database_module._db = db
    # The normal application shutdown writes a scraper cancellation marker.
    # Keep that process-local fixture state away from the deployment default.
    job_locks.LOCK_DIR = directory / "locks"
    job_locks.LOCK_DIR.mkdir()
    db.user_create("app-review", password_hash, role="admin")
    for item in RECIPES:
        folder = recipe_root / item["slug"]
        folder.mkdir()
        image_name = f"{item['slug']}.png"
        shutil.copy2(REPOSITORY / "review-demo/assets" / image_name, folder / image_name)
        (folder / "description.txt").write_text(item["description"], encoding="utf-8")
        (folder / "info.json").write_text(json.dumps({
            "source": "local-app-review-fixture", "artificial": True,
            "name": item["name"], "type": item["type"], "category": item["category"],
        }, ensure_ascii=False), encoding="utf-8")
        source_url = (f"{public_url}/static/review-source-zitronen-ricotta-pasta.html"
                      if item["slug"] == "zitronen-ricotta-pasta" else f"review-demo://{item['slug']}")
        recipe_id = db.recipe_upsert(
            url=source_url, name=item["name"], type=item["type"], category=item["category"],
            folder_path=str(folder), description=item["description"], thumb_filename=image_name,
            video_filename=None, source_added_at=time.time(),
        )
        db.recipe_apply_extraction_result(
            recipe_id, ingredients=_ingredient_rows(item["ingredients"]),
            steps=[{"instruction": text, "timer_seconds": timer} for text, timer in item["steps"]],
            servings=item["servings"], auto_tags=[], status="ok" if item["ingredients"] else "skipped",
        )
        db.recipe_tags_set(recipe_id, item["tags"])
        if item["ingredients"]:
            db.recipe_set_verified(recipe_id, True, "app-review")
        if item["slug"] in {"zitronen-ricotta-pasta", "ofengemuese-feta", "lachs-kraeuterkruste"}:
            db.meal_plan_add(planned_for=date.today().isoformat(), recipe_id=recipe_id,
                             planned_servings=item["servings"])
    for name, canonical, amount, unit, category, _checked in REVIEW_CART_ITEMS:
        prepared = prepare_for_cart(name, amount, unit)
        db.cart_add_or_merge(name=name, canonical_name=canonical, amount=prepared["amount"],
                             unit=prepared["unit"], source_recipe_id=None, category=category)
    name, canonical, amount, unit, category, interval = REVIEW_RECURRING_ITEM
    prepared = prepare_for_cart(name, amount, unit)
    db.recurring_create(name=name, canonical_name=canonical, amount=prepared["amount"],
                        unit=prepared["unit"], category=category, interval_days=interval,
                        next_due_on=date.today().isoformat(), active=True)
    return db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged local port.")
    prepare_fixture(args.directory, port=args.port, password=os.environ.get("APP_REVIEW_PASSWORD", ""))
    if args.seed_only:
        print("Artificial local fixture created with normal authentication.")
        return
    if not args.certificate or not args.key:
        parser.error("A local TLS certificate and key are required.")
    from app.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port,
                ssl_certfile=str(args.certificate), ssl_keyfile=str(args.key),
                proxy_headers=False)


if __name__ == "__main__":
    main()
