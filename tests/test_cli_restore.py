"""DB-Restore verarbeitet WAL konsistent und entfernt alte Sidecars."""

import sqlite3

from app import cli


class _Config:
    def __init__(self, db_path):
        self.db_path = db_path

    def get(self, section, key, default=None):
        if (section, key) == ("paths", "db_path"):
            return str(self.db_path)
        return default


def _value(path):
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM marker").fetchone()[0]
    finally:
        connection.close()


def test_restore_replaces_database_and_removes_stale_wal(tmp_path, monkeypatch):
    target = tmp_path / "scrapper.db"
    current = sqlite3.connect(target)
    current.execute("CREATE TABLE marker(value TEXT)")
    current.execute("INSERT INTO marker VALUES ('aktuell')")
    current.commit()
    current.close()

    source = tmp_path / "backup.db"
    backup = sqlite3.connect(source)
    backup.execute("CREATE TABLE marker(value TEXT)")
    backup.execute("INSERT INTO marker VALUES ('backup')")
    backup.commit()
    backup.close()

    original_copy = cli._sqlite_online_copy

    def copy_then_create_stale_sidecars(source, destination):
        original_copy(source, destination)
        if source == target and destination.name.startswith("pre-restore-"):
            target.with_name(target.name + "-wal").write_bytes(b"alte wal frames")
            target.with_name(target.name + "-shm").write_bytes(b"alter shared memory stand")

    monkeypatch.setattr(cli, "_sqlite_online_copy", copy_then_create_stale_sidecars)
    monkeypatch.setattr(cli, "get_config", lambda: _Config(target))

    assert cli._cmd_db_restore([str(source)]) == 0
    assert _value(target) == "backup"
    assert not target.with_name(target.name + "-wal").exists()
    assert not target.with_name(target.name + "-shm").exists()
    safety = list(tmp_path.glob("pre-restore-*.db"))
    assert len(safety) == 1
    assert _value(safety[0]) == "aktuell"


def test_restore_rejects_corrupt_backup_without_touching_target(tmp_path, monkeypatch):
    target = tmp_path / "scrapper.db"
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE marker(value TEXT)")
    connection.execute("INSERT INTO marker VALUES ('bleibt')")
    connection.commit()
    connection.close()
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"keine sqlite datei")
    monkeypatch.setattr(cli, "get_config", lambda: _Config(target))

    assert cli._cmd_db_restore([str(corrupt)]) == 1
    assert _value(target) == "bleibt"
