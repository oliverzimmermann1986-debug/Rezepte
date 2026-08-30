"""Create a sanitized, artificial dataset for the isolated Apple review server.

This tool deliberately refuses to run on any host except ``rezepte-review`` and
on any database that already contains recipe/import data. It never copies data
from the production instance and it does not print the generated password.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import stat
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import bcrypt
import yaml

from app.db import Database
from app.recipes.canonical import canonical_name
from app.recipes.source_integrity import normalize_source_text, source_fingerprint


REVIEW_HOSTNAME = "rezepte-review"
REVIEW_USERNAME = "app-review"

RECIPES: list[dict[str, Any]] = [
    {
        "slug": "zitronen-ricotta-pasta",
        "name": "Zitronen-Ricotta-Pasta",
        "type": "Hauptgericht",
        "category": "Pasta",
        "description": "Cremige Pasta mit Ricotta, frischer Zitrone und Basilikum.",
        "servings": 2,
        "rating": 5,
        "favorite": True,
        "tags": ["Schnell", "Vegetarisch"],
        "ingredients": [
            (250, "g", "Pasta"), (200, "g", "Ricotta"),
            (1, "Stück", "Bio-Zitrone"), (30, "g", "Parmesan"),
            (1, "Bund", "Basilikum"), (1, "Prise", "Pfeffer"),
        ],
        "steps": [
            ("Pasta in Salzwasser bissfest kochen und etwas Kochwasser auffangen.", 600),
            ("Ricotta mit Zitronenabrieb, Saft und Parmesan verrühren.", None),
            ("Pasta und etwas Kochwasser unterheben, abschmecken und mit Basilikum servieren.", None),
        ],
    },
    {
        "slug": "ofengemuese-feta",
        "name": "Buntes Ofengemüse mit Feta",
        "type": "Hauptgericht",
        "category": "Vegetarisch",
        "description": "Geröstetes Sommergemüse mit Feta, Kräutern und Zitronendressing.",
        "servings": 4,
        "rating": 4,
        "favorite": False,
        "tags": ["Ofengericht", "Vegetarisch"],
        "ingredients": [
            (2, "Stück", "Zucchini"), (2, "Stück", "Paprika"),
            (250, "g", "Cherrytomaten"), (200, "g", "Feta"),
            (3, "EL", "Olivenöl"), (1, "TL", "Oregano"),
        ],
        "steps": [
            ("Backofen auf 210 Grad Ober-/Unterhitze vorheizen.", None),
            ("Gemüse schneiden, mit Öl und Oregano mischen und auf einem Blech verteilen.", None),
            ("25 Minuten rösten, Feta darüberbröseln und weitere 5 Minuten backen.", 1800),
        ],
    },
    {
        "slug": "kuerbissuppe",
        "name": "Cremige Kürbissuppe",
        "type": "Vorspeise",
        "category": "Suppe",
        "description": "Wärmende Hokkaido-Suppe mit Ingwer und einem Klecks Joghurt.",
        "servings": 4,
        "rating": 5,
        "favorite": True,
        "tags": ["Herbst", "Vegetarisch"],
        "ingredients": [
            (1, "Stück", "Hokkaido-Kürbis"), (1, "Stück", "Zwiebel"),
            (20, "g", "Ingwer"), (750, "ml", "Gemüsebrühe"),
            (100, "g", "Joghurt"), (1, "EL", "Olivenöl"),
        ],
        "steps": [
            ("Kürbis entkernen und würfeln; Zwiebel und Ingwer fein schneiden.", None),
            ("Alles kurz in Öl anschwitzen, Brühe angießen und weich köcheln.", 1200),
            ("Fein pürieren, abschmecken und mit Joghurt servieren.", None),
        ],
    },
    {
        "slug": "beeren-pancakes",
        "name": "Fluffige Beeren-Pancakes",
        "type": "Frühstück",
        "category": "Süß",
        "description": "Lockere Pancakes mit frischen Beeren und Joghurt.",
        "servings": 3,
        "rating": 4,
        "favorite": False,
        "tags": ["Frühstück", "Familie"],
        "ingredients": [
            (220, "g", "Mehl"), (2, "Stück", "Eier"),
            (300, "ml", "Milch"), (1, "TL", "Backpulver"),
            (200, "g", "Beeren"), (150, "g", "Joghurt"),
        ],
        "steps": [
            ("Mehl und Backpulver mischen, Eier und Milch glatt einrühren.", None),
            ("Teig portionsweise in einer beschichteten Pfanne goldbraun backen.", 240),
            ("Mit Beeren und Joghurt anrichten.", None),
        ],
    },
    {
        "slug": "lachs-kraeuterkruste",
        "name": "Lachs mit Kräuterkruste",
        "type": "Hauptgericht",
        "category": "Fisch",
        "description": "Saftiges Lachsfilet mit knuspriger Kräuterkruste und grünem Gemüse.",
        "servings": 2,
        "rating": 5,
        "favorite": False,
        "tags": ["Fisch", "Wochenende"],
        "ingredients": [
            (2, "Stück", "Lachsfilet"), (40, "g", "Semmelbrösel"),
            (1, "Bund", "Petersilie"), (1, "Stück", "Zitrone"),
            (300, "g", "Brokkoli"), (1, "EL", "Olivenöl"),
        ],
        "steps": [
            ("Backofen auf 200 Grad vorheizen und Lachs in eine Form legen.", None),
            ("Brösel, Petersilie, Zitronenabrieb und Öl mischen und auf dem Lachs verteilen.", None),
            ("Lachs backen und Brokkoli parallel bissfest garen.", 900),
        ],
    },
    {
        "slug": "tomaten-galette",
        "name": "Sommerliche Tomaten-Galette",
        "type": "Hauptgericht",
        "category": "Vegetarisch",
        "description": "Ein absichtlich unvollständiges Bildrezept für den Filter „Manuelle Pflege“.",
        "servings": 4,
        "rating": 0,
        "favorite": False,
        "tags": ["Manuelle Pflege"],
        "ingredients": [],
        "steps": [
            ("Teig ausrollen, belegen und die Ränder locker einschlagen.", None),
            ("Goldbraun backen und vor dem Anschneiden kurz ruhen lassen.", 2100),
        ],
    },
]


def _assert_review_environment(hostname: str, public_url: str) -> None:
    if hostname != REVIEW_HOSTNAME:
        raise RuntimeError(
            f"Abbruch: dieses Tool läuft nur auf Hostname {REVIEW_HOSTNAME!r}, nicht {hostname!r}."
        )
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or parsed.netloc != "rezepte-review.mausbaeren.me":
        raise RuntimeError("Abbruch: unerwartete öffentliche Review-URL.")


def _assert_empty_review_data(db: Database, recipe_root: Path) -> None:
    with db.conn() as connection:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("recipes", "pending", "history")
        }
    if any(counts.values()):
        raise RuntimeError(f"Abbruch: Review-Datenbank ist nicht leer: {counts}")
    if recipe_root.exists() and any(recipe_root.iterdir()):
        raise RuntimeError(f"Abbruch: Review-Rezeptordner ist nicht leer: {recipe_root}")


def _sanitize_config(config_path: Path, public_url: str, trusted_proxy_cidr: str) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    web = config.setdefault("web", {})
    web.update({
        "auth_disabled": False,
        "external_logout_url": "",
        "public_url": public_url,
        "trusted_proxies": ["127.0.0.1/32", "::1/128", trusted_proxy_cidr],
    })
    for mailbox in ("recipe", "wedding"):
        section = config.setdefault("mail", {}).setdefault(mailbox, {})
        section.update({"enabled": False, "username": "", "password": ""})
    ai = config.setdefault("ai", {})
    ai.setdefault("openai", {}).update({"api_key": "", "base_url": ""})
    ai["auto_translate"] = False
    ai.setdefault("video_fallback", {})["enabled"] = False
    config.setdefault("ytdlp", {}).update({"cookies_file": "", "expanded_tiktok_caption": False})
    config["webhooks"] = []
    config.setdefault("external_hdd", {})["enabled"] = False
    config.setdefault("einkauf", {}).update({
        "api_url": "", "app_token": "", "cf_access_client_id": "",
        "cf_access_client_secret": "",
    })
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _ingredient_rows(values: list[tuple[Any, str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "amount": amount,
            "unit": unit,
            "name": name,
            "canonical_name": canonical_name(name),
            "raw": f"{amount} {unit} {name}",
        }
        for amount, unit, name in values
    ]


def seed_review_demo(
    *,
    db_path: Path,
    recipe_root: Path,
    asset_root: Path,
    config_path: Path,
    credential_output: Path,
    public_url: str,
    trusted_proxy_cidr: str,
    hostname: str,
) -> dict[str, Any]:
    _assert_review_environment(hostname, public_url)
    if credential_output.exists():
        raise RuntimeError(f"Abbruch: Zugangsdokument existiert bereits: {credential_output}")
    missing_assets = [item["slug"] for item in RECIPES if not (asset_root / f"{item['slug']}.png").is_file()]
    if missing_assets:
        raise RuntimeError(f"Abbruch: Review-Bilder fehlen: {', '.join(missing_assets)}")

    db = Database(db_path)
    _assert_empty_review_data(db, recipe_root)
    recipe_root.mkdir(parents=True, exist_ok=True)
    _sanitize_config(config_path, public_url, trusted_proxy_cidr)

    password = secrets.token_urlsafe(20)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
    existing_user = db.user_get_by_name(REVIEW_USERNAME)
    if existing_user:
        db.user_update_security(
            int(existing_user["id"]),
            password_hash=password_hash,
            role="admin",
            disabled=False,
        )
    else:
        db.user_create(REVIEW_USERNAME, password_hash, role="admin")

    created_ids: list[int] = []
    for offset, item in enumerate(RECIPES):
        folder = recipe_root / item["type"] / item["category"] / item["slug"]
        folder.mkdir(parents=True, exist_ok=False)
        image_name = f"{item['slug']}.png"
        shutil.copy2(asset_root / image_name, folder / image_name)
        (folder / "description.txt").write_text(item["description"], encoding="utf-8")
        (folder / "info.json").write_text(
            json.dumps(
                {
                    "source": "app-review-demo",
                    "artificial": True,
                    "name": item["name"],
                    "type": item["type"],
                    "category": item["category"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        recipe_id = db.recipe_upsert(
            url=f"review-demo://{item['slug']}",
            name=item["name"],
            type=item["type"],
            category=item["category"],
            folder_path=str(folder),
            description=item["description"],
            thumb_filename=image_name,
            video_filename=None,
            source_added_at=time.time() - offset * 3600,
        )
        db.recipe_apply_extraction_result(
            recipe_id,
            ingredients=_ingredient_rows(item["ingredients"]),
            steps=[{"instruction": text, "timer_seconds": timer} for text, timer in item["steps"]],
            servings=item["servings"],
            auto_tags=[],
            status="ok" if item["ingredients"] else "skipped",
        )
        db.recipe_tags_set(recipe_id, item["tags"])
        if item["ingredients"]:
            db.recipe_set_verified(recipe_id, True, REVIEW_USERNAME)
        with db.conn() as connection:
            connection.execute(
                "UPDATE recipes SET is_favorite=?, rating=? WHERE id=?",
                (int(item["favorite"]), int(item["rating"]), recipe_id),
            )
        created_ids.append(recipe_id)

    # Sichtbarer, rein künstlicher Quellenänderungs-Fall für den Review-Ablauf.
    # Die beiden Snapshots verändern das gespeicherte Rezept ausdrücklich nicht.
    demo_source_url = "review-demo://zitronen-ricotta-pasta"
    baseline_text = normalize_source_text(
        "Zitronen-Ricotta-Pasta\n250 g Pasta\n200 g Ricotta\n1 Bio-Zitrone"
    )
    changed_text = normalize_source_text(
        "Zitronen-Ricotta-Pasta\n250 g Pasta\n250 g Ricotta\n2 Bio-Zitronen"
    )
    db.recipe_source_snapshot_create(
        created_ids[0],
        source_url=demo_source_url,
        content_sha256=source_fingerprint(baseline_text),
        content_text=baseline_text,
        state="baseline",
        checked_at=time.time() - 86400,
        baseline_if_missing=True,
        description_source="app-review-demo",
    )
    db.recipe_source_snapshot_create(
        created_ids[0],
        source_url=demo_source_url,
        content_sha256=source_fingerprint(changed_text),
        content_text=changed_text,
        state="changed",
        checked_at=time.time(),
        description_source="app-review-demo",
    )

    monday = date.today() - timedelta(days=date.today().weekday())
    for day_offset, recipe_index, servings in ((0, 0, 2), (2, 1, 4), (4, 4, 2)):
        db.meal_plan_add(
            planned_for=(monday + timedelta(days=day_offset)).isoformat(),
            recipe_id=created_ids[recipe_index],
            planned_servings=servings,
        )
    cart_ids = [
        db.cart_add_or_merge(name="Zitronen", canonical_name="zitrone", amount=2, unit="Stück", source_recipe_id=None),
        db.cart_add_or_merge(name="Hafermilch", canonical_name="hafermilch", amount=1, unit="l", source_recipe_id=None),
        db.cart_add_or_merge(name="Küchenpapier", canonical_name="küchenpapier", amount=1, unit="Stück", source_recipe_id=None),
    ]
    with db.conn() as connection:
        connection.execute("UPDATE shopping_cart SET checked=1 WHERE id=?", (cart_ids[-1],))
    db.recurring_create(
        name="Hafermilch",
        canonical_name="hafermilch",
        amount=1,
        unit="l",
        category="Kühlregal",
        interval_days=7,
        next_due_on=date.today().isoformat(),
        active=True,
    )
    db.recurring_run_due(due_on=date.today())

    credential_output.parent.mkdir(parents=True, exist_ok=True)
    credential_output.write_text(
        "\n".join([
            "Rezepte App Store Review",
            f"Server: {public_url}",
            f"Username: {REVIEW_USERNAME}",
            f"Password: {password}",
            "Role: review administrator (isolated demo instance only)",
            "Data: artificial demo recipes only",
            "",
        ]),
        encoding="utf-8",
    )
    credential_output.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return {
        "ok": True,
        "recipes": len(created_ids),
        "complete_recipes": sum(bool(item["ingredients"]) for item in RECIPES),
        "manual_care_recipes": sum(not item["ingredients"] for item in RECIPES),
        "meal_plan_entries": 3,
        "shopping_items": 3,
        "recurring_items": 1,
        "source_change_demos": 1,
        "review_username": REVIEW_USERNAME,
        "credential_output": str(credential_output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("/opt/scrapper/data/scrapper.db"))
    parser.add_argument("--recipe-root", type=Path, default=Path("/opt/scrapper/files/rezepte"))
    parser.add_argument("--asset-root", type=Path, default=Path("/opt/scrapper/review-demo/assets"))
    parser.add_argument("--config", type=Path, default=Path("/opt/scrapper/data/config.yaml"))
    parser.add_argument(
        "--credential-output",
        type=Path,
        default=Path("/root/rezepte-app-review-credentials.txt"),
    )
    parser.add_argument("--public-url", default="https://rezepte-review.mausbaeren.me")
    parser.add_argument("--trusted-proxy-cidr", default="192.168.1.141/32")
    args = parser.parse_args()
    result = seed_review_demo(
        db_path=args.db,
        recipe_root=args.recipe_root,
        asset_root=args.asset_root,
        config_path=args.config,
        credential_output=args.credential_output,
        public_url=args.public_url,
        trusted_proxy_cidr=args.trusted_proxy_cidr,
        hostname=socket.gethostname(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
