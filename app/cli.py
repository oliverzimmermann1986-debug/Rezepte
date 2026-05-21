"""CLI für Admin-Tasks.

Aufrufe:
    python -m app.cli set-password
    python -m app.cli rotate-secret
"""
from __future__ import annotations

import getpass
import secrets as _secrets
import sys

from .auth import hash_password
from .config_store import get_config


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


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "Usage:\n"
            "  python -m app.cli set-password\n"
            "  python -m app.cli rotate-secret",
            file=sys.stderr,
        )
        return 1
    cmd = args[0]
    if cmd == "set-password":
        return _cmd_set_password()
    if cmd == "rotate-secret":
        return _cmd_rotate_secret()
    print(f"Unbekanntes Kommando: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
