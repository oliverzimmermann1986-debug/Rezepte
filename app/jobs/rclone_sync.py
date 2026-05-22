"""
rclone-Backup-Job.
Ersetzt das ursprüngliche rclone-sync.sh, aber konfigurierbar via config.yaml und
über das Web-UI triggerbar.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from ..config_store import get_config

logger = logging.getLogger(__name__)


# rclone braucht ein beschreibbares Cache-Verzeichnis (bisync legt dort
# Listing- und Lock-Files an). Mit ProtectHome=read-only in der systemd-Unit
# wäre der Default ~/.cache/rclone/ nicht beschreibbar. Daher umlenken
# nach data/ - liegt in ReadWritePaths und survives Restarts.
RCLONE_CACHE_DIR = "/opt/scrapper/data/.rclone-cache"


def _rclone_cache_args() -> List[str]:
    """Wird vor jeden rclone-Subprocess-Call gehängt damit Cache + bisync-
    Workdir in unserem beschreibbaren Bereich landen."""
    Path(RCLONE_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    return ["--cache-dir", RCLONE_CACHE_DIR]


# Globaler State - thread-safe verwaltet
_ACTIVE_PROCS: List[subprocess.Popen] = []   # laufende rclone-Subprozesse für Cancel
_ACTIVE_PROCS_LOCK = threading.Lock()
_CANCEL_EVENT = threading.Event()             # gesetzt = keine neuen Pairs starten


def _register_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.append(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCS_LOCK:
        try:
            _ACTIVE_PROCS.remove(proc)
        except ValueError:
            pass


def cancel_job() -> dict:
    """Killt alle laufenden rclone-Subprozesse + setzt cancel flag."""
    _CANCEL_EVENT.set()
    with _ACTIVE_PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    killed = 0
    for proc in procs:
        try:
            proc.terminate()
            killed += 1
        except Exception:
            pass
    return {"ok": True, "killed": killed}


def is_cancelled() -> bool:
    return _CANCEL_EVENT.is_set()


def reset_cancel() -> None:
    """Setzt das Cancel-Flag zurück. Wird beim Job-Start aufgerufen."""
    _CANCEL_EVENT.clear()


def _is_remote(path: str) -> bool:
    """True wenn ``path`` ein rclone-Remote-Pfad ist (z.B. ``pcloud:/foo``).

    rclone-Konvention: 'remotename:relpath' - ohne führenden Slash und mit ':'.
    Wir nutzen das, um automatisch zwischen lokalen Filesystem-Pfaden und
    Cloud→Cloud Sync zu unterscheiden, ohne zusätzliches Config-Feld.
    """
    if not path:
        return False
    if path.startswith("/"):
        return False
    return ":" in path


def _pair_stats(path: str) -> Tuple[int, str]:
    """Stats für eine beliebige Seite eines Paars - lokal oder rclone-Remote."""
    if _is_remote(path):
        return _rclone_stats_remote(path)
    return _local_stats(path)


def _rclone_stats_remote(remote: str) -> Tuple[int, str]:
    """Return (file_count, size_human). Bei Fehler (0, '?')"""
    try:
        c = subprocess.run(
            ["rclone", "lsf", remote, "--recursive", "--files-only"],
            capture_output=True, text=True, timeout=120,
        )
        files = len(c.stdout.splitlines()) if c.returncode == 0 else 0
        s = subprocess.run(
            ["rclone", "size", remote],
            capture_output=True, text=True, timeout=120,
        )
        size = "?"
        if s.returncode == 0:
            m = re.search(r"Total size:\s+([\d.]+\s*\w+)", s.stdout)
            if m:
                size = m.group(1)
        return files, size
    except Exception as e:
        logger.error(f"rclone stats {remote}: {e}")
        return 0, "?"


def _local_stats(path: str) -> Tuple[int, str]:
    try:
        p = Path(path)
        if not p.exists():
            return 0, "0"
        files = sum(1 for _ in p.rglob("*") if _.is_file())
        # du -sh ähnlich
        d = subprocess.run(["du", "-sh", path], capture_output=True, text=True, timeout=60)
        size = d.stdout.split()[0] if d.returncode == 0 else "?"
        return files, size
    except Exception as e:
        logger.error(f"local stats {path}: {e}")
        return 0, "?"


def _sync_pair(pair: Dict, args: List[str], log_dir: Path, dry_run: bool) -> Dict:
    name = pair["name"]
    remote = pair["remote"]
    local = pair["local"]

    # Nur mkdir wenn die zweite Seite tatsächlich ein lokaler Pfad ist.
    # Cloud→Cloud-Pairs (z.B. pcloud:/x ↔ gdrive:/y) haben keinen
    # lokalen Mountpoint, da würde mkdir Müll-Verzeichnisse im cwd anlegen.
    if not _is_remote(local):
        Path(local).mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"sync-{name}-{datetime.now():%Y%m%d-%H%M%S}.log"

    cloud_files, cloud_size = _pair_stats(remote)
    local_files_before, local_size_before = _pair_stats(local)

    cmd = [
        "rclone", "bisync", remote, local,
        *_rclone_cache_args(),
        "--stats", "10s", "--stats-one-line",
    ] + args
    if dry_run:
        cmd.append("--dry-run")

    logger.info(f"[{name}] {' '.join(shlex.quote(c) for c in cmd)}")

    summary = {
        "name": name,
        "remote": remote,
        "local": local,
        "cloud_files": cloud_files,
        "cloud_size": cloud_size,
        "local_files_before": local_files_before,
        "local_size_before": local_size_before,
        "log_file": str(log_file),
        "ok": False,
        "error": "",
        "transferred": 0,
    }

    try:
        if is_cancelled():
            summary["error"] = "vor Start abgebrochen"
            return summary
        with open(log_file, "w") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
            _register_proc(proc)
            try:
                proc.wait(timeout=4 * 3600)
                res_returncode = proc.returncode
            finally:
                
                    _unregister_proc(proc)
        class _R: pass
        res = _R()
        res.returncode = res_returncode
        # Bei "Must run --resync" automatisch nachholen
        log_content = log_file.read_text(errors="ignore") if log_file.exists() else ""
        if res.returncode != 0 and "Must run --resync" in log_content:
            logger.info(f"[{name}] auto --resync")
            cmd_resync = ["rclone", "bisync", remote, local,
                          *_rclone_cache_args(), "--resync"] + args
            if dry_run:
                cmd_resync.append("--dry-run")
            if is_cancelled():
                summary["error"] = "abgebrochen vor --resync"
                return summary
            with open(log_file, "a") as f:
                f.write("\n\n=== AUTO --resync ===\n\n")
                proc2 = subprocess.Popen(cmd_resync, stdout=f, stderr=subprocess.STDOUT)
                _register_proc(proc2)
                try:
                    proc2.wait(timeout=4 * 3600)
                    res.returncode = proc2.returncode
                finally:
                    
                        _unregister_proc(proc2)
            log_content = log_file.read_text(errors="ignore")

        summary["ok"] = (res.returncode == 0)
        if not summary["ok"]:
            summary["error"] = f"rclone exit {res.returncode}"
        # Geschätzte Anzahl transferierter Dateien
        summary["transferred"] = sum(1 for ln in log_content.splitlines()
                                       if "Copied" in ln or "Transferred:" in ln and "/" in ln)
    except subprocess.TimeoutExpired:
        summary["error"] = "Timeout"
        logger.error(f"[{name}] Timeout")
    except Exception as e:
        summary["error"] = str(e)
        logger.error(f"[{name}] Exception: {e}")

    lf, ls = _pair_stats(local)
    summary["local_files_after"] = lf
    summary["local_size_after"] = ls
    if cloud_files != lf:
        summary["warning"] = "Cloud/Lokal Anzahl unterschiedlich"
    return summary


def _parse_rclone_args(value) -> list:
    """rclone_args kann als String ('--foo --bar=1') oder als Liste gespeichert sein.
    Liefert immer eine Liste von Token."""
    if not value:
        return []
    if isinstance(value, list):
        # Liste kann Strings mit Mehrfach-Args enthalten, also nochmal splitten
        out = []
        for item in value:
            if isinstance(item, str):
                out.extend(item.split())
        return out
    if isinstance(value, str):
        return value.split()
    return []


def run_job(dry_run: bool = False, pairs_filter: list = None) -> Dict:
    cfg = get_config()
    backup_cfg = cfg.get("backup", default={}) or {}
    if not backup_cfg.get("enabled", True):
        logger.info("Backup deaktiviert")
        return {"enabled": False}

    pairs = backup_cfg.get("pairs") or []
    if not pairs:
        return {"enabled": True, "ok": False, "error": "Keine Sync-Paare konfiguriert"}

    if pairs_filter:
        wanted = set(pairs_filter)
        pairs = [p for p in pairs if p.get("name") in wanted]
        if not pairs:
            return {"enabled": True, "ok": False, "error": f"Keine Paare passen zu Filter: {pairs_filter}"}

    reset_cancel()

    args = _parse_rclone_args(backup_cfg.get("rclone_args"))
    log_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs")) / "rclone"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Lock-Cleanup (von altem Script übernommen)
    try:
        for lck in Path(os.path.expanduser("~/.cache/rclone/bisync/")).glob("*.lck"):
            lck.unlink()
    except Exception:
        pass

    start = time.time()
    results: List[Dict] = []
    # Cap auf max_parallel - vermeidet, dass z.B. 30 rclone-Prozesse gleichzeitig
    # pCloud-Connections aufmachen (führt zu Drosselung + RAM-Explosion).
    max_parallel = int(backup_cfg.get("max_parallel", 3))
    max_parallel = max(1, min(max_parallel, len(pairs)))
    logger.info(f"Starte Backup mit {max_parallel} parallelen Worker(n) für {len(pairs)} Paar(e)")
    with ThreadPoolExecutor(max_workers=max_parallel) as ex:
        futures = {ex.submit(_sync_pair, p, args, log_dir, dry_run): p for p in pairs}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"name": futures[fut].get("name", "?"), "ok": False, "error": str(e)})

    duration = time.time() - start
    total_transferred = sum(r.get("transferred", 0) for r in results)
    ok_count = sum(1 for r in results if r.get("ok"))
    summary = {
        "started_at": datetime.fromtimestamp(start).isoformat(),
        "duration_sec": round(duration, 1),
        "dry_run": dry_run,
        "pairs": results,
        "ok_count": ok_count,
        "total_pairs": len(pairs),
        "total_transferred": total_transferred,
    }

    return summary

def run_quick(remote_path: str, local_path: str, direction: str = "bisync",
              mode: str = "bisync", dry_run: bool = False,
              extra_args: list = None) -> Dict:
    """Ad-hoc Sync ohne Config-Paar.
    direction: 'pull' (remote→local), 'push' (local→remote), 'bisync' (bidir)
    mode: 'copy' (nur kopieren, kein Löschen), 'sync' (mirror, löscht), 'bisync'
    """
    from ..config_store import get_config as _gc
    cfg = _gc()

    log_dir = Path(cfg.get("paths", "logs_dir", default="/opt/scrapper/logs")) / "rclone"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{remote_path}-{local_path}")[:80]
    log_file = log_dir / f"quick-{safe_name}-{datetime.now():%Y%m%d-%H%M%S}.log"

    args = _parse_rclone_args(cfg.get("backup", "rclone_args", default=""))
    if extra_args:
        args += extra_args
    if dry_run and "--dry-run" not in args:
        args.append("--dry-run")

    reset_cancel()
    summary = {
        "direction": direction, "mode": mode,
        "remote": remote_path, "local": local_path,
        "dry_run": dry_run, "log_file": str(log_file),
    }

    # Befehl bauen
    cache_args = _rclone_cache_args()
    if direction == "bisync" or mode == "bisync":
        cmd = ["rclone", "bisync", remote_path, local_path, *cache_args] + args
        verb = "bisync"
    else:
        # rclone copy oder rclone sync
        rclone_verb = "sync" if mode == "sync" else "copy"
        if direction == "pull":
            cmd = ["rclone", rclone_verb, remote_path, local_path, *cache_args] + args
        else:  # push
            cmd = ["rclone", rclone_verb, local_path, remote_path, *cache_args] + args
        verb = f"{rclone_verb} {direction}"

    logger.info(f"[quick] rclone {' '.join(cmd[1:])}")
    summary["cmd"] = " ".join(cmd)
    summary["verb"] = verb

    try:
        if is_cancelled():
            summary["error"] = "vor Start abgebrochen"
            summary["ok"] = False
            return summary
        with open(log_file, "w") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
            _register_proc(proc)
            try:
                proc.wait(timeout=12 * 3600)
                rc = proc.returncode
            finally:
                
                    _unregister_proc(proc)
        summary["return_code"] = rc
        log_tail = log_file.read_text(errors="ignore")[-4096:]

        # bisync resync handling
        if (verb == "bisync" and rc != 0 and
            ("--resync" in log_tail or "Must run --resync" in log_tail) and
            not is_cancelled()):
            logger.info("[quick] auto --resync")
            cmd_r = cmd + ["--resync"]
            with open(log_file, "a") as f:
                f.write("\n\n=== AUTO --resync ===\n\n")
                proc2 = subprocess.Popen(cmd_r, stdout=f, stderr=subprocess.STDOUT)
                _register_proc(proc2)
                try:
                    proc2.wait(timeout=12 * 3600)
                    rc = proc2.returncode
                finally:
                    
                        _unregister_proc(proc2)
            summary["resync_return_code"] = rc

        summary["ok"] = (rc == 0)
        return summary
    except Exception as e:
        logger.exception("quick sync failed")
        summary["ok"] = False
        summary["error"] = str(e)
        return summary
