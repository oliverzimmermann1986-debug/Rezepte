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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from ..config_store import get_config
from ..core.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


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

    Path(local).mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"sync-{name}-{datetime.now():%Y%m%d-%H%M%S}.log"

    cloud_files, cloud_size = _rclone_stats_remote(remote)
    local_files_before, local_size_before = _local_stats(local)

    cmd = [
        "rclone", "bisync", remote, local,
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
            _ACTIVE_PROCS.append(proc)
            try:
                proc.wait(timeout=4 * 3600)
                res_returncode = proc.returncode
            finally:
                if proc in _ACTIVE_PROCS:
                    _ACTIVE_PROCS.remove(proc)
        class _R: pass
        res = _R()
        res.returncode = res_returncode
        # Bei "Must run --resync" automatisch nachholen
        log_content = log_file.read_text(errors="ignore") if log_file.exists() else ""
        if res.returncode != 0 and "Must run --resync" in log_content:
            logger.info(f"[{name}] auto --resync")
            cmd_resync = ["rclone", "bisync", remote, local, "--resync"] + args
            if dry_run:
                cmd_resync.append("--dry-run")
            if is_cancelled():
                summary["error"] = "abgebrochen vor --resync"
                return summary
            with open(log_file, "a") as f:
                f.write("\n\n=== AUTO --resync ===\n\n")
                proc2 = subprocess.Popen(cmd_resync, stdout=f, stderr=subprocess.STDOUT)
                _ACTIVE_PROCS.append(proc2)
                try:
                    proc2.wait(timeout=4 * 3600)
                    res.returncode = proc2.returncode
                finally:
                    if proc2 in _ACTIVE_PROCS:
                        _ACTIVE_PROCS.remove(proc2)
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

    lf, ls = _local_stats(local)
    summary["local_files_after"] = lf
    summary["local_size_after"] = ls
    if cloud_files != lf:
        summary["warning"] = "Cloud/Lokal Anzahl unterschiedlich"
    return summary


_ACTIVE_PROCS: list = []  # globale Liste der laufenden subprocess.Popen für Cancel
_CANCEL_EVENT = None       # threading.Event() — wenn gesetzt → keine neuen Pairs starten

def cancel_job() -> dict:
    """Killt alle laufenden rclone-Subprozesse + setzt cancel flag."""
    import threading
    global _CANCEL_EVENT
    if _CANCEL_EVENT is None:
        _CANCEL_EVENT = threading.Event()
    _CANCEL_EVENT.set()
    killed = 0
    for proc in list(_ACTIVE_PROCS):
        try:
            proc.terminate()
            killed += 1
        except Exception:
            pass
    return {"ok": True, "killed": killed}


def is_cancelled() -> bool:
    return _CANCEL_EVENT is not None and _CANCEL_EVENT.is_set()


def reset_cancel():
    global _CANCEL_EVENT
    import threading
    _CANCEL_EVENT = threading.Event()


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

    args = backup_cfg.get("rclone_args") or []
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
    with ThreadPoolExecutor(max_workers=len(pairs)) as ex:
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

    # Telegram-Bericht
    tg_cfg = cfg.get("telegram", default={}) or {}
    notifier = TelegramNotifier(
        tg_cfg.get("backup_bot_token", "") or tg_cfg.get("recipe_bot_token", ""),
        tg_cfg.get("backup_chat_id", "") or tg_cfg.get("recipe_chat_id", ""),
        label="backup",
    )
    if notifier.enabled and tg_cfg.get("enabled", True):
        lines = ["📊 <b>Backup Bericht</b>", ""]
        for r in results:
            ok = "✅" if r.get("ok") else "❌"
            lines.append(f"{ok} <b>{r['name']}</b>")
            lines.append(f"☁️ {r.get('cloud_files', 0)} Dateien · {r.get('cloud_size','?')}")
            lines.append(f"🖥 {r.get('local_files_after', 0)} Dateien · {r.get('local_size_after','?')}")
            if r.get("warning"):
                lines.append(f"⚠️ {r['warning']}")
            if r.get("error"):
                lines.append(f"❗ {r['error']}")
            lines.append("")
        lines.append(f"⏱ {int(duration // 60)}m {int(duration % 60)}s · "
                       f"{ok_count}/{len(pairs)} OK")
        if dry_run:
            lines.insert(1, "🔍 (DRY-RUN)")
        notifier.send("\n".join(lines))

    return summary
