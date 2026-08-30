"""Refresh the existing, isolated Apple App Review dataset after an update.

Unlike :mod:`tools.setup_app_review_demo`, this command is deliberately a
fixer for an already populated review instance.  It refuses production-like
data, takes and verifies an online SQLite backup, and then applies the small
demo migration in one ``BEGIN IMMEDIATE`` transaction.  Credentials, users,
recipe variants and integration configuration are never rewritten.
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

from app.recipes.source_integrity import normalize_source_text, source_fingerprint
from tools.setup_app_review_demo import (
    RECIPES,
    REVIEW_HOSTNAME,
    REVIEW_PLAN_RECIPES,
    REVIEW_PLAN_WEEKS,
    REVIEW_PUBLIC_URL,
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
    required = {"recipes", "recipe_source_snapshots", "meal_plan_entries", "users"}
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


def _apply_transactional_refresh(
    connection: sqlite3.Connection,
    *,
    base_by_slug: Mapping[str, sqlite3.Row],
    monday: date,
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
    url_changed = str(base_by_slug[PLAN_RECIPES[0][0]]["url"] or "") != source_url
    snapshots_changed = not _snapshot_rows_are_current(snapshot_rows, source_url)
    meal_plan_changed = not _meal_plan_is_current(meal_rows, monday, base_by_slug)

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
    if str(refreshed_recipe["url"]) != source_url:
        raise RuntimeError("Review-Quell-URL wurde nicht atomar aktualisiert.")
    if not _snapshot_rows_are_current(final_snapshots, source_url):
        raise RuntimeError("Review-Quell-Snapshots entsprechen nicht dem Sollzustand.")
    if not _meal_plan_is_current(final_meals, monday, refreshed_by_slug):
        raise RuntimeError("Review-Wochenplan entspricht nicht dem Sollzustand.")

    return {
        "changed": bool(url_changed or snapshots_changed or meal_plan_changed),
        "url_changed": url_changed,
        "snapshots_changed": snapshots_changed,
        "meal_plan_changed": meal_plan_changed,
        "recipe_id": lemon_id,
        "source_url": source_url,
        "meal_plan_date": monday.isoformat(),
        "meal_plan_entries": len(final_meals),
        "source_snapshots": len(final_snapshots),
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
