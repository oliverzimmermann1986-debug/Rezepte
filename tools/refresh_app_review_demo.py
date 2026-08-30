"""Refresh the existing, isolated Apple App Review dataset after an update.

Unlike :mod:`tools.setup_app_review_demo`, this command is deliberately a
fixer for an already populated review instance.  It refuses production-like
data, takes and verifies an online SQLite backup, and then applies the small
demo migration in one ``BEGIN IMMEDIATE`` transaction.  The artificial source
history, meal plan, shopping cart and recurring purchase are restored to their
documented review state.  Credentials, users, recipe variants and integration
configuration are never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from app.recipes.cart_logic import prepare_for_cart
from app.recipes.shopping_catalog import SHOPPING_CATEGORY_ICONS
from app.recipes.source_integrity import normalize_source_text, source_fingerprint
from tools.setup_app_review_demo import (
    RECIPES,
    REVIEW_CART_ITEMS,
    REVIEW_HOSTNAME,
    REVIEW_PLAN_RECIPES,
    REVIEW_PLAN_WEEKS,
    REVIEW_PUBLIC_URL,
    REVIEW_RECURRING_ITEM,
    REVIEW_SOURCE_BASELINE_TEXT,
    REVIEW_SOURCE_CURRENT_TEXT,
    REVIEW_SOURCE_DESCRIPTION_SOURCE,
    REVIEW_SOURCE_TITLE,
    REVIEW_USERNAME,
    review_source_url,
)


LEGACY_SOURCE_URL = "review-demo://zitronen-ricotta-pasta"
REVIEW_PROVENANCE_SOURCE = "app-review-demo"
PLAN_RECIPES = REVIEW_PLAN_RECIPES
_RECIPE_NAMES = {str(item["slug"]): str(item["name"]) for item in RECIPES}
_MISSING = object()


def _connect_existing(db_path: Path) -> sqlite3.Connection:
    try:
        resolved = db_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Abbruch: Review-Datenbank fehlt: {db_path}") from exc
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=rw",
        uri=True,
        timeout=30,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _assert_exact_review_environment(hostname: str, public_url: str) -> None:
    if hostname != REVIEW_HOSTNAME:
        raise RuntimeError(
            f"Abbruch: Review-Aktualisierung ist nur auf Hostname "
            f"{REVIEW_HOSTNAME!r} erlaubt, nicht {hostname!r}."
        )
    if public_url != REVIEW_PUBLIC_URL:
        raise RuntimeError(
            "Abbruch: öffentliche Review-URL muss exakt "
            f"{REVIEW_PUBLIC_URL!r} sein."
        )


def _nested_value(config: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _assert_sanitized_config(config_path: Path, public_url: str) -> None:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Abbruch: Review-Konfiguration ist nicht lesbar: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise RuntimeError("Abbruch: Review-Konfiguration ist kein YAML-Objekt.")

    expected: tuple[tuple[tuple[str, ...], Any], ...] = (
        (("web", "auth_disabled"), False),
        (("web", "public_url"), public_url),
        (("mail", "recipe", "enabled"), False),
        (("mail", "recipe", "username"), ""),
        (("mail", "recipe", "password"), ""),
        (("mail", "wedding", "enabled"), False),
        (("mail", "wedding", "username"), ""),
        (("mail", "wedding", "password"), ""),
        (("ai", "openai", "api_key"), ""),
        (("ai", "openai", "base_url"), ""),
        (("ai", "auto_translate"), False),
        (("ai", "video_fallback", "enabled"), False),
        (("ytdlp", "cookies_file"), ""),
        (("ytdlp", "expanded_tiktok_caption"), False),
        (("webhooks",), []),
        (("external_hdd", "enabled"), False),
        (("einkauf", "api_url"), ""),
        (("einkauf", "app_token"), ""),
        (("einkauf", "cf_access_client_id"), ""),
        (("einkauf", "cf_access_client_secret"), ""),
    )
    unsafe: list[str] = []
    for path, expected_value in expected:
        actual = _nested_value(loaded, path)
        if type(actual) is not type(expected_value) or actual != expected_value:
            unsafe.append(".".join(path))
    if unsafe:
        raise RuntimeError(
            "Abbruch: sensible Review-Integrationen sind nicht bereinigt: "
            + ", ".join(unsafe)
        )


def _assert_required_tables(connection: sqlite3.Connection) -> None:
    required = {
        "meal_plan_entries",
        "recipe_source_snapshots",
        "recipes",
        "shopping_cart",
        "shopping_products",
        "shopping_recurring",
        "users",
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            "Abbruch: Review-Datenbankschema ist unvollständig: " + ", ".join(missing)
        )


def _assert_review_database(
    connection: sqlite3.Connection,
) -> tuple[list[sqlite3.Row], sqlite3.Row]:
    _assert_required_tables(connection)
    review_user = connection.execute(
        "SELECT id, username, password_hash, role, disabled, session_version "
        "FROM users WHERE username=? COLLATE NOCASE",
        (REVIEW_USERNAME,),
    ).fetchone()
    if (
        review_user is None
        or str(review_user["role"]) != "admin"
        or int(review_user["disabled"] or 0) != 0
    ):
        raise RuntimeError(
            f"Abbruch: aktives Review-Admin-Konto {REVIEW_USERNAME!r} fehlt."
        )
    active = connection.execute(
        "SELECT id, url, name, folder_path FROM recipes "
        "WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    if not active:
        raise RuntimeError("Abbruch: Review-Instanz enthält keine aktiven Rezepte.")
    return list(active), review_user


def _assert_active_recipe_provenance(
    active_recipes: Iterable[sqlite3.Row], recipe_root: Path
) -> dict[str, sqlite3.Row]:
    try:
        root = recipe_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Abbruch: Review-Rezeptordner fehlt: {recipe_root}") from exc
    if not root.is_dir():
        raise RuntimeError(f"Abbruch: Review-Rezeptpfad ist kein Ordner: {root}")

    by_slug: dict[str, sqlite3.Row] = {}
    for recipe in active_recipes:
        raw_folder = Path(str(recipe["folder_path"] or ""))
        if not raw_folder.is_absolute():
            raise RuntimeError(
                f"Abbruch: Rezept {int(recipe['id'])} hat keinen absoluten Ordnerpfad."
            )
        try:
            folder = raw_folder.resolve(strict=True)
            folder.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise RuntimeError(
                f"Abbruch: Rezept {int(recipe['id'])} liegt außerhalb der Review-Daten."
            ) from exc
        info_path = folder / "info.json"
        if info_path.is_symlink() or not info_path.is_file():
            raise RuntimeError(
                f"Abbruch: Provenienzdatei fehlt für Rezept {int(recipe['id'])}: {info_path}"
            )
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Abbruch: Provenienzdatei ist ungültig für Rezept {int(recipe['id'])}."
            ) from exc
        if not isinstance(info, dict) or (
            info.get("source") != REVIEW_PROVENANCE_SOURCE
            or info.get("artificial") is not True
        ):
            raise RuntimeError(
                f"Abbruch: Rezept {int(recipe['id'])} ist nicht als künstliche "
                "App-Review-Demo markiert."
            )
        slug = folder.name
        if slug in by_slug:
            raise RuntimeError(f"Abbruch: doppelter aktiver Review-Slug: {slug}")
        by_slug[slug] = recipe

    for slug, _servings in PLAN_RECIPES:
        recipe = by_slug.get(slug)
        if recipe is None or str(recipe["name"]) != _RECIPE_NAMES[slug]:
            raise RuntimeError(f"Abbruch: erwartetes Basisrezept fehlt: {slug}")
    lemon_recipe = by_slug[PLAN_RECIPES[0][0]]
    if str(lemon_recipe["url"] or "") not in {
        LEGACY_SOURCE_URL,
        review_source_url(REVIEW_PUBLIC_URL),
    }:
        raise RuntimeError("Abbruch: Zitronen-Ricotta-Pasta hat eine fremde Quell-URL.")
    return by_slug


def _snapshot_rows_are_current(rows: Sequence[sqlite3.Row], source_url: str) -> bool:
    if len(rows) != 2:
        return False
    ordered = sorted(rows, key=lambda row: (float(row["checked_at"]), int(row["id"])))
    baseline, current = ordered
    baseline_text = normalize_source_text(REVIEW_SOURCE_BASELINE_TEXT)
    current_text = normalize_source_text(REVIEW_SOURCE_CURRENT_TEXT)

    def matches(
        row: sqlite3.Row, *, text: str, state: str, is_baseline: int
    ) -> bool:
        expected = {
            "source_url": source_url,
            "observed_url": source_url,
            "content_sha256": source_fingerprint(text),
            "content_text": text,
            "page_title": REVIEW_SOURCE_TITLE,
            "description_source": REVIEW_SOURCE_DESCRIPTION_SOURCE,
            "state": state,
            "error": None,
            "is_baseline": is_baseline,
            "accepted_at": None,
            "accepted_by": None,
        }
        return all(row[key] == value for key, value in expected.items())

    return matches(baseline, text=baseline_text, state="baseline", is_baseline=1) and matches(
        current,
        text=current_text,
        state="changed",
        is_baseline=0,
    )


def _meal_plan_is_current(
    rows: Sequence[sqlite3.Row], monday: date, base_by_slug: Mapping[str, sqlite3.Row]
) -> bool:
    if len(rows) != REVIEW_PLAN_WEEKS * len(PLAN_RECIPES):
        return False
    row_index = 0
    for week_offset in range(REVIEW_PLAN_WEEKS):
        expected_date = (monday + timedelta(weeks=week_offset)).isoformat()
        for sort_order, (slug, servings) in enumerate(PLAN_RECIPES):
            row = rows[row_index]
            row_index += 1
            if (
                str(row["planned_for"]) != expected_date
                or int(row["recipe_id"]) != int(base_by_slug[slug]["id"])
                or int(row["planned_servings"]) != servings
                or int(row["sort_order"]) != sort_order
            ):
                return False
    return True


def _review_recurring_next_due(
    recurring_rows: Sequence[sqlite3.Row], effective_today: date
) -> str:
    name, canonical, amount, unit, _category, interval_days = REVIEW_RECURRING_ITEM
    default_due = effective_today + timedelta(days=interval_days)
    if len(recurring_rows) != 1:
        return default_due.isoformat()
    row = recurring_rows[0]
    prepared = prepare_for_cart(name, amount, unit)
    try:
        candidate = date.fromisoformat(str(row["next_due_on"]))
        row_interval = int(row["interval_days"])
    except (TypeError, ValueError):
        return default_due.isoformat()
    if (
        str(row["canonical_name"]) != canonical
        or str(row["unit"]) != str(prepared["unit"])
        or row_interval != interval_days
        or not effective_today < candidate <= default_due
    ):
        return default_due.isoformat()
    return candidate.isoformat()


def _shopping_state_is_current(
    cart_rows: Sequence[sqlite3.Row],
    recurring_rows: Sequence[sqlite3.Row],
    product_rows: Sequence[sqlite3.Row],
    *,
    expected_next_due: str,
) -> bool:
    if len(cart_rows) != len(REVIEW_CART_ITEMS) or len(recurring_rows) != 1:
        return False

    expected_cart = sorted(REVIEW_CART_ITEMS, key=lambda item: item[1])
    ordered_cart = sorted(
        cart_rows,
        key=lambda row: (str(row["canonical_name"] or "").casefold(), int(row["id"])),
    )
    for row, expected in zip(ordered_cart, expected_cart):
        name, canonical, amount, unit, category, checked = expected
        prepared = prepare_for_cart(name, amount, unit)
        try:
            source_recipe_ids = json.loads(row["source_recipe_ids"] or "[]")
            actual_amount = float(row["amount"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if (
            str(row["name"]) != name
            or str(row["canonical_name"]) != canonical
            or actual_amount != float(prepared["amount"])
            or str(row["unit"]) != str(prepared["unit"])
            or str(row["category"]) != category
            or int(row["checked"]) != int(checked)
            or source_recipe_ids != []
            or row["sort_order"] is not None
        ):
            return False

    recurring = recurring_rows[0]
    name, canonical, amount, unit, category, interval_days = REVIEW_RECURRING_ITEM
    prepared_recurring = prepare_for_cart(name, amount, unit)
    try:
        recurring_amount = float(recurring["amount"])
    except (TypeError, ValueError):
        return False
    if (
        str(recurring["name"]) != name
        or str(recurring["canonical_name"]) != canonical
        or recurring_amount != float(prepared_recurring["amount"])
        or str(recurring["unit"]) != str(prepared_recurring["unit"])
        or str(recurring["category"]) != category
        or int(recurring["interval_days"]) != interval_days
        or str(recurring["next_due_on"]) != expected_next_due
        or int(recurring["active"]) != 1
        or recurring["last_added_at"] is None
    ):
        return False

    expected_products = {
        canonical: (
            name,
            category,
            str(prepare_for_cart(name, amount, unit)["unit"]),
        )
        for name, canonical, amount, unit, category, _checked in REVIEW_CART_ITEMS
    }
    if len(product_rows) != len(expected_products):
        return False
    for row in product_rows:
        canonical = str(row["canonical_name"]).casefold()
        expected = expected_products.get(canonical)
        if expected is None:
            return False
        display_name, category, default_unit = expected
        if (
            str(row["display_name"]) != display_name
            or str(row["category"]) != category
            or str(row["icon"]) != SHOPPING_CATEGORY_ICONS[category]
            or str(row["default_unit"]) != default_unit
        ):
            return False
    return True


def _apply_transactional_refresh(
    connection: sqlite3.Connection,
    *,
    base_by_slug: Mapping[str, sqlite3.Row],
    monday: date,
    effective_today: date,
) -> dict[str, Any]:
    source_url = review_source_url(REVIEW_PUBLIC_URL)
    lemon_id = int(base_by_slug[PLAN_RECIPES[0][0]]["id"])
    conflict = connection.execute(
        "SELECT id FROM recipes WHERE url=? AND id<>?",
        (source_url, lemon_id),
    ).fetchone()
    if conflict is not None:
        raise RuntimeError(
            "Abbruch: künstliche HTTPS-Review-Quelle ist bereits einem anderen "
            "Rezept zugeordnet."
        )

    snapshot_rows = connection.execute(
        "SELECT * FROM recipe_source_snapshots WHERE recipe_id=? "
        "ORDER BY checked_at, id",
        (lemon_id,),
    ).fetchall()
    meal_rows = connection.execute(
        "SELECT * FROM meal_plan_entries ORDER BY planned_for, sort_order, id"
    ).fetchall()
    cart_rows = connection.execute("SELECT * FROM shopping_cart ORDER BY id").fetchall()
    recurring_rows = connection.execute(
        "SELECT * FROM shopping_recurring ORDER BY id"
    ).fetchall()
    product_canonicals = tuple(item[1] for item in REVIEW_CART_ITEMS)
    product_rows = connection.execute(
        "SELECT * FROM shopping_products WHERE canonical_name IN ("
        + ",".join("?" for _item in product_canonicals)
        + ") ORDER BY canonical_name COLLATE NOCASE",
        product_canonicals,
    ).fetchall()
    expected_next_due = _review_recurring_next_due(recurring_rows, effective_today)
    url_changed = str(base_by_slug[PLAN_RECIPES[0][0]]["url"] or "") != source_url
    snapshots_changed = not _snapshot_rows_are_current(snapshot_rows, source_url)
    meal_plan_changed = not _meal_plan_is_current(meal_rows, monday, base_by_slug)
    shopping_changed = not _shopping_state_is_current(
        cart_rows,
        recurring_rows,
        product_rows,
        expected_next_due=expected_next_due,
    )

    now = time.time()
    if url_changed:
        connection.execute("UPDATE recipes SET url=? WHERE id=?", (source_url, lemon_id))
    if snapshots_changed:
        baseline_text = normalize_source_text(REVIEW_SOURCE_BASELINE_TEXT)
        current_text = normalize_source_text(REVIEW_SOURCE_CURRENT_TEXT)
        connection.execute(
            "DELETE FROM recipe_source_snapshots WHERE recipe_id=?",
            (lemon_id,),
        )
        snapshot_sql = (
            "INSERT INTO recipe_source_snapshots ("
            "recipe_id, source_url, observed_url, content_sha256, content_text, "
            "page_title, description_source, checked_at, state, error, "
            "is_baseline, accepted_at, accepted_by"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL)"
        )
        connection.execute(
            snapshot_sql,
            (
                lemon_id,
                source_url,
                source_url,
                source_fingerprint(baseline_text),
                baseline_text,
                REVIEW_SOURCE_TITLE,
                REVIEW_SOURCE_DESCRIPTION_SOURCE,
                now - 86400,
                "baseline",
                1,
            ),
        )
        connection.execute(
            snapshot_sql,
            (
                lemon_id,
                source_url,
                source_url,
                source_fingerprint(current_text),
                current_text,
                REVIEW_SOURCE_TITLE,
                REVIEW_SOURCE_DESCRIPTION_SOURCE,
                now,
                "changed",
                0,
            ),
        )
    if meal_plan_changed:
        connection.execute("DELETE FROM meal_plan_entries")
        for week_offset in range(REVIEW_PLAN_WEEKS):
            planned_for = (monday + timedelta(weeks=week_offset)).isoformat()
            for sort_order, (slug, servings) in enumerate(PLAN_RECIPES):
                connection.execute(
                    "INSERT INTO meal_plan_entries ("
                    "planned_for, recipe_id, planned_servings, sort_order, "
                    "created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        planned_for,
                        int(base_by_slug[slug]["id"]),
                        servings,
                        sort_order,
                        now,
                        now,
                    ),
                )
    if shopping_changed:
        connection.execute("DELETE FROM shopping_cart")
        connection.execute("DELETE FROM shopping_recurring")
        for name, canonical, amount, unit, category, checked in REVIEW_CART_ITEMS:
            prepared = prepare_for_cart(name, amount, unit)
            connection.execute(
                "INSERT INTO shopping_products ("
                "canonical_name, display_name, category, icon, default_unit, "
                "usage_count, recipe_count, last_used_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 0, 0, NULL, ?) "
                "ON CONFLICT(canonical_name) DO UPDATE SET "
                "display_name=excluded.display_name, category=excluded.category, "
                "icon=excluded.icon, default_unit=excluded.default_unit, "
                "updated_at=excluded.updated_at",
                (
                    canonical,
                    name,
                    category,
                    SHOPPING_CATEGORY_ICONS[category],
                    prepared["unit"],
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO shopping_cart ("
                "name, canonical_name, amount, unit, checked, added_at, "
                "source_recipe_ids, category, sort_order"
                ") VALUES (?, ?, ?, ?, ?, ?, '[]', ?, NULL)",
                (
                    name,
                    canonical,
                    prepared["amount"],
                    prepared["unit"],
                    1 if checked else 0,
                    now,
                    category,
                ),
            )
        name, canonical, amount, unit, category, interval_days = REVIEW_RECURRING_ITEM
        prepared_recurring = prepare_for_cart(name, amount, unit)
        connection.execute(
            "INSERT INTO shopping_recurring ("
            "name, canonical_name, amount, unit, category, interval_days, "
            "next_due_on, active, last_added_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                name,
                canonical,
                prepared_recurring["amount"],
                prepared_recurring["unit"],
                category,
                interval_days,
                expected_next_due,
                now,
                now,
                now,
            ),
        )

    refreshed_recipe = connection.execute(
        "SELECT id, url, name, folder_path FROM recipes WHERE id=?",
        (lemon_id,),
    ).fetchone()
    refreshed_by_slug = dict(base_by_slug)
    refreshed_by_slug[PLAN_RECIPES[0][0]] = refreshed_recipe
    final_snapshots = connection.execute(
        "SELECT * FROM recipe_source_snapshots WHERE recipe_id=? "
        "ORDER BY checked_at, id",
        (lemon_id,),
    ).fetchall()
    final_meals = connection.execute(
        "SELECT * FROM meal_plan_entries ORDER BY planned_for, sort_order, id"
    ).fetchall()
    final_cart = connection.execute("SELECT * FROM shopping_cart ORDER BY id").fetchall()
    final_recurring = connection.execute(
        "SELECT * FROM shopping_recurring ORDER BY id"
    ).fetchall()
    final_products = connection.execute(
        "SELECT * FROM shopping_products WHERE canonical_name IN ("
        + ",".join("?" for _item in product_canonicals)
        + ") ORDER BY canonical_name COLLATE NOCASE",
        product_canonicals,
    ).fetchall()
    if str(refreshed_recipe["url"]) != source_url:
        raise RuntimeError("Review-Quell-URL wurde nicht atomar aktualisiert.")
    if not _snapshot_rows_are_current(final_snapshots, source_url):
        raise RuntimeError("Review-Quell-Snapshots entsprechen nicht dem Sollzustand.")
    if not _meal_plan_is_current(final_meals, monday, refreshed_by_slug):
        raise RuntimeError("Review-Wochenplan entspricht nicht dem Sollzustand.")
    if not _shopping_state_is_current(
        final_cart,
        final_recurring,
        final_products,
        expected_next_due=expected_next_due,
    ):
        raise RuntimeError("Review-Einkaufsliste entspricht nicht dem Sollzustand.")

    return {
        "changed": bool(
            url_changed or snapshots_changed or meal_plan_changed or shopping_changed
        ),
        "url_changed": url_changed,
        "snapshots_changed": snapshots_changed,
        "meal_plan_changed": meal_plan_changed,
        "shopping_changed": shopping_changed,
        "recipe_id": lemon_id,
        "source_url": source_url,
        "meal_plan_date": monday.isoformat(),
        "meal_plan_entries": len(final_meals),
        "source_snapshots": len(final_snapshots),
        "shopping_cart_items": len(final_cart),
        "shopping_recurring_items": len(final_recurring),
    }


def _create_verified_backup(db_path: Path, backup_dir: Path) -> dict[str, Any]:
    if backup_dir.is_symlink():
        raise RuntimeError(f"Abbruch: Backupordner darf kein Symlink sein: {backup_dir}")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = backup_dir / (
        f"app-review-refresh-{stamp}-{os.getpid()}-{time.time_ns()}.db"
    )
    temporary = backup_dir / f".{final.name}.tmp"
    file_descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_RDWR,
        0o600,
    )
    os.close(file_descriptor)
    try:
        source = _connect_existing(db_path)
        try:
            destination = sqlite3.connect(str(temporary), timeout=30)
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
        finally:
            source.close()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    try:
        check = sqlite3.connect(str(temporary), timeout=30)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()
            foreign_key_error = check.execute("PRAGMA foreign_key_check").fetchone()
        finally:
            check.close()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"integrity_check fehlgeschlagen: {integrity}")
        if foreign_key_error is not None:
            raise RuntimeError(f"foreign_key_check fehlgeschlagen: {foreign_key_error}")
        with temporary.open("r+b") as backup_file:
            os.fsync(backup_file.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, final)
        try:
            directory_fd = os.open(backup_dir, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        final_check = sqlite3.connect(str(final), timeout=30)
        try:
            final_integrity = final_check.execute("PRAGMA integrity_check").fetchone()
            final_foreign_key_error = final_check.execute(
                "PRAGMA foreign_key_check"
            ).fetchone()
        finally:
            final_check.close()
        if not final_integrity or final_integrity[0] != "ok":
            raise RuntimeError(f"finaler integrity_check fehlgeschlagen: {final_integrity}")
        if final_foreign_key_error is not None:
            raise RuntimeError(
                f"finaler foreign_key_check fehlgeschlagen: {final_foreign_key_error}"
            )
    except Exception:
        temporary.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "dest": str(final),
        "size_bytes": final.stat().st_size,
        "verified": True,
    }


def refresh_app_review_demo(
    *,
    db_path: Path,
    recipe_root: Path,
    config_path: Path,
    backup_dir: Path,
    public_url: str,
    hostname: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Validate, back up and atomically refresh an existing review dataset."""
    _assert_exact_review_environment(hostname, public_url)
    _assert_sanitized_config(config_path, public_url)
    preflight = _connect_existing(db_path)
    try:
        active, _review_user = _assert_review_database(preflight)
        _assert_active_recipe_provenance(active, recipe_root)
    finally:
        preflight.close()

    backup = _create_verified_backup(db_path, backup_dir)

    # Re-read all non-database guards after the backup.  The database-specific
    # guards are repeated below while the writer lock is held, closing the
    # time-of-check/time-of-use gap before the first mutation.
    _assert_sanitized_config(config_path, public_url)
    connection = _connect_existing(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        active, review_user = _assert_review_database(connection)
        base_by_slug = _assert_active_recipe_provenance(active, recipe_root)
        effective_today = today or date.today()
        monday = effective_today - timedelta(days=effective_today.weekday())
        result = _apply_transactional_refresh(
            connection,
            base_by_slug=base_by_slug,
            monday=monday,
            effective_today=effective_today,
        )
        final_user = connection.execute(
            "SELECT password_hash, role, disabled, session_version FROM users WHERE id=?",
            (int(review_user["id"]),),
        ).fetchone()
        if tuple(final_user) != tuple(
            review_user[key]
            for key in ("password_hash", "role", "disabled", "session_version")
        ):
            raise RuntimeError("Review-Admin-Konto wurde unerwartet verändert.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "ok": True,
        **result,
        "backup": backup,
        "review_username": REVIEW_USERNAME,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("/opt/scrapper/data/scrapper.db"))
    parser.add_argument(
        "--recipe-root", type=Path, default=Path("/opt/scrapper/files/rezepte")
    )
    parser.add_argument(
        "--config", type=Path, default=Path("/opt/scrapper/data/config.yaml")
    )
    parser.add_argument(
        "--backup-dir", type=Path, default=Path("/opt/scrapper/data/backups/review-refresh")
    )
    parser.add_argument("--public-url", default=REVIEW_PUBLIC_URL)
    args = parser.parse_args()
    result = refresh_app_review_demo(
        db_path=args.db,
        recipe_root=args.recipe_root,
        config_path=args.config,
        backup_dir=args.backup_dir,
        public_url=args.public_url,
        hostname=socket.gethostname(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
