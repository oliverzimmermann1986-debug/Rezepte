"""
Persistente Speicherung für:
  - pending Items (unklare KI-Analysen)
  - history (bereits verarbeitete URLs)
  - jobs (Status, Logs)
"""
from __future__ import annotations

import json
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

CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs(kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status, created_at DESC);
"""


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(_DDL)

    @contextmanager
    def conn(self):
        # SQLite: pro-Aufruf Verbindung, dank check_same_thread=False thread-safe genug
        c = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
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
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO pending "
                "(url, content_type, created_at, description, video_path, frame_path, ai_suggestion, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
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

    def pending_list(self, status: str = "pending") -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM pending WHERE status=? ORDER BY created_at DESC",
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


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
