"""Kommandozeile für den getrennten Video-Archiver."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from .worker import ArchiveQueue, VideoArchiver, load_recipe_links


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m video_archiver",
        description="Privater Video-Archiver mit separater SQLite-Queue.",
    )
    parser.add_argument("--queue", type=Path, default=Path("video-archive/queue.db"))
    commands = parser.add_subparsers(dest="command", required=True)

    enqueue = commands.add_parser("enqueue", help="Rezept-ID und Plattform-Link einplanen")
    enqueue.add_argument("--id", type=int, required=True, dest="recipe_id")
    enqueue.add_argument("--url", required=True)

    sync = commands.add_parser(
        "sync", help="Neue Plattform-Links read-only aus der Rezeptdatenbank übernehmen"
    )
    sync.add_argument("--recipes-db", type=Path, required=True)
    sync.add_argument(
        "--min-interval",
        type=int,
        default=0,
        help="Vollständigen DB-Abgleich höchstens alle N Sekunden ausführen",
    )
    sync.add_argument(
        "--queue-user",
        help="Nach dem Lesen der Rezeptdatenbank vor dem Queue-Zugriff zu diesem Benutzer wechseln",
    )

    commands.add_parser("status", help="Queue-Zähler ausgeben")
    events = commands.add_parser("events", help="Letzte Worker-Ereignisse ausgeben")
    events.add_argument("--limit", type=int, default=50)

    run = commands.add_parser("run", help="Genau einen verfügbaren Auftrag verarbeiten")
    run.add_argument("--archive", type=Path, default=Path("video-archive/files"))
    run.add_argument("--yt-dlp", default="yt-dlp", dest="ytdlp_path")
    run.add_argument("--cookies", type=Path)
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--max-size-mb", type=int, default=1000)
    run.add_argument("--min-free-mb", type=int, default=512)
    run.add_argument(
        "--max-jobs", type=int, default=1,
        help="Pro Aufruf höchstens N verfügbare Queue-Einträge verarbeiten",
    )
    run.add_argument(
        "--confirm-rights",
        action="store_true",
        help="Bestätigt, dass nur eigene oder zum Archivieren freigegebene Inhalte verarbeitet werden.",
    )
    return parser


def _drop_privileges(username: str) -> None:
    if os.name != "posix":
        raise ValueError("--queue-user wird nur auf POSIX-Systemen unterstützt")
    if os.geteuid() != 0:
        raise ValueError("--queue-user benötigt Root-Rechte")
    import pwd

    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise ValueError(f"Unbekannter Queue-Benutzer: {username}") from exc
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def _recent_sync_exists(queue_path: Path, min_interval: int) -> bool:
    """Prüft die Sync-Sperre read-only, bevor Root die große Quell-DB liest."""
    if min_interval <= 0:
        return False
    path = queue_path.expanduser().resolve()
    if not path.is_file():
        return False
    try:
        uri = f"{path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            row = connection.execute(
                "SELECT value FROM archive_state WHERE key='last_recipe_sync'"
            ).fetchone()
        last_sync = float(row[0]) if row else 0.0
    except (sqlite3.Error, TypeError, ValueError):
        return False
    return time.time() - last_sync < min_interval


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        recipe_links = None
        skip_recipe_sync = False
        if args.command == "sync" and args.queue_user:
            # Root darf die WAL-Quelldatenbank konsistent read-only öffnen.
            # Vor dem ersten Zugriff auf die private Queue werden sämtliche
            # Privilegien dauerhaft abgegeben, damit deren Dateien weiterhin
            # dem isolierten Archiver-Benutzer gehören.
            skip_recipe_sync = _recent_sync_exists(
                args.queue, max(0, args.min_interval),
            )
            if not skip_recipe_sync:
                recipe_links = load_recipe_links(args.recipes_db)
            _drop_privileges(args.queue_user)

        queue = ArchiveQueue(args.queue)
        if args.command == "enqueue":
            result = queue.enqueue(args.recipe_id, args.url)
        elif args.command == "sync":
            if skip_recipe_sync:
                result = {"seen": 0, "eligible": 0, "enqueued": 0,
                          "unchanged": 0, "ignored": 0, "skipped": 1}
            elif recipe_links is not None:
                result = queue.sync_recipe_links(recipe_links)
                queue.mark_recipe_sync()
            else:
                result = queue.sync_from_recipes_db(
                    args.recipes_db,
                    min_interval_seconds=max(0, args.min_interval),
                )
        elif args.command == "status":
            result = queue.counts()
        elif args.command == "events":
            result = queue.events(args.limit)
        elif args.command == "run":
            if not args.confirm_rights:
                raise ValueError("run benötigt --confirm-rights")
            worker = VideoArchiver(
                queue=queue,
                archive_dir=args.archive,
                ytdlp_path=args.ytdlp_path,
                cookies_file=args.cookies,
                timeout_seconds=args.timeout,
                max_attempts=args.max_attempts,
                max_bytes=args.max_size_mb * 1024 * 1024,
                free_space_reserve_bytes=args.min_free_mb * 1024 * 1024,
            )
            max_jobs = max(1, min(50, int(args.max_jobs)))
            processed = []
            for _ in range(max_jobs):
                item = worker.process_one()
                if item is None:
                    break
                processed.append(item)
            if max_jobs == 1:
                result = processed[0] if processed else {"status": "idle"}
            else:
                failed = sum(
                    1 for item in processed
                    if item.get("status") in {"queued", "failed"}
                )
                result = {
                    "status": "partial" if failed else ("completed" if processed else "idle"),
                    "processed": len(processed),
                    "failed": failed,
                    "items": processed,
                }
        else:  # pragma: no cover - argparse verhindert diesen Zustand
            raise ValueError(f"Unbekannter Befehl: {args.command}")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "run" and result.get("status") in {"queued", "failed", "partial"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
