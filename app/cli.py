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

from .auth import cleanup_initial_password_file, hash_password
from .config_store import get_config
from .db import get_db


def _cmd_set_password() -> int:
    pw = getpass.getpass("Neues Passwort: ")
    if len(pw) < 12:
        print("Passwort muss mindestens 12 Zeichen haben.", file=sys.stderr)
        return 2
    pw2 = getpass.getpass("Wiederholen:    ")
    if pw != pw2:
        print("Passwörter stimmen nicht überein.", file=sys.stderr)
        return 2
    cfg = get_config()
    cfg.set("web", "password", hash_password(pw))
    cfg.save()
    cleanup_initial_password_file()
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
    data_dir = get_db().path.parent

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

    db_path = get_db().path
    # Replacing an SQLite file while the web process has WAL connections open is
    # unsafe. Refuse the operation when the service is active.
    import subprocess as _sp
    try:
        active = _sp.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", "scrapper-web.service"],
            stdin=_sp.DEVNULL, timeout=5, check=False,
        ).returncode == 0
    except (FileNotFoundError, _sp.TimeoutExpired):
        active = False
    if active:
        print("✗ scrapper-web.service läuft noch. Vor Restore zuerst stoppen.", file=sys.stderr)
        return 2
    if not db_path.exists():
        # Erstaufnahme - kein Pre-Backup nötig
        print(f"ℹ Ziel-DB existiert nicht ({db_path}), wird neu angelegt")
    else:
        # Konsistentes Safety-Backup vom aktuellen Stand.
        safety = db_path.parent / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}.db.gz"
        print(f"💾 Sichere aktuelle DB nach: {safety}")
        safety_result = get_db().backup_to(safety, compress=True, verify=True)
        if not safety_result.get("ok"):
            print(f"✗ Safety-Backup fehlgeschlagen: {safety_result.get('error')}", file=sys.stderr)
            return 1

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

        # Atomic move ins Ziel; stale WAL/SHM-Dateien aus dem alten DB-Inode
        # dürfen nach einem Restore keinesfalls weiterverwendet werden.
        os.chmod(tmp_target, 0o600)
        tmp_target.replace(db_path)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        dir_fd = os.open(str(db_path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
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
    data_dir = get_db().path.parent
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


def _cmd_log_cleanup(args: list) -> int:
    """Löscht Log-Files älter als N Tage. Default: 30.
    Liest log_retention_days aus config wenn ohne Args."""
    cfg = get_config()
    if args and args[0]:
        try:
            days = int(args[0])
        except ValueError:
            print(f"✗ ungültige Tage-Zahl: {args[0]}", file=sys.stderr)
            return 1
    else:
        days = int(cfg.get("paths", "log_retention_days", default=30) or 30)

    logs_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs"))
    if not logs_dir.exists():
        print(f"ℹ Logs-Verzeichnis existiert nicht: {logs_dir}")
        return 0

    print(f"🧹 Lösche Log-Files älter als {days} Tage in {logs_dir}")
    cutoff = time.time() - days * 86400
    removed = 0
    bytes_freed = 0
    errors = 0

    for p in logs_dir.rglob("*"):
        if not p.is_file():
            continue
        # Nur Files mit Log-Endungen (Vorsicht falls jemand andere Files reingelegt hat)
        if p.suffix not in (".log", ".gz", ".txt", ".out"):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                size = p.stat().st_size
                p.unlink()
                removed += 1
                bytes_freed += size
        except Exception as e:
            errors += 1
            print(f"  ! kann nicht löschen: {p}: {e}", file=sys.stderr)

    mb_freed = bytes_freed / 1024 / 1024
    print(f"✓ {removed} Files gelöscht, {mb_freed:.1f} MB frei ({errors} Fehler)")
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
            "  python -m app.cli list-backups\n"
            "  python -m app.cli log-cleanup [DAYS]  # Default aus config (30)",
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
    if cmd == "log-cleanup":
        return _cmd_log_cleanup(args[1:])
    print(f"Unbekanntes Kommando: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
