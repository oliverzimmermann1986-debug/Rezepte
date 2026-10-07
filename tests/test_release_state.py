"""A failed migration must recover the original data and installed units."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

import pytest
import yaml

from tools.release_state import UNITS, capture, enable_accounts, preflight, restore, verify_code


def test_code_gate_rejects_same_size_stale_files(tmp_path):
    source, app = tmp_path / "source", tmp_path / "installed"
    _installation(source)
    _installation(app)
    text = source / "app/main.py"
    text.write_text("APP_CAPABILITIES = ['new']\n")
    (app / "app/main.py").write_text("APP_CAPABILITIES = ['old']\n")
    assert text.stat().st_size == (app / "app/main.py").stat().st_size
    import os
    os.utime(text, (0, 0))
    os.utime(app / "app/main.py", (0, 0))
    (source / "release-manifest.json").write_text(json.dumps({"version": "1.8.0", "schema": 260,
        "files": {"app/main.py": hashlib.sha256(text.read_bytes()).hexdigest()}}))
    with pytest.raises(ValueError, match="Dateiprüfung"):
        verify_code(source, app)
    shutil.copyfile(text, app / "app/main.py")
    assert verify_code(source, app)["verified_files"] == 1


def test_updater_uses_checksum_source_version_and_hash_based_bytecode():
    script = (Path(__file__).resolve().parents[1] / "proxmox/update-local.sh").read_text()
    assert script.count("rsync -a --checksum --delete") == 2
    assert "verify-code" in script
    assert "--invalidation-mode checked-hash" in script
    assert '"$SOURCE_DIR/app/__init__.py" | head' in script


def test_checked_hash_bytecode_does_not_reuse_same_size_old_version(tmp_path):
    import os
    import sys
    source = tmp_path / "version_probe.py"
    source.write_text('__version__ = "1.8.0"\n')
    os.utime(source, (0, 0))
    subprocess.run([sys.executable, '-m', 'compileall', '-q', '-f', '--invalidation-mode', 'checked-hash', str(source)], check=True)
    source.write_text('__version__ = "1.8.1"\n')
    os.utime(source, (0, 0))
    program = "import importlib.util;import sys;s=importlib.util.spec_from_file_location('version_probe',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.__version__)"
    result = subprocess.run([sys.executable, '-c', program, str(source)], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "1.8.1"


def test_updater_api_gate_matches_registered_methods():
    from app.main import app

    script = (Path(__file__).resolve().parents[1] / "proxmox/update-local.sh").read_text()
    contracts = []
    for source in re.findall(r"<<'PY'\n(.*?)\nPY", script, re.DOTALL):
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "required_methods"
                for target in node.targets
            ):
                contracts.append(ast.literal_eval(node.value))
    assert len(contracts) == 1, "The release must check its registered HTTP contracts"
    paths = app.openapi()["paths"]
    missing = [f"{method.upper()} {path}" for path, methods in contracts[0].items()
               for method in methods if method not in paths.get(path, {})]
    assert missing == []


def _installation(root: Path, *, schema=260, capabilities=("existing-feature",)):
    (root / "app").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "app/db.py").write_text(f"CURRENT_SCHEMA_VERSION = {schema}\n")
    (root / "app/main.py").write_text(f"APP_CAPABILITIES = {list(capabilities)!r}\n")
    (root / "app/__init__.py").write_text('__version__ = "1.8.0"\n')
    path = root / "data/recipes.db"
    (root / "data/config.yaml").write_text(yaml.safe_dump({"paths": {"db_path": str(path)}, "web": {"secret_key": "synthetic-test-only"}}))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
        connection.execute("INSERT INTO schema_migrations VALUES(?)", (schema,))
        connection.execute("CREATE TABLE recipes(id INTEGER PRIMARY KEY,name TEXT)")
        connection.execute("INSERT INTO recipes VALUES(44,'Original')")
    connection.close()
    return path


def test_preflight_rejects_schema_downgrade_without_writing(tmp_path):
    app, source = tmp_path / "installed", tmp_path / "candidate"
    path = _installation(app)
    _installation(source, schema=232)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Downgrade"):
        preflight(source, app)
    assert path.read_bytes() == before


def test_preflight_rejects_lost_capabilities_and_accepts_additive_upgrade(tmp_path):
    app, source = tmp_path / "installed", tmp_path / "candidate"
    _installation(app)
    _installation(source, schema=261, capabilities=("households",))
    with pytest.raises(ValueError, match="existing-feature"):
        preflight(source, app)
    (source / "app/main.py").write_text("APP_CAPABILITIES = ['existing-feature','households']\n")
    result = preflight(source, app)
    assert result["installed_schema"] == 260 and result["release_schema"] == 261


def test_restore_recovers_database_config_units_and_absent_files(tmp_path):
    app, units, state = tmp_path / "app", tmp_path / "units", tmp_path / "snapshot"
    path = _installation(app)
    units.mkdir()
    original_unit = units / UNITS[0]
    original_unit.write_text("Original unit")
    config = app / "data/config.yaml"
    original_config = config.read_bytes()
    capture(app, state, unit_root=units)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE recipes SET name='Changed'")
        connection.execute("INSERT INTO recipes VALUES(55,'New')")
        connection.execute("INSERT INTO schema_migrations VALUES(261)")
    connection.close()
    config.write_text("changed: true")
    original_unit.write_text("Changed unit")
    (units / UNITS[1]).write_text("New unit")
    assert restore(state)["database_verified"] is True
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM recipes").fetchall() == [(44, "Original")]
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 260
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()
    assert config.read_bytes() == original_config
    assert original_unit.read_text() == "Original unit"
    assert not (units / UNITS[1]).exists()
    with pytest.raises(FileExistsError):
        capture(app, state, unit_root=units)


def test_damaged_snapshot_cannot_replace_current_database(tmp_path):
    app, units, state = tmp_path / "app", tmp_path / "units", tmp_path / "snapshot"
    path = _installation(app)
    units.mkdir()
    capture(app, state, unit_root=units)
    before = path.read_bytes()
    (state / "database.db").write_bytes(b"damaged snapshot")
    with pytest.raises(sqlite3.DatabaseError):
        restore(state)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".release-restore-*"))


def test_account_activation_preserves_credentials_settings_and_rollback(tmp_path):
    app, units, state = tmp_path / "app", tmp_path / "units", tmp_path / "snapshot"
    database = _installation(app)
    units.mkdir()
    config = app / "data/config.yaml"
    settings = yaml.safe_load(config.read_text())
    settings["web"].update(username="owner", password="existing-config-hash", auth_disabled=True, secret_key="s" * 48,
                           external_logout_url="https://obsolete.invalid/logout")
    settings["schedule"] = {"enabled": True, "time": "13:15"}
    config.write_text(yaml.safe_dump(settings))
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE users(username TEXT,role TEXT,disabled INTEGER,password_hash TEXT)")
        connection.execute("INSERT INTO users VALUES('owner','admin',0,'existing-user-hash')")
    connection.close()
    capture(app, state, unit_root=units)
    assert enable_accounts(app)["accounts_enabled"] is True
    expected = dict(settings)
    expected["web"] = {key: value for key, value in settings["web"].items()
                       if key not in {"auth_disabled", "external_logout_url"}}
    assert yaml.safe_load(config.read_text()) == expected
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT password_hash FROM users").fetchone()[0] == "existing-user-hash"
    connection.close()
    restore(state)
    assert yaml.safe_load(config.read_text()) == settings


def test_account_activation_without_ready_operator_keeps_config(tmp_path):
    app = tmp_path / "app"
    database = _installation(app)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE users(username TEXT,role TEXT,disabled INTEGER)")
    connection.close()
    config = app / "data/config.yaml"
    before = config.read_bytes()
    with pytest.raises(ValueError, match="Betreiberzugang"):
        enable_accounts(app)
    assert config.read_bytes() == before


def test_active_background_tasks_prevent_snapshot_before_any_writes(tmp_path):
    app, units, state = tmp_path / "app", tmp_path / "units", tmp_path / "snapshot"
    database = _installation(app)
    units.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE background_tasks(status TEXT)")
        connection.execute("INSERT INTO background_tasks VALUES('running')")
    connection.close()
    with pytest.raises(ValueError, match="Hintergrundaufgaben"):
        capture(app, state, unit_root=units)
    assert not state.exists()


def test_updater_keeps_rollback_slots_and_resumes_timers_after_gates():
    script = Path("proxmox/update-local.sh").read_text(encoding="utf-8")
    rollback = script.split("restore_on_error() {", 1)[1].split("trap restore_on_error ERR", 1)[0]
    assert "--exclude='/venv.previous/'" in rollback
    assert "--exclude='/playwright-browsers.previous/'" in rollback
    assert 'release_state.py" restore' in rollback
    assert "poll_local_health" in rollback
    assert "systemctl enable scrapper-job.timer" not in script
    assert script.index('release_state.py" preflight') < script.index("systemctl stop scrapper-web.service")
    assert script.rindex("restore_timer_activity") > script.index("required_methods =")


@pytest.mark.parametrize("review,import_active,backup_active,expected", [
    (False, False, False, ["stop scrapper-job.timer", "stop scrapper-db-backup.timer"]),
    (False, True, False, ["start scrapper-job.timer", "stop scrapper-db-backup.timer"]),
    (True, True, True, ["disable --now scrapper-job.timer", "start scrapper-db-backup.timer"]),
])
def test_timer_resume_executes_only_previously_active_jobs(review, import_active, backup_active, expected):
    bash = shutil.which("bash") or str(Path("C:/Program Files/Git/bin/bash.exe"))
    if not Path(bash).is_file():
        pytest.skip("Bash is required for the timer policy simulation")
    script = Path("proxmox/update-local.sh").read_text(encoding="utf-8")
    start = script.index("restore_timer_activity() {")
    function = script[start:script.index("\n}\n", start) + 3]
    # A shell function substitutes systemctl, so no real service is touched.
    harness = '\n'.join([
        "set -euo pipefail", 'systemctl() { printf "%s\\n" "$*"; }',
        f"IS_REVIEW_INSTANCE={int(review)}", "declare -A TIMER_WAS_ACTIVE",
        f"TIMER_WAS_ACTIVE[scrapper-job.timer]={int(import_active)}",
        f"TIMER_WAS_ACTIVE[scrapper-db-backup.timer]={int(backup_active)}",
        function.replace(' >/dev/null', ''), "restore_timer_activity",
    ])
    result = subprocess.run([bash, "-c", harness], capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == expected
