"""Installed-code checks using temporary data and mocked AI; no live records."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import yaml


def main() -> None:
    app_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(app_root))
    checks = {}
    with tempfile.TemporaryDirectory(prefix="rezepte-functional-probe-") as temporary:
        root = Path(temporary)
        config = yaml.safe_load((app_root / "config/config.example.yaml").read_text(encoding="utf-8"))
        for key, value in list(config["paths"].items()):
            if isinstance(value, str):
                config["paths"][key] = str(root / key)
        for key in ("data_dir", "db_path", "recipe_dir", "wedding_dir", "temp_dir", "logs_dir"):
            config["paths"][key] = str(root / key)
        for account in config["mail"].values():
            account.update(enabled=False, password="")
        config["ai"]["openai"]["api_key"] = ""
        config["ai"]["image_generation"]["enabled"] = False
        config["ai"]["video_fallback"]["enabled"] = False
        config["ai"]["auto_translate"] = False
        config_file = root / "config.yaml"
        config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
        os.environ["SCRAPPER_CONFIG"] = str(config_file)
        from app import __version__, auth, ai_budget, db as db_module
        from app.config_store import get_config
        from app.db import CURRENT_SCHEMA_VERSION, Database
        from app.recipes.pdf_recipe_extract import extract_recipe_data, parse_ingredient_lines, apply_extracted_recipe_data
        from app.recipes.cart_logic import prepare_for_cart
        from app.core.analyzer import OpenAIAnalyzer
        from app.core.email_processor import _ai_body_excerpt
        from app.recipes.shopping_catalog import infer_shopping_category
        from app.recipes.canonical import canonical_name
        Database.__init__.__defaults__ = (root / "fallback.db",)
        db = Database(root / "probe.db")
        db_module._db = db
        store = get_config()
        for value in ("false", "true", 1, None):
            store.set("web", "auth_disabled", value)
            assert auth.auth_disabled() is False
        store.set("web", "auth_disabled", False)
        checks["auth_strings_fail_closed"] = True
        for line, amount in (("1.5 kg Mehl", 1.5), ("1.000 g Mehl", 1000),
                             ("1/2 Bund Petersilie", .5), ("1 1/2 EL Olivenöl", 1.5), ("½ TL Salz", .5)):
            assert parse_ingredient_lines("Zutaten:\n" + line)[0]["amount"] == amount
        checks["pdf_decimal_and_fraction_amounts"] = True
        source = "Zutaten:\n250 g Mehl\nZubereitung:\nMehl verrühren."
        class Analyzer:
            def analyze_recipe_content(self, *_a, **_kw):
                return {"ingredients": [{"name": "Hummer", "amount": 250, "unit": "g", "raw": "250 g Mehl"}],
                        "steps": [{"instruction": "Verrühren."}], "confidence": .99}
        data = extract_recipe_data(source, analyzer=Analyzer())
        assert data.needs_review and data.ingredients[0]["name"] == "Mehl"
        rid = db.recipe_upsert(url="https://example.test/probe", name="Probe", type="Hauptgericht", category="Test",
                               folder_path=str(root / "recipe"), description=source,
                               thumb_filename=None, video_filename=None, source_added_at=1)
        apply_extracted_recipe_data(db, rid, data)
        assert db.recipe_get(rid)["ingredients_status"] == "error"
        checks["hallucinated_ai_cannot_replace_local_or_be_ready"] = True
        complete = OpenAIAnalyzer("mock-only-key")
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"finish_reason": "length", "message": {"content": "partial recipe"}}]}
        with patch.object(complete, "_request_with_retry", return_value=Response()):
            assert complete._call_vision("aGVsbG8=", "jpeg", "mock") is None
            assert complete._call("mock", "mock") is None
        checks["incomplete_text_and_vision_rejected"] = True
        safe = db.recipe_upsert(url="https://example.test/rice", name="Reis mit Weißwein", type="Hauptgericht", category="Test",
                                folder_path=str(root / "rice"), description=None,
                                thumb_filename=None, video_filename=None, source_added_at=1)
        assert safe in {row["id"] for row in db.recipe_list(search="ohne Ei")}
        phrase = db.recipe_upsert(url="https://example.test/beet", name="Salat mit rote Bete", type="Hauptgericht", category="Test",
                                  folder_path=str(root / "beet"), description=None,
                                  thumb_filename=None, video_filename=None, source_added_at=1)
        assert [row["id"] for row in db.recipe_list(search='"rote bete"')] == [phrase]
        checks["negative_search_and_phrases"] = True
        first = prepare_for_cart("Milch", .25, "l")
        item = db.cart_add_or_merge(**first, source_recipe_id=None)
        db.cart_update(item, checked=True)
        second = prepare_for_cart("Milch", 500, "ml")
        db.cart_add_or_merge(**second, source_recipe_id=None)
        assert db.cart_list()[0]["amount"] == 750 and not db.cart_list()[0]["checked"]
        checks["cart_converts_and_reopens_purchased_items"] = True
        assert canonical_name("Zwiebeln, gewürfelt") == "zwiebel"
        assert canonical_name("Butter (weich)") == "butter"
        assert canonical_name("Rote Bete") == "rote bete"
        assert infer_shopping_category("Tomatenmark") == "Vorrat & Konserven"
        assert infer_shopping_category("Teelicht") == "Drogerie & Haushalt"
        checks["canonical_names_and_product_categories"] = True
        assert _ai_body_excerpt("250 g Mehl\nViele Grüße\nprivate@example.test") == "250 g Mehl"
        checks["mail_signature_excerpt_removed"] = True
        store.set("ai", "openai", "server_daily_request_limit", 2)
        store.set("ai", "openai", "server_daily_image_limit", 1)
        def reserve(_index):
            try:
                ai_budget.reserve_request("POST", "/chat/completions")
                return True
            except ai_budget.AIBudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(reserve, range(6))) == 2
        assert Database(db.path).recipe_count() == 3
        checks["persistent_ai_budget_is_atomic"] = True
        assert db.path.is_relative_to(root) and store.path.is_relative_to(root)
        checks["all_probe_data_is_temporary"] = True
        assert all(checks.values())
        print(json.dumps({"version": __version__, "schema": CURRENT_SCHEMA_VERSION, "checks": checks}))
        db_module._db = None


if __name__ == "__main__":
    main()
