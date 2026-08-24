"""Fehlende Social-Links aus dem Rezept-Postfach sicher wiederherstellen.

Standardmäßig wird ausschließlich gelesen und ein JSON-Bericht geschrieben.
Mit ``--apply`` werden nur konfliktfreie, durch starke Evidenz belegte Treffer
nach SQLite-Backup und Rezept-Snapshot übernommen.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from app.config_store import get_config
from app.core.email_processor import MailAccount, normalize_content_url
from app.db import Database, get_db
from app.recipes.manage import safe_update_recipe_metadata


STRONG_METHODS = {"path-exact", "description-exact", "media-sha256"}


def _text_key(value: Optional[str]) -> str:
    ascii_text = unicodedata.normalize("NFKD", value or "")
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _path_key(value: Optional[str]) -> str:
    parts = [
        _text_key(part).replace(" ", "")
        for part in PurePosixPath((value or "").replace("\\", "/")).parts
        if part != "/"
    ]
    try:
        recipe_pos = parts.index("rezepte")
    except ValueError:
        tail = parts[-3:]
    else:
        tail = parts[recipe_pos + 1:]
    return "/".join(tail)


def _normalize_url(value: Optional[str]) -> str:
    return normalize_content_url(value or "") or (value or "").strip()


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _file_sha256(path: Optional[str]) -> Optional[str]:
    candidate = Path(path or "")
    if not candidate.is_file() or candidate.is_symlink():
        return None
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _recipe_media_hashes(recipe: dict[str, Any]) -> set[str]:
    folder = Path(str(recipe.get("folder_path") or ""))
    if not folder.is_dir() or folder.is_symlink():
        return set()
    hashes: set[str] = set()
    for child in folder.iterdir():
        if child.suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
            continue
        digest = _file_sha256(str(child))
        if digest:
            hashes.add(digest)
    return hashes


def _unique_value(values: Iterable[tuple[int, str]]) -> Optional[tuple[int, str]]:
    unique = {(int(recipe_id), name) for recipe_id, name in values}
    return next(iter(unique)) if len(unique) == 1 else None


def match_source_to_recipes(
    source: dict[str, Any],
    recipes: list[dict[str, Any]],
    *,
    include_media: bool = True,
    media_hashes: Optional[dict[int, set[str]]] = None,
) -> dict[str, Any]:
    """Ermittelt einen Kandidaten und liefert die Evidenz nachvollziehbar mit."""
    evidence: list[dict[str, Any]] = []

    history_path = _path_key(source.get("history_target"))
    if history_path:
        match = _unique_value(
            (recipe["id"], recipe.get("name") or "")
            for recipe in recipes
            if _path_key(recipe.get("folder_path")) == history_path
        )
        if match:
            evidence.append({"method": "path-exact", "id": match[0], "name": match[1]})

    source_names = {
        _text_key(source.get("history_name")),
        _text_key(source.get("pending_name")),
    } - {"", "skipped", "verworfen", "unbekannt", "tiktok rezept prufen"}
    for source_name in sorted(source_names):
        match = _unique_value(
            (recipe["id"], recipe.get("name") or "")
            for recipe in recipes
            if _text_key(recipe.get("name")) == source_name
        )
        if match:
            evidence.append({"method": "name-exact", "id": match[0], "name": match[1]})

    pending_description = _text_key(source.get("pending_description"))
    if len(pending_description) >= 50:
        ranked: list[tuple[float, float, float, dict[str, Any]]] = []
        source_tokens = set(pending_description.split())
        for recipe in recipes:
            recipe_description = _text_key(recipe.get("description"))
            if len(recipe_description) < 50:
                continue
            recipe_tokens = set(recipe_description.split())
            sequence = difflib.SequenceMatcher(
                None, pending_description, recipe_description,
            ).ratio()
            jaccard = len(source_tokens & recipe_tokens) / max(
                1, len(source_tokens | recipe_tokens),
            )
            ranked.append((min(sequence, jaccard), sequence, jaccard, recipe))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if (
            ranked
            and ranked[0][1] >= 0.97
            and ranked[0][2] >= 0.90
            and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08)
        ):
            best = ranked[0]
            evidence.append({
                "method": "description-exact",
                "id": best[3]["id"],
                "name": best[3].get("name") or "",
                "sequence": round(best[1], 4),
                "jaccard": round(best[2], 4),
            })

    if include_media:
        source_hash = _file_sha256(source.get("pending_video_path"))
        if source_hash:
            matches = []
            for recipe in recipes:
                recipe_id = int(recipe["id"])
                if media_hashes is not None:
                    if recipe_id not in media_hashes:
                        media_hashes[recipe_id] = _recipe_media_hashes(recipe)
                    known_hashes = media_hashes[recipe_id]
                else:
                    known_hashes = _recipe_media_hashes(recipe)
                if source_hash in known_hashes:
                    matches.append((recipe["id"], recipe.get("name") or ""))
            match = _unique_value(matches)
            if match:
                evidence.append({
                    "method": "media-sha256", "id": match[0], "name": match[1],
                })

    evidence_ids = {item["id"] for item in evidence}
    candidate_id = next(iter(evidence_ids)) if len(evidence_ids) == 1 else None
    methods = {item["method"] for item in evidence}
    safe = candidate_id is not None and bool(methods & STRONG_METHODS)
    return {
        "url": source["url"],
        "history_name": source.get("history_name"),
        "candidate_id": candidate_id,
        "safe": safe,
        "evidence": evidence,
        "conflict": len(evidence_ids) > 1,
    }


def _load_sources(db: Database, *, max_mails: Optional[int]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cfg = get_config().get("mail", "recipe", default={}) or {}
    account = MailAccount(
        "recipe", cfg, "recipe", cfg.get("default_category"),
    )
    mail_result = account.fetch_all_readonly(
        max_mails=max_mails,
        include_attachments=False,
    )

    with db.conn() as connection:
        active_urls = {
            _normalize_url(row[0])
            for row in connection.execute(
                "SELECT url FROM recipes WHERE deleted_at IS NULL AND url IS NOT NULL"
            )
        }
        histories = [dict(row) for row in connection.execute(
            "SELECT url, name, target_dir, processed_at FROM history"
        )]
        pending_rows = [dict(row) for row in connection.execute(
            "SELECT url, description, video_path, ai_suggestion, status FROM pending"
        )]

    histories_by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in histories:
        histories_by_url[_normalize_url(item.get("url"))].append(item)
    pending_by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in pending_rows:
        pending_by_url[_normalize_url(item.get("url"))].append(item)

    mail_by_url: dict[str, dict[str, Any]] = {}
    for item in mail_result.get("urls", []):
        normalized = _normalize_url(item.get("url"))
        if normalized:
            mail_by_url[normalized] = item

    sources: list[dict[str, Any]] = []
    for url, mail_item in sorted(mail_by_url.items()):
        if url in active_urls:
            continue
        history = (histories_by_url.get(url) or [{}])[-1]
        pending = (pending_by_url.get(url) or [{}])[-1]
        suggestion = _json_dict(pending.get("ai_suggestion"))
        sources.append({
            "url": url,
            "mail_uid": mail_item.get("mail_uid"),
            "subject": mail_item.get("subject"),
            "history_name": history.get("name"),
            "history_target": history.get("target_dir"),
            "pending_name": suggestion.get("name"),
            "pending_description": pending.get("description") or "",
            "pending_video_path": pending.get("video_path"),
            "pending_status": pending.get("status"),
        })
    return sources, {
        "url_occurrences": len(mail_result.get("urls", [])),
        "unique_mail_links": len(mail_by_url),
        "already_active": len(mail_by_url) - len(sources),
        "missing_from_portal": len(sources),
    }


def _backup_database(db: Database) -> Path:
    backup_dir = db.path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-mail-link-reconcile-{time.strftime('%Y%m%d-%H%M%S')}.db"
    source_connection = sqlite3.connect(str(db.path))
    destination_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    return target


def _apply_matches(
    db: Database,
    matches: list[dict[str, Any]],
) -> tuple[Optional[Path], list[dict[str, Any]]]:
    safe_matches = [item for item in matches if item.get("safe")]
    duplicate_recipes = {
        recipe_id for recipe_id, count in Counter(
            item["candidate_id"] for item in safe_matches
        ).items() if count > 1
    }
    selected = [
        item for item in safe_matches
        if item["candidate_id"] not in duplicate_recipes
    ]
    if not selected:
        return None, []
    backup = _backup_database(db)
    results: list[dict[str, Any]] = []
    for match in selected:
        recipe_id = int(match["candidate_id"])
        recipe = db.recipe_get(recipe_id)
        if not recipe or recipe.get("deleted_at") is not None or recipe.get("url"):
            results.append({"id": recipe_id, "status": "skipped-changed"})
            continue
        conflict = db.recipe_get_by_url(match["url"])
        if conflict:
            results.append({
                "id": recipe_id, "status": "skipped-conflict",
                "conflict_id": conflict["id"],
            })
            continue
        version_id = db.recipe_version_create(
            recipe_id,
            created_by="mail-link-reconcile",
            source="mail-reconcile",
            reason="Vor Postfach-Link-Merge",
        )
        if version_id is None:
            results.append({"id": recipe_id, "status": "error-snapshot"})
            continue
        try:
            safe_update_recipe_metadata(
                db,
                recipe_id,
                name=recipe.get("name") or "Unbenannt",
                recipe_type=recipe.get("type") or "Sonstiges",
                category=recipe.get("category") or "Allgemein",
                description=recipe.get("description") or "",
                servings=recipe.get("servings"),
                url=match["url"],
                target_folder_override=recipe.get("folder_path"),
            )
            if db.pending_get(match["url"]):
                db.pending_resolve(match["url"], "resolved")
            results.append({
                "id": recipe_id,
                "status": "merged",
                "version_id": version_id,
                "url": match["url"],
                "evidence": match["evidence"],
            })
        except Exception as exc:  # Einzelne Zuordnung darf den Lauf nicht abbrechen.
            results.append({"id": recipe_id, "status": "error", "error": str(exc)})
    return backup, results


def run(*, apply: bool, max_mails: Optional[int], report_path: Path) -> dict[str, Any]:
    db = get_db()
    sources, scan_stats = _load_sources(db, max_mails=max_mails)
    recipes = db.recipe_list(limit=10_000)
    missing_recipes = [
        recipe for recipe in recipes
        if recipe.get("deleted_at") is None and not recipe.get("url")
    ]
    media_hashes: dict[int, set[str]] = {}
    matches = [
        match_source_to_recipes(
            source,
            missing_recipes,
            media_hashes=media_hashes,
        )
        for source in sources
    ]
    report: dict[str, Any] = {
        "generated_at": time.time(),
        "mode": "apply" if apply else "dry-run",
        "scan": scan_stats,
        "missing_recipes": len(missing_recipes),
        "safe_matches": sum(1 for item in matches if item["safe"]),
        "ambiguous_matches": sum(1 for item in matches if not item["safe"]),
        "matches": matches,
    }
    if apply:
        backup, results = _apply_matches(db, matches)
        report["backup"] = str(backup) if backup else None
        report["results"] = results
        report["merged"] = sum(1 for item in results if item["status"] == "merged")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Sichere Treffer übernehmen")
    parser.add_argument("--max-mails", type=int, default=None)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/opt/scrapper/data/mail-link-reconcile-report.json"),
    )
    args = parser.parse_args()
    report = run(apply=args.apply, max_mails=args.max_mails, report_path=args.report)
    print(json.dumps({
        "mode": report["mode"],
        "scan": report["scan"],
        "missing_recipes": report["missing_recipes"],
        "safe_matches": report["safe_matches"],
        "ambiguous_matches": report["ambiguous_matches"],
        "merged": report.get("merged", 0),
        "backup": report.get("backup"),
        "report": str(args.report),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
