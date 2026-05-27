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

-- ─── Recipe-Browser (seit feat/recipe-browser-and-cart) ───────────────────
-- recipes: ein Eintrag pro indiziertem Rezept-Ordner. Logischer FK auf
-- history.url (kein REFERENCES, weil history nur per URL aufgebaut ist und
-- ein User auch Folder ohne URL anlegen können soll).
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE,                          -- NULL für manuell angelegte Folder
  name TEXT NOT NULL,
  type TEXT,                                -- Hauptgericht, Dessert, ...
  category TEXT,                            -- Pasta, Fleisch, ...
  folder_path TEXT NOT NULL UNIQUE,         -- /mnt/rezepte/Hauptgericht/Pasta/Lasagne
  description TEXT,                         -- Caption-Text (Source für KI)
  thumb_filename TEXT,                      -- relative {name}.jpg
  video_filename TEXT,                      -- relative {name}.mp4
  source_added_at REAL,                     -- Original history.processed_at
  indexed_at REAL NOT NULL,                 -- als die recipes-Zeile entstand
  ingredients_extracted_at REAL,            -- NULL = noch nicht durch KI
  ingredients_status TEXT DEFAULT 'pending' -- pending | ok | error | skipped
);
CREATE INDEX IF NOT EXISTS idx_recipes_type     ON recipes(type, category);
CREATE INDEX IF NOT EXISTS idx_recipes_added    ON recipes(source_added_at DESC);
CREATE INDEX IF NOT EXISTS idx_recipes_extract  ON recipes(ingredients_status, ingredients_extracted_at);

-- recipe_ingredients: pro Rezept N Zutaten. Kein FK auf eine Master-Tabelle —
-- canonical_name reicht für Merge & Filter und ist robust gegen Tippfehler
-- der KI (ein neuer Eintrag mit gleichem canonical_name verschmilzt sich
-- automatisch in Filter und Einkaufskorb).
CREATE TABLE IF NOT EXISTS recipe_ingredients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  name TEXT NOT NULL,                       -- Anzeigename (vom KI/User)
  canonical_name TEXT,                      -- normalisiert (lowercase, plural→singular)
  amount REAL,                              -- NULL bei "Prise", "nach Geschmack"
  unit TEXT,                                -- g/kg/ml/l/TL/EL/Stück/Prise/Bund/...
  raw TEXT,                                 -- Original-Snippet aus description
  sort_order INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingred_recipe    ON recipe_ingredients(recipe_id);
CREATE INDEX IF NOT EXISTS idx_ingred_canonical ON recipe_ingredients(canonical_name);

-- recipe_steps: Zubereitungs-Schritte pro Rezept. step_number ist 1-basiert.
-- timer_seconds optional — wenn die KI im Schritt einen Zeit-Hinweis findet
-- ("8 Min köcheln") setzt sie den Wert, sonst NULL. Frontend rendert dann
-- einen Stoppuhr-Button neben dem Schritt.
CREATE TABLE IF NOT EXISTS recipe_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  step_number INTEGER NOT NULL,             -- 1, 2, 3, ...
  instruction TEXT NOT NULL,                -- "Wasser zum Kochen bringen und Salz dazu"
  timer_seconds INTEGER                     -- z.B. 480 für 8 Min, NULL wenn kein Timer
);
CREATE INDEX IF NOT EXISTS idx_steps_recipe ON recipe_steps(recipe_id, step_number);

-- tags + recipe_tags: freie User-Tags
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS recipe_tags (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  tag_id    INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
  PRIMARY KEY (recipe_id, tag_id)
);

-- shopping_cart: aggregierte Zutaten. Smart-Merge passiert vor dem INSERT
-- (siehe app/recipes/cart_logic.py: gleiche canonical_name + kompatible Unit
-- → Mengen summieren statt neuer Zeile).
CREATE TABLE IF NOT EXISTS shopping_cart (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                       -- Anzeigename ("Tomaten")
  canonical_name TEXT,                      -- für Merge ("tomate")
  amount REAL,                              -- summierte Menge (in unit-Basiseinheit gespeichert)
  unit TEXT,                                -- Speicher-Einheit (g/ml/Stück/..., NICHT kg/l — siehe units.py)
  checked INTEGER NOT NULL DEFAULT 0,       -- "habe ich"-Häkchen
  added_at REAL NOT NULL,
  source_recipe_ids TEXT                    -- JSON-Array der recipe_id's die zur Menge beigetragen haben
);
CREATE INDEX IF NOT EXISTS idx_cart_canonical ON shopping_cart(canonical_name, unit);
CREATE INDEX IF NOT EXISTS idx_cart_added     ON shopping_cart(added_at DESC);
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
            self._migrate(c0)
            c0.commit()
        finally:
            c0.close()

    @staticmethod
    def _migrate(c) -> None:
        """Idempotente ALTER-Statements für Schema-Erweiterungen die nach der
        initialen DDL kamen. SQLite hat kein IF NOT EXISTS für ADD COLUMN,
        also via PRAGMA table_info() prüfen ob die Spalte fehlt."""
        cols = {r[1] for r in c.execute("PRAGMA table_info(recipes)").fetchall()}
        if "servings" not in cols:
            # Anzahl Portionen, für die das Rezept ausgelegt ist (aus Caption
            # via KI gelesen). Default NULL = unbekannt.
            c.execute("ALTER TABLE recipes ADD COLUMN servings INTEGER")

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

    def jobs_delete_failed(self) -> int:
        """Löscht ALLE Jobs mit Status='error'. Sinnvoll zum Aufräumen
        nach einer Reihe von Crashes (z.B. AI-Provider war down). Daten
        gehen nicht verloren - Jobs sind reine Log-Einträge.
        Returnt Anzahl gelöschter Zeilen."""
        with self.conn() as c:
            cur = c.execute("DELETE FROM jobs WHERE status='error'")
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

    # ════════════════════════════════════════════════════════════════════════
    # Recipes (feat/recipe-browser-and-cart)
    # ════════════════════════════════════════════════════════════════════════

    def recipe_upsert(
        self,
        *,
        url: Optional[str],
        name: str,
        type: Optional[str],
        category: Optional[str],
        folder_path: str,
        description: Optional[str],
        thumb_filename: Optional[str],
        video_filename: Optional[str],
        source_added_at: Optional[float],
    ) -> int:
        """Legt einen Recipe-Eintrag an oder aktualisiert ihn (Key: folder_path).
        Zutaten-Status wird NICHT überschrieben — ein bereits extrahiertes
        Rezept bleibt ohne erneuten KI-Lauf bestehen, auch wenn der Indexer
        es nochmal sieht (z.B. nach FS-Resync)."""
        now = time.time()
        with self.conn() as c:
            existing = c.execute(
                "SELECT id, ingredients_extracted_at, ingredients_status FROM recipes WHERE folder_path=?",
                (folder_path,),
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE recipes SET url=?, name=?, type=?, category=?, "
                    "description=?, thumb_filename=?, video_filename=?, "
                    "source_added_at=COALESCE(?, source_added_at) "
                    "WHERE id=?",
                    (url, name, type, category, description,
                     thumb_filename, video_filename, source_added_at,
                     existing["id"]),
                )
                return int(existing["id"])
            cur = c.execute(
                "INSERT INTO recipes (url, name, type, category, folder_path, "
                "description, thumb_filename, video_filename, source_added_at, "
                "indexed_at, ingredients_extracted_at, ingredients_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending')",
                (url, name, type, category, folder_path, description,
                 thumb_filename, video_filename, source_added_at, now),
            )
            return int(cur.lastrowid)

    def recipe_get(self, recipe_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
            return dict(row) if row else None

    def recipe_get_by_folder(self, folder_path: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipes WHERE folder_path=?", (folder_path,)).fetchone()
            return dict(row) if row else None

    def recipe_delete(self, recipe_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))

    def recipe_list(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        folder_prefix: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        ingredient_canonical: Optional[List[str]] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Filter-fähige Rezept-Liste. Alle Filter sind AND-verknüpft.
        - tag_ids: Rezept muss ALLE genannten Tags haben.
        - ingredient_canonical: Rezept muss ALLE genannten Zutaten haben.
        - search: matcht in name OR description (LIKE)."""
        params: List[Any] = []
        where: List[str] = []
        if type:
            where.append("r.type = ?"); params.append(type)
        if category:
            where.append("r.category = ?"); params.append(category)
        if folder_prefix:
            where.append("r.folder_path LIKE ?"); params.append(folder_prefix + "%")
        if search:
            where.append("(r.name LIKE ? OR r.description LIKE ?)")
            params.append(f"%{search}%"); params.append(f"%{search}%")
        # Tag-AND: für jedes Tag eine EXISTS-Subquery
        if tag_ids:
            for tid in tag_ids:
                where.append(
                    "EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id=r.id AND rt.tag_id=?)"
                )
                params.append(tid)
        # Ingredient-AND: für jede Zutat eine EXISTS-Subquery
        if ingredient_canonical:
            for ing in ingredient_canonical:
                where.append(
                    "EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id=r.id AND ri.canonical_name=?)"
                )
                params.append(ing)
        sql = (
            "SELECT r.* FROM recipes r"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY COALESCE(r.source_added_at, r.indexed_at) DESC"
            + " LIMIT ? OFFSET ?"
        )
        params.append(limit); params.append(offset)
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def recipe_count(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        folder_prefix: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        ingredient_canonical: Optional[List[str]] = None,
        search: Optional[str] = None,
    ) -> int:
        """Gleiche Filter wie recipe_list, liefert nur den Count."""
        params: List[Any] = []
        where: List[str] = []
        if type:
            where.append("r.type = ?"); params.append(type)
        if category:
            where.append("r.category = ?"); params.append(category)
        if folder_prefix:
            where.append("r.folder_path LIKE ?"); params.append(folder_prefix + "%")
        if search:
            where.append("(r.name LIKE ? OR r.description LIKE ?)")
            params.append(f"%{search}%"); params.append(f"%{search}%")
        if tag_ids:
            for tid in tag_ids:
                where.append(
                    "EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id=r.id AND rt.tag_id=?)"
                )
                params.append(tid)
        if ingredient_canonical:
            for ing in ingredient_canonical:
                where.append(
                    "EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id=r.id AND ri.canonical_name=?)"
                )
                params.append(ing)
        sql = "SELECT COUNT(*) AS n FROM recipes r" + (" WHERE " + " AND ".join(where) if where else "")
        with self.conn() as c:
            return int(c.execute(sql, params).fetchone()["n"])

    def recipes_pending_extraction(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipes WHERE ingredients_status='pending' "
                "AND description IS NOT NULL AND length(description) >= 20 "
                "ORDER BY COALESCE(source_added_at, indexed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recipes_extraction_stats(self) -> Dict[str, int]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT ingredients_status AS s, COUNT(*) AS n FROM recipes GROUP BY ingredients_status"
            ).fetchall()
            return {r["s"]: int(r["n"]) for r in rows}

    def recipe_set_extraction_result(
        self,
        recipe_id: int,
        status: str,
        ingredients: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Setzt status + writes ingredients ATOMISCH. Bei status='ok' werden
        alte ingredients ersetzt; bei status='error'/'skipped' nur das Flag."""
        now = time.time()
        with self.conn() as c:
            if status == "ok" and ingredients is not None:
                c.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
                for idx, ing in enumerate(ingredients):
                    c.execute(
                        "INSERT INTO recipe_ingredients (recipe_id, name, canonical_name, "
                        "amount, unit, raw, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (recipe_id, ing.get("name") or "", ing.get("canonical_name"),
                         ing.get("amount"), ing.get("unit"), ing.get("raw"), idx),
                    )
            c.execute(
                "UPDATE recipes SET ingredients_status=?, ingredients_extracted_at=? WHERE id=?",
                (status, now, recipe_id),
            )

    def recipe_ingredients_get(self, recipe_id: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order, id",
                (recipe_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Steps + Servings ─────────────────────────────────────────────────

    def recipe_steps_get(self, recipe_id: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipe_steps WHERE recipe_id=? ORDER BY step_number, id",
                (recipe_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recipe_steps_set(self, recipe_id: int, steps: List[Dict[str, Any]]) -> None:
        """Atomic: löscht alte Schritte, fügt neue ein. step_number wird beim
        Insert ignoriert und automatisch durch die Position vergeben (1-basiert),
        sodass Reordering im Frontend nur die Reihenfolge der Liste ändern muss."""
        with self.conn() as c:
            c.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,))
            for idx, s in enumerate(steps, start=1):
                instr = (s.get("instruction") or "").strip()
                if not instr:
                    continue
                timer = s.get("timer_seconds")
                if timer is not None:
                    try:
                        timer = int(timer)
                        if timer <= 0:
                            timer = None
                    except (TypeError, ValueError):
                        timer = None
                c.execute(
                    "INSERT INTO recipe_steps (recipe_id, step_number, instruction, timer_seconds) "
                    "VALUES (?, ?, ?, ?)",
                    (recipe_id, idx, instr, timer),
                )

    def recipe_set_servings(self, recipe_id: int, servings: Optional[int]) -> None:
        """servings = NULL erlaubt (= unbekannt). Wert wird beim KI-Extract
        gesetzt, kann aber vom User später überschrieben werden."""
        if servings is not None:
            try:
                servings = int(servings)
                if servings <= 0:
                    servings = None
            except (TypeError, ValueError):
                servings = None
        with self.conn() as c:
            c.execute("UPDATE recipes SET servings=? WHERE id=?", (servings, recipe_id))

    def ingredients_known(self) -> List[Dict[str, Any]]:
        """Distinct Liste aller canonical_names mit Verwendungs-Count.
        Für die Filter-UI: "Tomate (12 Rezepte)", "Knoblauch (8)"."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT canonical_name, MIN(name) AS display_name, COUNT(DISTINCT recipe_id) AS n "
                "FROM recipe_ingredients "
                "WHERE canonical_name IS NOT NULL AND canonical_name != '' "
                "GROUP BY canonical_name ORDER BY n DESC, canonical_name"
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Tags ─────────────────────────────────────────────────────────────
    def tag_get_or_create(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("tag name empty")
        with self.conn() as c:
            row = c.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = c.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            return int(cur.lastrowid)

    def tag_list(self) -> List[Dict[str, Any]]:
        """Alle Tags mit Recipe-Count."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT t.id, t.name, COUNT(rt.recipe_id) AS n "
                "FROM tags t LEFT JOIN recipe_tags rt ON rt.tag_id = t.id "
                "GROUP BY t.id ORDER BY n DESC, t.name"
            ).fetchall()
            return [dict(r) for r in rows]

    def recipe_tags_set(self, recipe_id: int, tag_names: List[str]) -> None:
        """Ersetzt alle Tags eines Rezepts. Neue Tag-Namen werden angelegt."""
        with self.conn() as c:
            c.execute("DELETE FROM recipe_tags WHERE recipe_id=?", (recipe_id,))
        for raw in tag_names:
            name = (raw or "").strip()
            if not name:
                continue
            tag_id = self.tag_get_or_create(name)
            with self.conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                    (recipe_id, tag_id),
                )

    def recipe_tags_get(self, recipe_id: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT t.id, t.name FROM tags t "
                "JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id=? "
                "ORDER BY t.name",
                (recipe_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Shopping Cart ────────────────────────────────────────────────────
    def cart_list(self) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM shopping_cart ORDER BY checked ASC, added_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def cart_find_mergeable(self, canonical_name: str, unit: Optional[str]) -> Optional[Dict[str, Any]]:
        """Sucht einen Cart-Eintrag mit gleichem canonical+unit (case unit==None matched unit==None)."""
        with self.conn() as c:
            if unit is None:
                row = c.execute(
                    "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit IS NULL",
                    (canonical_name,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit=?",
                    (canonical_name, unit),
                ).fetchone()
            return dict(row) if row else None

    def cart_add_or_merge(
        self,
        *,
        name: str,
        canonical_name: Optional[str],
        amount: Optional[float],
        unit: Optional[str],
        source_recipe_id: Optional[int],
    ) -> int:
        """Insert oder Merge basierend auf canonical_name+unit.
        Die eigentliche Einheiten-Konvertierung passiert vor diesem Call in
        cart_logic.add_to_cart() — hier wird nur summiert wenn unit gleich ist."""
        existing = self.cart_find_mergeable(canonical_name or name.lower(), unit) if canonical_name else None
        with self.conn() as c:
            if existing:
                new_amount = (existing.get("amount") or 0) + (amount or 0) if amount is not None else existing.get("amount")
                src_ids = json.loads(existing.get("source_recipe_ids") or "[]")
                if source_recipe_id and source_recipe_id not in src_ids:
                    src_ids.append(source_recipe_id)
                c.execute(
                    "UPDATE shopping_cart SET amount=?, source_recipe_ids=? WHERE id=?",
                    (new_amount, json.dumps(src_ids), existing["id"]),
                )
                return int(existing["id"])
            src_json = json.dumps([source_recipe_id] if source_recipe_id else [])
            cur = c.execute(
                "INSERT INTO shopping_cart (name, canonical_name, amount, unit, "
                "checked, added_at, source_recipe_ids) VALUES (?, ?, ?, ?, 0, ?, ?)",
                (name, canonical_name, amount, unit, time.time(), src_json),
            )
            return int(cur.lastrowid)

    def cart_update(self, item_id: int, *, amount: Optional[float] = None,
                    checked: Optional[bool] = None, name: Optional[str] = None) -> None:
        sets, params = [], []
        if amount is not None:
            sets.append("amount=?"); params.append(amount)
        if checked is not None:
            sets.append("checked=?"); params.append(1 if checked else 0)
        if name is not None:
            sets.append("name=?"); params.append(name)
        if not sets:
            return
        params.append(item_id)
        with self.conn() as c:
            c.execute(f"UPDATE shopping_cart SET {', '.join(sets)} WHERE id=?", params)

    def cart_delete(self, item_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM shopping_cart WHERE id=?", (item_id,))

    def cart_clear(self, *, only_checked: bool = False) -> int:
        with self.conn() as c:
            if only_checked:
                cur = c.execute("DELETE FROM shopping_cart WHERE checked=1")
            else:
                cur = c.execute("DELETE FROM shopping_cart")
            return cur.rowcount


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
