"""Read-only release gates and verified rollback snapshots for local updates.

This helper never constructs Database or imports the application. Capture and
restore run only while the updater has stopped every application writer.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import uuid

import yaml

UNITS = (
    "scrapper-web.service", "scrapper-job.service", "scrapper-job.timer",
    "scrapper-db-backup.service", "scrapper-db-backup.timer", "scrapper-schedule-apply.service",
)
UNIT_DROPINS = ("scrapper-web.service.d/timer-write.conf",)


def assignment(path: Path, name: str):
    for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"Release-Metadaten fehlen: {path.name}/{name}")


def db_path(app: Path) -> Path:
    config = yaml.safe_load((app / "data/config.yaml").read_text(encoding="utf-8")) or {}
    value = str(config.get("paths", {}).get("db_path") or app / "data/scrapper.db").strip()
    path = Path(value)
    return (path if path.is_absolute() else app / path).resolve()


def read_database(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def preflight(source: Path, app: Path) -> dict:
    expected = int(assignment(source / "app/db.py", "CURRENT_SCHEMA_VERSION"))
    capabilities = set(assignment(source / "app/main.py", "APP_CAPABILITIES"))
    installed = set(assignment(app / "app/main.py", "APP_CAPABILITIES"))
    missing = sorted(installed - capabilities)
    with closing(read_database(db_path(app))) as connection:
        schema = int(connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0])
    if schema > expected:
        raise ValueError(f"Datenbank-Downgrade abgelehnt: Schema {schema} ist neuer als Release {expected}")
    if missing:
        raise ValueError("Release entfernt vorhandene Fähigkeiten: " + ", ".join(missing))
    return {"installed_schema": schema, "release_schema": expected,
            "release_version": assignment(source / "app/__init__.py", "__version__"),
            "preserved_capabilities": len(installed)}


def verify_code(source: Path, app: Path) -> dict:
    """Reject same-size stale files before starting any new application writer."""
    version = assignment(source / "app/__init__.py", "__version__")
    if assignment(app / "app/__init__.py", "__version__") != version:
        raise ValueError("Installierte Version stimmt nicht mit dem Release überein")
    manifest_path = source / "release-manifest.json"
    if not manifest_path.is_file():
        return {"version": version, "verified_files": 0, "manifest_supplied": False}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != version or manifest.get("schema") != assignment(source / "app/db.py", "CURRENT_SCHEMA_VERSION"):
        raise ValueError("Release-Manifest enthält widersprüchliche Metadaten")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Ungültiger Dateipfad im Release-Manifest")
        for root in (source, app):
            target = (root / relative).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Dateipfad verlässt das Release-Verzeichnis")
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Release-Dateiprüfung fehlgeschlagen: {name}")
    return {"version": version, "verified_files": len(manifest["files"]), "manifest_supplied": True}


def _metadata(path: Path) -> dict:
    info = path.stat()
    return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def _permissions(path: Path, metadata: dict) -> None:
    path.chmod(metadata["mode"])
    if hasattr(os, "chown"):
        os.chown(path, metadata["uid"], metadata["gid"])


def _verified_copy(source: Path, target: Path) -> None:
    with closing(read_database(source)) as original, closing(sqlite3.connect(target)) as backup:
        original.backup(backup)
        # A single-file snapshot must not depend on WAL sidecars.
        backup.execute("PRAGMA journal_mode=DELETE")
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite-Sicherung ist beschädigt")
        backup.commit()
    target.chmod(0o600)


def capture(app: Path, state: Path, *, unit_root: Path = Path("/etc/systemd/system")) -> dict:
    dropins = [unit_root / relative for relative in UNIT_DROPINS]
    for dropin in dropins:
        if dropin.parent.is_symlink() or dropin.parent.resolve() != dropin.parent:
            raise ValueError("Unit-Drop-in-Verzeichnis benötigt manuelle Prüfung")
        if dropin.exists() or dropin.is_symlink():
            info = dropin.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unit-Drop-in-Dateityp benötigt manuelle Prüfung")
    path = db_path(app)
    with closing(read_database(path)) as connection:
        has_tasks = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='background_tasks'").fetchone()
        if has_tasks and connection.execute("SELECT 1 FROM background_tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Vor dem Update müssen laufende oder wartende Hintergrundaufgaben abgeschlossen sein")
    state.mkdir(mode=0o700, parents=True, exist_ok=False)
    _verified_copy(path, state / "database.db")
    files = []
    for index, original in enumerate([app / "data/config.yaml", *(unit_root / unit for unit in UNITS), *dropins]):
        entry = {"path": str(original.resolve()), "existed": original.exists()}
        if original in dropins:
            entry["unit_dropin"] = True
            if original.parent.is_dir():
                entry["parent_metadata"] = _metadata(original.parent)
        if entry["existed"]:
            entry.update(_metadata(original))
            entry["backup"] = f"file-{index}"
            shutil.copyfile(original, state / entry["backup"])
            (state / entry["backup"]).chmod(0o600)
        files.append(entry)
    manifest = {"database": {"path": str(path), **_metadata(path)}, "files": files}
    (state / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (state / "manifest.json").chmod(0o600)
    return {"snapshot": str(state), "database_verified": True}


def enable_accounts(app: Path) -> dict:
    """Use existing administrator credentials, never reset or create any."""
    path = app / "data/config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    web = config.setdefault("web", {})
    with closing(read_database(db_path(app))) as connection:
        operator = connection.execute("SELECT role,disabled FROM users WHERE username=? COLLATE NOCASE",
                                      (str(web.get("username") or ""),)).fetchone()
    if not operator or operator[0] != "admin" or operator[1]:
        raise ValueError("Kontenanmeldung benötigt zuerst einen aktiven bestehenden Betreiberzugang")
    if len(str(web.get("secret_key") or "")) < 32:
        raise ValueError("Kontenanmeldung benötigt den eingerichteten Sitzungsschlüssel")
    metadata = _metadata(path)
    web.pop("auth_disabled", None)
    web.pop("external_logout_url", None)
    temporary = path.with_name(f".release-auth-{uuid.uuid4().hex}.yaml")
    try:
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        _permissions(temporary, metadata)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"accounts_enabled": True, "existing_operator_preserved": True}


def restore(state: Path) -> dict:
    manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item.get("unit_dropin"):
            parent = Path(item["path"]).parent
            if parent.is_symlink() or parent.resolve() != parent:
                raise ValueError("Unit-Drop-in-Verzeichnis benötigt manuelle Prüfung")
    db = manifest["database"]
    path = Path(db["path"])
    temporary = path.with_name(f".release-restore-{uuid.uuid4().hex}.db")
    try:
        _verified_copy(state / "database.db", temporary)
        _permissions(temporary, db)
        os.replace(temporary, path)
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    for item in manifest["files"]:
        target = Path(item["path"])
        if not item["existed"]:
            target.unlink(missing_ok=True)
            continue
        if item.get("unit_dropin") and not target.parent.exists():
            target.parent.mkdir(mode=0o755, parents=True)
            if "parent_metadata" in item:
                _permissions(target.parent, item["parent_metadata"])
        staged = target.with_name(f".release-restore-{uuid.uuid4().hex}")
        try:
            shutil.copyfile(state / item["backup"], staged)
            _permissions(staged, item)
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
    return {"restored": True, "database_verified": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "verify-code", "capture", "restore", "enable-accounts"))
    parser.add_argument("--app", type=Path, default=Path("/opt/scrapper"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    try:
        if args.action in ("preflight", "verify-code"):
            if args.source is None:
                parser.error(f"{args.action} benötigt --source")
            check = preflight if args.action == "preflight" else verify_code
            result = check(args.source.resolve(), args.app.resolve())
        elif args.action == "enable-accounts":
            result = enable_accounts(args.app.resolve())
        elif args.action == "capture":
            if args.state is None:
                parser.error("capture benötigt --state")
            result = capture(args.app.resolve(), args.state.resolve())
        else:
            if args.state is None:
                parser.error("restore benötigt --state")
            result = restore(args.state.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        parser.exit(1, f"Release-Prüfung fehlgeschlagen: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
