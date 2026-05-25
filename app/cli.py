"""CLI für Admin-Tasks.

Aufrufe:
    python -m app.cli set-password
    python -m app.cli rotate-secret
    python -m app.cli db-backup [PATH]
    python -m app.cli db-restore PATH
    python -m app.cli db-vacuum
    python -m app.cli list-backups
"""
from __future__ import annotations

import getpass
import os
import secrets as _secrets
import sys
import sqlite3
import time
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
    """SQLite-Online-Backup mit gzip + integrity-check + multi-tier retention.

    Multi-Tier:
      data/backups/daily/scrapper-YYYY-MM-DD.db.gz    (7 Tage)
      data/backups/weekly/scrapper-YYYY-WW.db.gz      (4 Wochen)
      data/backups/monthly/scrapper-YYYY-MM.db.gz     (12 Monate)

    Wenn ein Pfad gegeben ist, wird ohne Multi-Tier dorthin gespeichert.
    """
    cfg = get_config()
    data_dir = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data"))

    # Explizites Ziel: einmaliges Backup, kein Tier
    if args and args[0]:
        dest = Path(args[0])
        result = get_db().backup_to(dest, compress=dest.suffix == ".gz", verify=True)
        if result.get("ok"):
            print(f"✓ Backup nach {result['dest']} ({result['size_bytes']/1024/1024:.2f} MB)")
            return 0
        print(f"✗ Backup fehlgeschlagen: {result.get('error')}", file=sys.stderr)
        return 1

    # Multi-Tier-Backup
    now = datetime.now()
    backups_root = data_dir / "backups"
    daily = backups_root / "daily" / f"scrapper-{now:%Y-%m-%d}.db.gz"
    weekly = backups_root / "weekly" / f"scrapper-{now:%G-W%V}.db.gz"
    monthly = backups_root / "monthly" / f"scrapper-{now:%Y-%m}.db.gz"

    # Daily immer
    r = get_db().backup_to(daily, compress=True, verify=True)
    if not r.get("ok"):
        print(f"✗ Daily-Backup fehlgeschlagen: {r.get('error')}", file=sys.stderr)
        return 1
    print(f"✓ Daily:   {r['dest']} ({r['size_bytes']/1024/1024:.2f} MB, "
          f"{'verified' if r.get('verified') else 'NOT VERIFIED'})")

    # Weekly nur wenn noch nicht da (Idempotent über die Woche)
    if not weekly.exists():
        r = get_db().backup_to(weekly, compress=True, verify=True)
        if r.get("ok"):
            print(f"✓ Weekly:  {r['dest']} ({r['size_bytes']/1024/1024:.2f} MB)")

    # Monthly nur wenn noch nicht da
    if not monthly.exists():
        r = get_db().backup_to(monthly, compress=True, verify=True)
        if r.get("ok"):
            print(f"✓ Monthly: {r['dest']} ({r['size_bytes']/1024/1024:.2f} MB)")

    # Retention: alte Tiers löschen
    _prune_tier(backups_root / "daily",   keep_days=7)
    _prune_tier(backups_root / "weekly",  keep_days=4 * 7)
    _prune_tier(backups_root / "monthly", keep_days=12 * 31)
    return 0


def _prune_tier(tier_dir: Path, keep_days: int) -> None:
    """Löscht Backups in einem Tier-Verzeichnis die älter als keep_days sind."""
    if not tier_dir.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for f in tier_dir.glob("scrapper-*.db*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                print(f"  ✗ removed (>{keep_days}d): {f.name}")
        except Exception as e:
            print(f"  ! prune fail for {f.name}: {e}", file=sys.stderr)


def _cmd_db_restore(args: list) -> int:
    """Backup zurückspielen.
    Macht: 1) aktuellen Stand wegsichern als pre-restore.db
           2) Backup-Datei entpacken (falls .gz)
           3) Integrity-Check der Backup-Datei
           4) Ziel überschreiben

    WICHTIG: Service muss vorher gestoppt werden!
       systemctl stop scrapper-web
       python -m app.cli db-restore <backup-file>
       systemctl start scrapper-web
    """
    if not args or not args[0]:
        print("Usage: python -m app.cli db-restore <backup-file>", file=sys.stderr)
        return 1

    src = Path(args[0])
    if not src.exists():
        print(f"✗ Backup-Datei nicht gefunden: {src}", file=sys.stderr)
        return 1

    cfg = get_config()
    db_path = Path(cfg.get("paths", "db_path",
                            default="/opt/scrapper/data/scrapper.db"))
    if not db_path.exists():
        # Erstaufnahme - kein Pre-Backup nötig
        print(f"ℹ Ziel-DB existiert nicht ({db_path}), wird neu angelegt")
    else:
        # Safety-Backup vom aktuellen Stand
        safety = db_path.parent / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
        print(f"💾 Sichere aktuelle DB nach: {safety}")
        import shutil as _sh
        _sh.copy2(db_path, safety)

    # Entpacken wenn gzipped
    import gzip as _gz
    import shutil as _sh

    is_gz = src.name.endswith(".gz")
    tmp_target = db_path.parent / f".restore-tmp-{os.getpid()}.db"

    try:
        if is_gz:
            print(f"📦 Entpacke {src} → {tmp_target}")
            with _gz.open(src, "rb") as fin, open(tmp_target, "wb") as fout:
                _sh.copyfileobj(fin, fout)
        else:
            _sh.copy2(src, tmp_target)

        # Integrity-Check der entpackten Datei
        print("🔍 Integrity-Check…")
        check = sqlite3.connect(str(tmp_target), timeout=10)
        try:
            row = check.execute("PRAGMA integrity_check").fetchone()
            if row and row[0] == "ok":
                print("  ✓ integrity_check: ok")
            else:
                print(f"  ✗ integrity_check fehlgeschlagen: {row}", file=sys.stderr)
                tmp_target.unlink()
                return 1
        finally:
            check.close()

        # Atomic move ins Ziel
        tmp_target.replace(db_path)
        print(f"✓ Restore abgeschlossen: {db_path}")
        print(f"  Start den Service: systemctl start scrapper-web")
        return 0
    except Exception as e:
        try:
            tmp_target.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"✗ Restore fehlgeschlagen: {e}", file=sys.stderr)
        return 1


def _cmd_db_vacuum(args: list) -> int:
    """VACUUM auf die DB - reclaimt Disk-Speicher nach vielen Deletes."""
    print("🧹 VACUUM läuft…")
    result = get_db().vacuum()
    if not result.get("ok"):
        print(f"✗ VACUUM fehlgeschlagen: {result.get('error')}", file=sys.stderr)
        return 1
    before_mb = result["size_before"] / 1024 / 1024
    after_mb = result["size_after"] / 1024 / 1024
    reclaimed_mb = result["reclaimed_bytes"] / 1024 / 1024
    print(f"✓ VACUUM erfolgreich")
    print(f"  Vorher:    {before_mb:.2f} MB")
    print(f"  Nachher:   {after_mb:.2f} MB")
    print(f"  Reclaimed: {reclaimed_mb:.2f} MB")
    return 0


def _cmd_list_backups(args: list) -> int:
    """Listet alle vorhandenen Backups gegliedert nach Tier."""
    cfg = get_config()
    data_dir = Path(cfg.get("paths", "data_dir", default="/opt/scrapper/data"))
    backups_root = data_dir / "backups"
    if not backups_root.exists():
        print(f"Keine Backups vorhanden ({backups_root})")
        return 0
    for tier in ("daily", "weekly", "monthly"):
        tier_dir = backups_root / tier
        if not tier_dir.exists():
            continue
        files = sorted(tier_dir.glob("scrapper-*.db*"), key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"\n{tier.upper()} ({len(files)} Backups):")
        for f in files:
            mb = f.stat().st_size / 1024 / 1024
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {mtime}  {mb:6.2f} MB  {f.name}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "Usage:\n"
            "  python -m app.cli set-password\n"
            "  python -m app.cli rotate-secret\n"
            "  python -m app.cli db-backup [PATH]    # multi-tier wenn ohne PATH\n"
            "  python -m app.cli db-restore PATH     # Service vorher stoppen!\n"
            "  python -m app.cli db-vacuum           # Reclaim Disk-Speicher\n"
            "  python -m app.cli list-backups",
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
    if cmd == "db-restore":
        return _cmd_db_restore(args[1:])
    if cmd == "db-vacuum":
        return _cmd_db_vacuum(args[1:])
    if cmd == "list-backups":
        return _cmd_list_backups(args[1:])
    print(f"Unbekanntes Kommando: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
