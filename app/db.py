"""
Persistente Speicherung für:
  - pending Items (unklare KI-Analysen)
  - history (bereits verarbeitete URLs)
  - jobs (Status, Logs)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = Path("/opt/scrapper/data/scrapper.db")


_DDL = """
CREATE TABLE IF NOT EXISTS history (
  url TEXT PRIMARY KEY,
  processed_at REAL NOT NULL,
  content_type TEXT,
  name TEXT,
  target_dir TEXT
);

CREATE TABLE IF NOT EXISTS pending (
  url TEXT PRIMARY KEY,
  content_type TEXT NOT NULL,      -- 'recipe' | 'wedding'
  created_at REAL NOT NULL,
  description TEXT,
  video_path TEXT,
  frame_path TEXT,
  ai_suggestion TEXT,              -- JSON
  status TEXT DEFAULT 'pending'    -- pending | resolved | skipped
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- 'scraper' | 'backup'
  started_at REAL NOT NULL,
  ended_at REAL,
  status TEXT,                     -- running | ok | error
  summary TEXT,                    -- JSON
  log_file TEXT
);

CREATE TABLE IF NOT EXISTS download_failures (
  url TEXT PRIMARY KEY,
  first_seen REAL NOT NULL,
  last_try REAL NOT NULL,
  attempts INTEGER DEFAULT 1,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs(kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, kind);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_processed ON history(processed_at DESC);
"""


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Pragmas einmalig auf einer separaten Verbindung setzen (WAL bleibt erhalten).
        c0 = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        try:
            c0.execute("PRAGMA journal_mode=WAL")
            c0.execute("PRAGMA synchronous=NORMAL")
            c0.execute("PRAGMA busy_timeout=10000")
            c0.executescript(_DDL)
            c0.commit()
        finally:
            c0.close()

    @contextmanager
    def conn(self):
        # SQLite: pro-Aufruf Verbindung, dank check_same_thread=False thread-safe genug.
        # synchronous=NORMAL ist per-connection und muss jedes Mal gesetzt werden;
        # journal_mode=WAL ist file-persistent (einmalig in __init__ gesetzt).
        c = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            yield c
            c.commit()
        finally:
            c.close()

    # ---------------- History ----------------
    def history_has(self, url: str) -> bool:
        with self.conn() as c:
            row = c.execute("SELECT 1 FROM history WHERE url=?", (url,)).fetchone()
            return row is not None

    def history_add(self, url: str, *, content_type: str = "", name: str = "",
                    target_dir: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO history (url, processed_at, content_type, name, target_dir) "
                "VALUES (?, ?, ?, ?, ?)",
                (url, time.time(), content_type, name, target_dir),
            )

    def history_list(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM history ORDER BY processed_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def history_get(self, url: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM history WHERE url=?", (url,)).fetchone()
            return dict(row) if row else None

    def history_update(self, url: str, *, name: str = None, target_dir: str = None,
                        content_type: str = None) -> None:
        sets = []
        params = []
        if name is not None:
            sets.append("name=?"); params.append(name)
        if target_dir is not None:
            sets.append("target_dir=?"); params.append(target_dir)
        if content_type is not None:
            sets.append("content_type=?"); params.append(content_type)
        if not sets:
            return
        params.append(url)
        with self.conn() as c:
            c.execute(f"UPDATE history SET {', '.join(sets)} WHERE url=?", params)

    def history_delete(self, url: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM history WHERE url=?", (url,))

    # ---------------- Pending ----------------
    def pending_add(
        self,
        url: str,
        content_type: str,
        *,
        description: Optional[str] = None,
        video_path: Optional[str] = None,
        frame_path: Optional[str] = None,
        ai_suggestion: Optional[Dict] = None,
    ) -> None:
        """Upsert: bei Konflikt werden nur Description/Pfade/Vorschlag aktualisiert.
        ``status`` und ``created_at`` bleiben erhalten - sonst würde ein bereits
        resolved/skipped-Item beim erneuten Auftauchen wieder auf 'pending' springen."""
        with self.conn() as c:
            c.execute(
                "INSERT INTO pending "
                "(url, content_type, created_at, description, video_path, frame_path, ai_suggestion, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending') "
                "ON CONFLICT(url) DO UPDATE SET "
                "  content_type=excluded.content_type, "
                "  description=excluded.description, "
                "  video_path=excluded.video_path, "
                "  frame_path=excluded.frame_path, "
                "  ai_suggestion=excluded.ai_suggestion",
                (
                    url,
                    content_type,
                    time.time(),
                    description,
                    video_path,
                    frame_path,
                    json.dumps(ai_suggestion or {}, ensure_ascii=False),
                ),
            )

    def pending_list(self, status: str = "pending", sort: str = "newest") -> List[Dict[str, Any]]:
        """Liefert Pending-Items.

        sort:
          - 'newest' (default): neueste zuerst
          - 'oldest': älteste zuerst (für Aufräum-Workflow)
          - 'confidence_asc': niedrigste Confidence zuerst (am unsichersten)
          - 'confidence_desc': höchste Confidence zuerst (kandidaten für 'reanalyze')
        """
        order_by = {
            "newest": "created_at DESC",
            "oldest": "created_at ASC",
            "confidence_asc":
                "CAST(json_extract(ai_suggestion, '$.confidence') AS REAL) ASC, created_at DESC",
            "confidence_desc":
                "CAST(json_extract(ai_suggestion, '$.confidence') AS REAL) DESC, created_at DESC",
        }.get(sort, "created_at DESC")
        with self.conn() as c:
            rows = c.execute(
                f"SELECT * FROM pending WHERE status=? ORDER BY {order_by}",
                (status,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["ai_suggestion"] = json.loads(d.get("ai_suggestion") or "{}")
                except Exception:
                    d["ai_suggestion"] = {}
                result.append(d)
            return result

    def pending_get(self, url: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM pending WHERE url=?", (url,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["ai_suggestion"] = json.loads(d.get("ai_suggestion") or "{}")
            except Exception:
                d["ai_suggestion"] = {}
            return d

    def pending_resolve(self, url: str, status: str = "resolved") -> None:
        with self.conn() as c:
            c.execute("UPDATE pending SET status=? WHERE url=?", (status, url))

    def pending_update_suggestion(self, url: str, suggestion: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE pending SET ai_suggestion=? WHERE url=?",
                (json.dumps(suggestion, ensure_ascii=False), url),
            )

    def pending_count(self) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM pending WHERE status='pending'"
            ).fetchone()
            return int(row["n"]) if row else 0

    # ---------------- Download-Failures ----------------
    def download_failure_record(self, url: str, error: str) -> int:
        """Zählt einen Download-Fehlversuch. Returnt die neue Versuchszahl."""
        now = time.time()
        with self.conn() as c:
            c.execute(
                "INSERT INTO download_failures (url, first_seen, last_try, attempts, last_error) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "  last_try=excluded.last_try, "
                "  attempts=attempts + 1, "
                "  last_error=excluded.last_error",
                (url, now, now, (error or "")[:500]),
            )
            row = c.execute(
                "SELECT attempts FROM download_failures WHERE url=?", (url,)
            ).fetchone()
            return int(row["attempts"]) if row else 1

    def download_failure_attempts(self, url: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT attempts FROM download_failures WHERE url=?", (url,)
            ).fetchone()
            return int(row["attempts"]) if row else 0

    def download_failure_clear(self, url: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM download_failures WHERE url=?", (url,))

    # ---------------- Jobs ----------------
    def job_start(self, kind: str, log_file: str = "") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO jobs (kind, started_at, status, log_file) VALUES (?, ?, 'running', ?)",
                (kind, time.time(), log_file),
            )
            return int(cur.lastrowid)

    def job_set_log_file(self, job_id: int, log_file: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE jobs SET log_file=? WHERE id=?", (log_file, job_id))

    def job_update_summary(self, job_id: int, summary: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE jobs SET summary=? WHERE id=?",
                (json.dumps(summary, ensure_ascii=False), job_id),
            )

    def job_finish(self, job_id: int, status: str, summary: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE jobs SET ended_at=?, status=?, summary=? WHERE id=?",
                (time.time(), status, json.dumps(summary, ensure_ascii=False), job_id),
            )

    def job_list(self, kind: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.conn() as c:
            if kind:
                rows = c.execute(
                    "SELECT * FROM jobs WHERE kind=? ORDER BY started_at DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["summary"] = json.loads(d.get("summary") or "{}")
                except Exception:
                    d["summary"] = {}
                out.append(d)
            return out

    def job_get(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["summary"] = json.loads(d.get("summary") or "{}")
            except Exception:
                d["summary"] = {}
            return d

    def job_running(self, kind: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE kind=? AND status='running' ORDER BY started_at DESC LIMIT 1",
                (kind,),
            ).fetchone()
            return dict(row) if row else None

    def reset_stale_running(self) -> int:
        """Beim App-Start: Jobs die noch als 'running' markiert sind
        können nicht wirklich laufen (Process ist tot). Auf 'error' setzen
        und Reason in summary stempeln. Liefert Anzahl resetteter Jobs.
        """
        now = time.time()
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, summary FROM jobs WHERE status='running'"
            ).fetchall()
            for r in rows:
                try:
                    summary = json.loads(r["summary"] or "{}")
                except Exception:
                    summary = {}
                summary["error"] = summary.get("error") or "App restart while job was running"
                summary["recovered_at"] = now
                c.execute(
                    "UPDATE jobs SET status='error', ended_at=?, summary=? WHERE id=?",
                    (now, json.dumps(summary, ensure_ascii=False), r["id"]),
                )
            return len(rows)

    def cleanup_old_jobs(self, days: int = 90) -> int:
        """Löscht Job-Einträge älter als ``days`` Tage. Hindert die jobs-Tabelle
        am unbegrenzten Wachsen (bei OnCalendar=*:0/30 = 17.500/Jahr)."""
        cutoff = time.time() - days * 86400
        with self.conn() as c:
            cur = c.execute(
                "DELETE FROM jobs WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0

    def auto_skip_old_pending(self, days: int = 30) -> int:
        """Markiert pending Items älter als ``days`` Tage als 'auto_skipped'.
        Hindert die Pending-Liste am Vollstopfen mit toten Items.
        """
        cutoff = time.time() - days * 86400
        with self.conn() as c:
            cur = c.execute(
                "UPDATE pending SET status='auto_skipped' "
                "WHERE status='pending' AND created_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0

    def backup_to(self, dest_path, *, compress: bool = False, verify: bool = True) -> dict:
        """Online-Backup der SQLite-DB via PRAGMA-basierte .backup-API.
        Konsistent auch bei laufenden Writes (keine Locks nötig).

        Args:
            dest_path: Zielpfad. Bei compress=True wird '.gz' angehängt falls noch nicht da.
            compress:  Backup mit gzip komprimieren (~60-80% kleiner).
            verify:    Nach Backup PRAGMA integrity_check ausführen.

        Returnt {ok, dest, size_bytes, compressed, verified} oder {ok: False, error}.
        """
        from pathlib import Path as _P
        import gzip
        import shutil

        dest = _P(dest_path)
        if compress and not str(dest).endswith(".gz"):
            dest = _P(str(dest) + ".gz")
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Erst nach temp-Datei schreiben, dann atomic move/gzip - so kein
        # halb-fertiges Backup im Ziel-Verzeichnis bei Crash mittendrin.
        tmp_db = dest.parent / f".tmp-{dest.name}.{os.getpid()}.db"
        try:
            # Schritt 1: Online-Backup nach tmp-Datei (immer unkomprimiert,
            # damit wir verify und compress separat machen können).
            src = sqlite3.connect(str(self.path), timeout=10)
            dst = sqlite3.connect(str(tmp_db), timeout=10)
            try:
                with dst:
                    src.backup(dst)
            finally:
                src.close()
                dst.close()

            # Schritt 2: Integrity-Check der Kopie
            verified = None
            if verify:
                check = sqlite3.connect(str(tmp_db), timeout=10)
                try:
                    row = check.execute("PRAGMA integrity_check").fetchone()
                    verified = (row and row[0] == "ok")
                    if not verified:
                        return {"ok": False, "error": f"integrity_check failed: {row}",
                                "dest": str(dest)}
                finally:
                    check.close()

            # Schritt 3: Compress oder Move ins finale Ziel
            if compress:
                with open(tmp_db, "rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
                    shutil.copyfileobj(fin, fout)
                tmp_db.unlink(missing_ok=True)
            else:
                tmp_db.replace(dest)

            return {
                "ok": True,
                "dest": str(dest),
                "size_bytes": dest.stat().st_size,
                "compressed": bool(compress),
                "verified": verified,
            }
        except Exception as e:
            try:
                tmp_db.unlink(missing_ok=True)
            except Exception:
                pass
            return {"ok": False, "error": str(e)}

    def vacuum(self) -> dict:
        """SQLite-Speicher reclaimen nach vielen Deletes. Schreibt die DB neu
        und nimmt nur die genutzten Seiten - kann je nach Auslese 10-30%
        kleiner werden. Sollte gelegentlich (z.B. 1x pro Woche) laufen."""
        try:
            with self.conn() as c:
                size_before = self.path.stat().st_size
                c.execute("VACUUM")
                size_after = self.path.stat().st_size
            return {
                "ok": True,
                "size_before": size_before,
                "size_after": size_after,
                "reclaimed_bytes": max(0, size_before - size_after),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
