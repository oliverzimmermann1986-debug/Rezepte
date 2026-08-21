"""Kommandozeile für den getrennten Video-Archiver."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .worker import ArchiveQueue, VideoArchiver


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
    run.add_argument(
        "--confirm-rights",
        action="store_true",
        help="Bestätigt, dass nur eigene oder zum Archivieren freigegebene Inhalte verarbeitet werden.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    queue = ArchiveQueue(args.queue)
    try:
        if args.command == "enqueue":
            result = queue.enqueue(args.recipe_id, args.url)
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
            )
            result = worker.process_one() or {"status": "idle"}
        else:  # pragma: no cover - argparse verhindert diesen Zustand
            raise ValueError(f"Unbekannter Befehl: {args.command}")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
