from pathlib import Path
import os
import shutil

import pytest
import yaml

from app.db import Database
from tools.setup_app_review_demo import RECIPES, seed_review_demo


ROOT = Path(__file__).resolve().parents[1]


def _review_inputs(tmp_path: Path) -> dict:
    assets = tmp_path / "assets"
    assets.mkdir()
    source_image = ROOT / "native-ios" / "assets" / "images" / "icon.png"
    for recipe in RECIPES:
        shutil.copy2(source_image, assets / f"{recipe['slug']}.png")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({
            "web": {"auth_disabled": True, "public_url": ""},
            "mail": {
                "recipe": {"enabled": True, "username": "private", "password": "private"},
                "wedding": {"enabled": True, "username": "private", "password": "private"},
            },
            "ai": {
                "openai": {"api_key": "private", "base_url": "https://private.invalid"},
                "auto_translate": True,
                "video_fallback": {"enabled": True},
            },
            "ytdlp": {"cookies_file": "/private/cookies", "expanded_tiktok_caption": True},
            "webhooks": [{"url": "https://private.invalid"}],
            "external_hdd": {"enabled": True},
            "einkauf": {"api_url": "https://private.invalid", "app_token": "private"},
        }, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "db_path": tmp_path / "review.db",
        "recipe_root": tmp_path / "recipes",
        "asset_root": assets,
        "config_path": config,
        "credential_output": tmp_path / "credentials.txt",
        "public_url": "https://rezepte-review.mausbaeren.me",
        "trusted_proxy_cidr": "192.168.1.141/32",
        "hostname": "rezepte-review",
    }


def test_review_demo_is_artificial_complete_and_sanitized(tmp_path: Path):
    inputs = _review_inputs(tmp_path)
    result = seed_review_demo(**inputs)
    db = Database(inputs["db_path"])
    config = yaml.safe_load(inputs["config_path"].read_text(encoding="utf-8"))

    assert result["recipes"] == 6
    assert result["complete_recipes"] == 5
    assert result["manual_care_recipes"] == 1
    assert db.recipe_count() == 6
    assert len(db.meal_plan_entries("2000-01-01", "2100-01-01")) == 3
    assert len(db.cart_list()) == 3
    assert db.user_get_by_name("app-review")["role"] == "user"
    assert len(list(inputs["recipe_root"].rglob("*.png"))) == 6
    if os.name != "nt":
        assert inputs["credential_output"].stat().st_mode & 0o777 == 0o600
    assert "Password:" in inputs["credential_output"].read_text(encoding="utf-8")
    assert config["web"]["auth_disabled"] is False
    assert config["web"]["public_url"] == inputs["public_url"]
    assert config["web"]["trusted_proxies"] == [
        "127.0.0.1/32",
        "::1/128",
        inputs["trusted_proxy_cidr"],
    ]
    assert config["mail"]["recipe"]["enabled"] is False
    assert config["mail"]["wedding"]["enabled"] is False
    assert config["ai"]["openai"]["api_key"] == ""
    assert config["ai"]["video_fallback"]["enabled"] is False
    assert config["webhooks"] == []
    assert config["external_hdd"]["enabled"] is False
    assert config["einkauf"]["app_token"] == ""


def test_review_demo_refuses_wrong_host_and_nonempty_instance(tmp_path: Path):
    inputs = _review_inputs(tmp_path)
    with pytest.raises(RuntimeError, match="nur auf Hostname"):
        seed_review_demo(**{**inputs, "hostname": "rezepte"})

    seed_review_demo(**inputs)
    inputs["credential_output"].unlink()
    with pytest.raises(RuntimeError, match="nicht leer"):
        seed_review_demo(**inputs)
