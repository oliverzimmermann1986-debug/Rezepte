"""
Persistente Speicherung für:
  - pending Items (unklare KI-Analysen)
  - history (bereits verarbeitete URLs)
  - jobs (Status, Logs)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DB_PATH = Path(os.environ.get("SCRAPPER_DB_PATH", "/opt/scrapper/data/scrapper.db"))


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
  kind TEXT NOT NULL,              -- 'scraper' | 'reanalyze'
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
            self._migrate_history(c0)
            c0.commit()
        finally:
            c0.close()

    @staticmethod
    def _history_item_id(url: str) -> str:
        return hashlib.sha256(str(url).encode("utf-8", errors="replace")).hexdigest()[:24]

    def _migrate_history(self, connection: sqlite3.Connection) -> None:
        """Erweitert bestehende Datenbanken ohne destructive Migration.

        Die zusätzlichen Felder bilden den durchsuchbaren Rezeptkatalog ab.
        Bestehende Installationen erhalten stabile IDs; Metadaten werden beim
        ersten Aufruf der Rezept-API aus den vorhandenen ``info.json`` Dateien
        nachgezogen.
        """
        columns = {row[1] for row in connection.execute("PRAGMA table_info(history)")}
        additions = {
            "item_id": "TEXT",
            "recipe_type": "TEXT",
            "category": "TEXT",
            "description": "TEXT",
            "source": "TEXT",
            "metadata_indexed": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE history ADD COLUMN {name} {sql_type}")
        rows = connection.execute(
            "SELECT url FROM history WHERE item_id IS NULL OR item_id=''"
        ).fetchall()
        for (url,) in rows:
            connection.execute(
                "UPDATE history SET item_id=? WHERE url=?",
                (self._history_item_id(url), url),
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_history_item_id ON history(item_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_recipe_lookup "
            "ON history(content_type, recipe_type, category, processed_at DESC)"
        )

    @contextmanager
    def conn(self):
        # SQLite: pro-Aufruf Verbindung, dank check_same_thread=False thread-safe genug.
        # synchronous=NORMAL ist per-connection und muss jedes Mal gesetzt werden;
        # journal_mode=WAL ist file-persistent (einmalig in __init__ gesetzt).
        c = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.create_function(
            "CASEFOLD", 1,
            lambda value: str(value or "").casefold(),
            deterministic=True,
        )
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

    def history_add(
        self,
        url: str,
        *,
        content_type: str = "",
        name: str = "",
        target_dir: str = "",
        recipe_type: str = "",
        category: str = "",
        description: str = "",
        source: str = "",
    ) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO history "
                "(url, processed_at, content_type, name, target_dir, item_id, "
                " recipe_type, category, description, source, metadata_indexed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url, time.time(), content_type, name, target_dir,
                    self._history_item_id(url), recipe_type, category,
                    (description or "")[:50000], source,
                    int(
                        content_type == "recipe"
                        and bool(target_dir)
                        and any(
                            str(value or "").strip()
                            for value in (recipe_type, category, description, source)
                        )
                    ),
                ),
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

    def history_update(
        self,
        url: str,
        *,
        name: str = None,
        target_dir: str = None,
        content_type: str = None,
        recipe_type: str = None,
        category: str = None,
        description: str = None,
        source: str = None,
    ) -> None:
        values = {
            "name": name,
            "target_dir": target_dir,
            "content_type": content_type,
            "recipe_type": recipe_type,
            "category": category,
            "description": (description[:50000] if isinstance(description, str) else description),
            "source": source,
        }
        if all(value is None for value in values.values()):
            return
        with self.conn() as c:
            metadata_changed = any(values[key] is not None for key in (
                "recipe_type", "category", "description", "source"
            ))
            for column, value in values.items():
                if value is not None:
                    c.execute(f"UPDATE history SET {column}=? WHERE url=?", (value, url))
            if metadata_changed:
                c.execute("UPDATE history SET metadata_indexed=1 WHERE url=?", (url,))

    def recipe_index_metadata_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Schreibt den initialen Katalogindex in einer einzigen Transaktion."""
        if not entries:
            return
        rows = []
        for entry in entries:
            rows.append((
                entry.get("name") or "Unbenanntes Rezept",
                entry.get("recipe_type") or "Sonstiges",
                entry.get("category") or "Allgemein",
                (entry.get("description") or "")[:50000],
                entry.get("source") or "",
                entry["url"],
            ))
        with self.conn() as c:
            c.executemany(
                "UPDATE history SET name=?, recipe_type=?, category=?, "
                "description=?, source=?, metadata_indexed=1 WHERE url=?",
                rows,
            )

    def history_get_by_item_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM history WHERE item_id=? LIMIT 1", (item_id,)
            ).fetchone()
            return dict(row) if row else None

    def recipe_all(self, limit: int = 10000) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 50000))
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM history WHERE content_type='recipe' "
                "ORDER BY processed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def recipe_search(
        self,
        *,
        query: str = "",
        recipe_type: str = "",
        category: str = "",
        sort: str = "newest",
        limit: int = 60,
        offset: int = 0,
    ) -> Dict[str, Any]:
        where = ["content_type='recipe'", "COALESCE(target_dir, '') <> ''"]
        params: List[Any] = []
        query = (query or "").strip().casefold()
        if query:
            like = f"%{query}%"
            where.append(
                "(CASEFOLD(COALESCE(name,'')) LIKE ? OR "
                " CASEFOLD(COALESCE(recipe_type,'')) LIKE ? OR "
                " CASEFOLD(COALESCE(category,'')) LIKE ? OR "
                " CASEFOLD(COALESCE(description,'')) LIKE ?)"
            )
            params.extend([like, like, like, like])
        if recipe_type:
            where.append("recipe_type=?")
            params.append(recipe_type)
        if category:
            where.append("category=?")
            params.append(category)
        order = {
            "newest": "processed_at DESC",
            "oldest": "processed_at ASC",
            "name": "CASEFOLD(COALESCE(name,'')) ASC, processed_at DESC",
            "type": "CASEFOLD(COALESCE(recipe_type,'')) ASC, CASEFOLD(COALESCE(name,'')) ASC",
        }.get(sort, "processed_at DESC")
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where_sql = " AND ".join(where)
        with self.conn() as c:
            total = int(c.execute(
                f"SELECT COUNT(*) FROM history WHERE {where_sql}", params
            ).fetchone()[0])
            rows = c.execute(
                f"SELECT * FROM history WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            types = [row[0] for row in c.execute(
                "SELECT DISTINCT recipe_type FROM history "
                "WHERE content_type='recipe' AND COALESCE(recipe_type,'')<>'' "
                "ORDER BY CASEFOLD(recipe_type)"
            ).fetchall()]
            categories = [row[0] for row in c.execute(
                "SELECT DISTINCT category FROM history "
                "WHERE content_type='recipe' AND COALESCE(category,'')<>'' "
                "ORDER BY CASEFOLD(category)"
            ).fetchall()]
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "types": types,
            "categories": categories,
            "limit": limit,
            "offset": offset,
        }

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
        query = {
            "newest": "SELECT * FROM pending WHERE status=? ORDER BY created_at DESC",
            "oldest": "SELECT * FROM pending WHERE status=? ORDER BY created_at ASC",
            "confidence_asc": (
                "SELECT * FROM pending WHERE status=? "
                "ORDER BY CAST(json_extract(ai_suggestion, '$.confidence') AS REAL) ASC, created_at DESC"
            ),
            "confidence_desc": (
                "SELECT * FROM pending WHERE status=? "
                "ORDER BY CAST(json_extract(ai_suggestion, '$.confidence') AS REAL) DESC, created_at DESC"
            ),
        }.get(sort, "SELECT * FROM pending WHERE status=? ORDER BY created_at DESC")
        with self.conn() as c:
            rows = c.execute(query, (status,)).fetchall()
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

    def download_failures_list(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Liste aller URLs die mehrfach fehlgeschlagen sind. Für Re-Process-UI."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT url, first_seen, last_try, attempts, last_error "
                "FROM download_failures "
                "ORDER BY last_try DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def download_failures_clear_all(self) -> int:
        """Alle Failure-Einträge löschen damit URLs beim nächsten Mail-Sync
        nochmal versucht werden. Returnt Anzahl gelöschter Zeilen."""
        with self.conn() as c:
            cur = c.execute("DELETE FROM download_failures")
            return cur.rowcount

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
            if not row:
                return None
            data = dict(row)
            try:
                data["summary"] = json.loads(data.get("summary") or "{}")
            except Exception:
                data["summary"] = {}
            return data

    def running_jobs(self) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs WHERE status='running' ORDER BY started_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def reset_stale_running(self, protected_job_ids: Optional[Iterable[int]] = None) -> int:
        """Markiert verwaiste ``running``-Jobs nach einem Neustart als Fehler.

        Scraper und Backups können in separaten systemd-Prozessen weiterlaufen,
        während nur der Webdienst neu startet. Deren aktuellsten, per File-Lock
        nachweislich aktiven Job übergibt der Startup-Code als geschützt.
        """
        now = time.time()
        protected = {int(job_id) for job_id in (protected_job_ids or [])}
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, summary FROM jobs WHERE status='running'"
            ).fetchall()
            reset = 0
            for r in rows:
                if int(r["id"]) in protected:
                    continue
                try:
                    summary = json.loads(r["summary"] or "{}")
                except Exception:
                    summary = {}
                summary["error"] = summary.get("error") or "Process ended before job completion"
                summary["recovered_at"] = now
                c.execute(
                    "UPDATE jobs SET status='error', ended_at=?, summary=? WHERE id=?",
                    (now, json.dumps(summary, ensure_ascii=False), r["id"]),
                )
                reset += 1
            return reset

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

    def jobs_delete_failed(self) -> int:
        """Löscht ALLE Jobs mit Status='error'. Sinnvoll zum Aufräumen
        nach einer Reihe von Crashes (z.B. AI-Provider war down). Daten
        gehen nicht verloren - Jobs sind reine Log-Einträge.
        Returnt Anzahl gelöschter Zeilen."""
        with self.conn() as c:
            cur = c.execute("DELETE FROM jobs WHERE status='error'")
            return cur.rowcount or 0

    def pending_older_than(self, days: int = 30, status: str = "pending") -> List[Dict[str, Any]]:
        """Liefert alte Pending-Einträge, damit die zugehörigen Stash-Dateien
        vor einer Statusänderung kontrolliert entfernt werden können."""
        cutoff = time.time() - max(0, days) * 86400
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM pending WHERE status=? AND created_at < ? ORDER BY created_at ASC",
                (status, cutoff),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                try:
                    item["ai_suggestion"] = json.loads(item.get("ai_suggestion") or "{}")
                except Exception:
                    item["ai_suggestion"] = {}
                out.append(item)
            return out

    def auto_skip_old_pending(self, days: int = 30) -> int:
        """Markiert pending Items älter als ``days`` Tage als 'auto_skipped'."""
        cutoff = time.time() - max(0, days) * 86400
        with self.conn() as c:
            cur = c.execute(
                "UPDATE pending SET status='auto_skipped' "
                "WHERE status='pending' AND created_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0

    def cleanup_old_pending(self, days: int = 90) -> int:
        """Entfernt bereits erledigte Pending-Datensätze nach der Aufbewahrungsfrist."""
        cutoff = time.time() - max(0, days) * 86400
        with self.conn() as c:
            cur = c.execute(
                "DELETE FROM pending WHERE status!='pending' AND created_at < ?",
                (cutoff,),
            )
            return cur.rowcount or 0

    def backup_to(self, dest_path, *, compress: bool = False, verify: bool = True) -> dict:
        """Create a verified SQLite online backup and publish it atomically."""
        from pathlib import Path as _P
        import gzip
        import shutil

        dest = _P(dest_path)
        if compress and not str(dest).endswith(".gz"):
            dest = _P(str(dest) + ".gz")
        dest.parent.mkdir(parents=True, exist_ok=True)

        nonce = f"{os.getpid()}-{threading.get_ident()}"
        tmp_db = dest.parent / f".tmp-{dest.name}.{nonce}.db"
        tmp_out = dest.parent / f".tmp-{dest.name}.{nonce}.out"
        try:
            src = sqlite3.connect(str(self.path), timeout=10)
            dst = sqlite3.connect(str(tmp_db), timeout=10)
            try:
                with dst:
                    src.backup(dst)
            finally:
                src.close()
                dst.close()

            verified = None
            if verify:
                check = sqlite3.connect(str(tmp_db), timeout=10)
                try:
                    row = check.execute("PRAGMA integrity_check").fetchone()
                    verified = bool(row and row[0] == "ok")
                    if not verified:
                        raise RuntimeError(f"integrity_check failed: {row}")
                finally:
                    check.close()

            if compress:
                with open(tmp_db, "rb") as fin, open(tmp_out, "wb") as raw_out:
                    with gzip.GzipFile(fileobj=raw_out, mode="wb", compresslevel=6) as zipped:
                        shutil.copyfileobj(fin, zipped, length=1024 * 1024)
                    raw_out.flush()
                    os.fsync(raw_out.fileno())
                os.chmod(tmp_out, 0o600)
                os.replace(tmp_out, dest)
            else:
                os.chmod(tmp_db, 0o600)
                os.replace(tmp_db, dest)

            os.chmod(dest, 0o600)
            dir_fd = os.open(str(dest.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

            return {
                "ok": True,
                "dest": str(dest),
                "size_bytes": dest.stat().st_size,
                "compressed": bool(compress),
                "verified": verified,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "dest": str(dest)}
        finally:
            for temporary in (tmp_db, tmp_out):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

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
