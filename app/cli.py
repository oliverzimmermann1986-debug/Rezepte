"""CLI für Admin-Tasks.

Aufrufe:
    python -m app.cli set-password
    python -m app.cli rotate-secret
    python -m app.cli db-backup [PATH]
"""
from __future__ import annotations

import getpass
import secrets as _secrets
import sys
from datetime import datetime
from pathlib import Path

from .auth import hash_password
from .config_store import get_config
from .db import get_db


def _cmd_set_password() -> int:
    pw = getpass.getpass("Neues Passwort: ")
    if len(pw) < 8:
        print("Passwort muss mindestens 8 Zeichen haben.", file=sys.stderr)
        return 2
    pw2 = getpass.getpass("Wiederholen:    ")
    if pw != pw2:
        print("Passwörter stimmen nicht überein.", file=sys.stderr)
        return 2
    cfg = get_config()
    cfg.set("web", "password", hash_password(pw))
    cfg.save()
    print("✓ Passwort gespeichert (bcrypt).")
    return 0


def _cmd_rotate_secret() -> int:
    cfg = get_config()
    cfg.set("web", "secret_key", _secrets.token_urlsafe(48))
    cfg.save()
    print("✓ secret_key rotiert. Alle bestehenden Sessions sind ungültig.")
    return 0


def _cmd_db_backup(args: list) -> int:
    """SQLite-Online-Backup. Default-Ziel: data/backups/scrapper-YYYY-MM-DD.db.
    Für tägliche Backups idealerweise via Cron / systemd-Timer aufrufen.
    """
    cfg = get_config()
    data_dir = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data"))
    if args and args[0]:
        dest = Path(args[0])
    else:
        dest = data_dir / "backups" / f"scrapper-{datetime.now():%Y-%m-%d}.db"
    print(f"Backup nach: {dest}")
    result = get_db().backup_to(dest)
    if result.get("ok"):
        size_mb = result["size_bytes"] / 1024 / 1024
        print(f"✓ Backup erfolgreich ({size_mb:.2f} MB)")
        return 0
    print(f"✗ Backup fehlgeschlagen: {result.get('error')}", file=sys.stderr)
    return 1


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "Usage:\n"
            "  python -m app.cli set-password\n"
            "  python -m app.cli rotate-secret\n"
            "  python -m app.cli db-backup [PATH]",
            file=sys.stderr,
        )
        return 1
    cmd = args[0]
    if cmd == "set-password":
        return _cmd_set_password()
    if cmd == "rotate-secret":
        return _cmd_rotate_secret()
    if cmd == "db-backup":
        return _cmd_db_backup(args[1:])
    print(f"Unbekanntes Kommando: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
