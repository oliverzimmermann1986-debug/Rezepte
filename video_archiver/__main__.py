"""Kommandozeile für den getrennten Video-Archiver."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        recipe_links = None
        if args.command == "sync" and args.queue_user:
            # Root darf die WAL-Quelldatenbank konsistent read-only öffnen.
            # Vor dem ersten Zugriff auf die private Queue werden sämtliche
            # Privilegien dauerhaft abgegeben, damit deren Dateien weiterhin
            # dem isolierten Archiver-Benutzer gehören.
            recipe_links = load_recipe_links(args.recipes_db)
            _drop_privileges(args.queue_user)

        queue = ArchiveQueue(args.queue)
        if args.command == "enqueue":
            result = queue.enqueue(args.recipe_id, args.url)
        elif args.command == "sync":
            result = (
                queue.sync_recipe_links(recipe_links)
                if recipe_links is not None
                else queue.sync_from_recipes_db(args.recipes_db)
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
            result = worker.process_one() or {"status": "idle"}
        else:  # pragma: no cover - argparse verhindert diesen Zustand
            raise ValueError(f"Unbekannter Befehl: {args.command}")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
