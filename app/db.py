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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

-- Persistente Queue für kurze, vom Web ausgelöste Hintergrundaufgaben.
-- Anders als lose Daemon-Threads überlebt ein queued Task einen Neustart.
CREATE TABLE IF NOT EXISTS background_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  dedupe_key TEXT,
  status TEXT NOT NULL DEFAULT 'queued', -- queued|running|ok|error
  created_at REAL NOT NULL,
  started_at REAL,
  ended_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_background_tasks_queue
  ON background_tasks(status, created_at);
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
  ingredients_status TEXT DEFAULT 'pending',-- pending | running | ok | error | skipped
  extraction_claimed_at REAL,
  extraction_claim_owner TEXT
);
CREATE INDEX IF NOT EXISTS idx_recipes_type     ON recipes(type, category);
CREATE INDEX IF NOT EXISTS idx_recipes_added    ON recipes(source_added_at DESC);
CREATE INDEX IF NOT EXISTS idx_recipes_extract  ON recipes(ingredients_status, ingredients_extracted_at);
-- idx_recipes_deleted wird in _migrate erstellt NACHDEM die deleted_at-Spalte
-- via ALTER COLUMN hinzugefügt ist (DDL läuft auf bestehender DB sonst vor Migration).

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

-- Wiederkehrende Einkäufe gehören zur selben lokalen Einkaufsliste. Beim
-- Fälligwerden wird die Regel atomar in shopping_cart gemerged und auf den
-- nächsten Termin weitergeschoben.
CREATE TABLE IF NOT EXISTS shopping_recurring (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  canonical_name TEXT,
  amount REAL,
  unit TEXT,
  category TEXT,
  interval_days INTEGER NOT NULL CHECK(interval_days BETWEEN 1 AND 3650),
  next_due_on TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  last_added_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recurring_due
  ON shopping_recurring(active, next_due_on);

-- shopping_exclusions: globale Zutaten, die beim Kochen nicht auf die
-- Einkaufsliste übernommen werden sollen (z.B. Salz, Wasser, Pfeffer).
CREATE TABLE IF NOT EXISTS shopping_exclusions (
  canonical_name TEXT PRIMARY KEY COLLATE NOCASE,
  created_at REAL NOT NULL
);

-- meal_plan_entries: Wochenplan ohne separate Wochen-Tabelle. Das ISO-Datum
-- reicht für Navigation und erlaubt mehrere Rezepte pro Tag.
CREATE TABLE IF NOT EXISTS meal_plan_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  planned_for TEXT NOT NULL,                -- YYYY-MM-DD
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  planned_servings INTEGER NOT NULL DEFAULT 2,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(planned_for, recipe_id)
);
CREATE INDEX IF NOT EXISTS idx_meal_plan_date
  ON meal_plan_entries(planned_for, sort_order, id);

-- users: Multi-User-Auth. Bcrypt-Hashes in password_hash. Die role-Spalte
-- bleibt nur für Abwärtskompatibilität; Berechtigungen werden nicht danach
-- unterschieden. disabled=1 → kein Login mehr,
-- Datensatz bleibt für Audit-Trail.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',           -- Legacy, nicht mehr ausgewertet
  disabled INTEGER NOT NULL DEFAULT 0,
  session_version INTEGER NOT NULL DEFAULT 0,  -- erhöht bei Passwort/Sperr-Änderungen
  created_at REAL NOT NULL,
  last_login_at REAL                           -- NULL bis 1. Login
);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(username);

-- sync_errors: FS-Sync-Konflikte (UNIQUE constraint failed, etc.)
-- Werden vom Audit-Tab als 'FS-Konflikte'-Findings angezeigt — User entscheidet
-- welcher der konkurrierenden Folder behalten/gelöscht wird.
CREATE TABLE IF NOT EXISTS sync_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  folder_path TEXT NOT NULL UNIQUE,         -- Pfad der den Crash verursacht hat
  error_type TEXT NOT NULL,                 -- 'unique_url' | 'unique_folder' | 'other'
  error_msg TEXT,                           -- Original exception text
  conflict_with_id INTEGER,                 -- recipes.id des bestehenden Eintrags (bei unique_url)
  detected_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_errors_type ON sync_errors(error_type);

-- audit_ai_findings: KI-Sanity-Check-Findings (Pfad/Name/Description-Konsistenz)
-- Persistent zwischen Audit-Runs, damit User Aktionen ausführen kann ohne dass
-- der nächste Run erst wieder alle KI-Calls macht.
CREATE TABLE IF NOT EXISTS audit_ai_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  finding_type TEXT NOT NULL,               -- 'category_mismatch' | 'name_mismatch'
  current_value TEXT,                       -- z.B. 'Hauptgericht/Spargel'
  suggested_value TEXT,                     -- z.B. 'Frühstück/Bowls'
  reason TEXT,                              -- KI-Begründung, kurz
  resolved INTEGER NOT NULL DEFAULT 0,      -- 0 = offen, 1 = ignoriert oder angewendet
  created_at REAL NOT NULL,
  UNIQUE(recipe_id, finding_type)           -- pro Rezept+Typ nur ein Finding
);
CREATE INDEX IF NOT EXISTS idx_aaf_recipe ON audit_ai_findings(recipe_id);
CREATE INDEX IF NOT EXISTS idx_aaf_open   ON audit_ai_findings(resolved, finding_type);

-- recipe_versions: unveränderliche Snapshots vor relevanten Änderungen.
-- Der Snapshot enthält Rezept-Metadaten, Zutaten, Schritte und Tags als JSON.
CREATE TABLE IF NOT EXISTS recipe_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  version_no INTEGER NOT NULL,
  created_at REAL NOT NULL,
  created_by TEXT,
  source TEXT NOT NULL DEFAULT 'user',
  reason TEXT,
  snapshot_json TEXT NOT NULL,
  UNIQUE(recipe_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_recipe_versions_recipe
  ON recipe_versions(recipe_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_recipe_versions_created
  ON recipe_versions(created_at DESC);

-- search_synonyms: administrierbare Suchbegriffe. Ein Eintrag bildet eine
-- Gruppe gleichwertiger Begriffe ab, z.B. Tomate -> [Tomaten, Paradeiser].
CREATE TABLE IF NOT EXISTS search_synonyms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL UNIQUE COLLATE NOCASE,
  synonyms_json TEXT NOT NULL DEFAULT '[]',
  updated_at REAL NOT NULL,
  updated_by TEXT
);

-- maintenance_runs: nachvollziehbare Admin-Wartungsläufe.
CREATE TABLE IF NOT EXISTS maintenance_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  started_at REAL NOT NULL,
  ended_at REAL,
  status TEXT NOT NULL DEFAULT 'running',
  result_json TEXT,
  started_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_maintenance_runs_created
  ON maintenance_runs(started_at DESC);

-- schema_migrations: kleine, nachvollziehbare interne Migrationen ohne
-- externes Tool. Bestehende idempotente ALTERs bleiben kompatibel.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at REAL NOT NULL
);

-- recipes_fts: SQLite-FTS5-Volltextindex für recipes.{name, description, type, category}.
-- 'unicode61 remove_diacritics 2' = Umlauten-/Akzent-Folding (Tomaten matcht Tomáten),
-- 'content=recipes' = contentless FTS (kein doppelter Speicher, recipes ist Source of Truth).
-- Trigger unten halten den Index aktuell.
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
  name, description, type, category,
  content='recipes', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS recipes_fts_ai AFTER INSERT ON recipes BEGIN
  INSERT INTO recipes_fts(rowid, name, description, type, category)
  VALUES (new.id, new.name, COALESCE(new.description, ''),
          COALESCE(new.type, ''), COALESCE(new.category, ''));
END;
CREATE TRIGGER IF NOT EXISTS recipes_fts_ad AFTER DELETE ON recipes BEGIN
  INSERT INTO recipes_fts(recipes_fts, rowid, name, description, type, category)
  VALUES ('delete', old.id, old.name, COALESCE(old.description, ''),
          COALESCE(old.type, ''), COALESCE(old.category, ''));
END;
CREATE TRIGGER IF NOT EXISTS recipes_fts_au
AFTER UPDATE OF name, description, type, category ON recipes BEGIN
  INSERT INTO recipes_fts(recipes_fts, rowid, name, description, type, category)
  VALUES ('delete', old.id, old.name, COALESCE(old.description, ''),
          COALESCE(old.type, ''), COALESCE(old.category, ''));
  INSERT INTO recipes_fts(rowid, name, description, type, category)
  VALUES (new.id, new.name, COALESCE(new.description, ''),
          COALESCE(new.type, ''), COALESCE(new.category, ''));
END;
"""


def _build_fts_query(q: str) -> Optional[str]:
    """User-Input → FTS5-MATCH-Syntax. Multi-Word wird AND-verknüpft, jedes
    Token bekommt prefix-* damit Wortanfänge matchen.

      'pasta cheese' → '"pasta"* AND "cheese"*'
      'tomáten'      → '"tomáten"*'  (FTS5 entfernt Diakritika beim Tokenize)
      'a'            → None  (Single-Char zu kurz, sonst Garbage-Matches)
      ''             → None

    Quoting der Tokens schützt vor FTS5-Syntax-Special-Chars (- + * etc.).
    Tokens < 2 Chars werden skipped (FTS5 würde meckern bzw. zu viele Treffer)."""
    if not q:
        return None
    import re
    # Special FTS5-Chars + Punkte/Klammern raus, Underscore/Bindestrich als Spacer
    cleaned = re.sub(r'[^\w\s\u00C0-\u017F-]+', ' ', q, flags=re.UNICODE)
    cleaned = cleaned.replace('-', ' ').replace('_', ' ')
    tokens = [t for t in cleaned.split() if len(t) >= 2]
    if not tokens:
        return None
    # Maximal 8 Tokens (Performance + AND wird sonst sehr restriktiv)
    tokens = tokens[:8]
    return ' AND '.join(f'"{t}"*' for t in tokens)


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Pragmas einmalig auf einer separaten Verbindung setzen (WAL bleibt erhalten).
        c0 = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        try:
            c0.execute("PRAGMA journal_mode=WAL")
            c0.execute("PRAGMA synchronous=FULL")
            c0.execute("PRAGMA wal_autocheckpoint=100")
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
        # Nährwerte pro Portion (von analyzer.compute_nutrition geschrieben).
        # Alle NULL = noch nicht berechnet. Einmalig beim ersten Extract,
        # On-Demand via Detail-Modal-Button neu rechenbar.
        for col, sqltype in (
            ("calories_per_serving", "INTEGER"),
            ("protein_g", "REAL"),
            ("carbs_g", "REAL"),
            ("fat_g", "REAL"),
            ("nutrition_computed_at", "REAL"),
            # User-Verifikations-Flag: 1 = vom User manuell als 'ok' geprüft.
            # Verifizierte Rezepte werden aus den Daten-Lücken-Detections
            # ausgeschlossen — User-Override über die KI-Heuristik.
            ("user_verified", "INTEGER NOT NULL DEFAULT 0"),
            ("verified_at", "REAL"),
            ("verified_by", "TEXT"),
            # Soft-Delete: Unix-Timestamp wann das Rezept in den Papierkorb
            # verschoben wurde. NULL = aktiv. NOT NULL = im Papierkorb.
            # Nach 30 Tagen wird durch Background-Job endgültig gelöscht
            # (inkl. Files). User kann via Restore wiederherstellen.
            ("deleted_at", "REAL"),
            # Wurde der Folder beim Soft-Delete schon entfernt? Wenn ja,
            # kann Restore die Files nicht wiederherstellen. Default 0.
            ("files_deleted", "INTEGER NOT NULL DEFAULT 0"),
            # Favorit (User-Stern). 0 = unmarkiert, 1 = Favorit.
            ("is_favorite", "INTEGER NOT NULL DEFAULT 0"),
            # Bewertung 1-5 Sterne (0 = unbewertet). Persönlich pro Rezept.
            ("rating", "INTEGER NOT NULL DEFAULT 0"),
            # Worker-Lease: verhindert, dass parallele Worker dasselbe Rezept
            # gleichzeitig extrahieren. Verwaiste Claims werden nach Ablauf
            # der Lease automatisch wieder auf pending gesetzt.
            ("extraction_claimed_at", "REAL"),
            ("extraction_claim_owner", "TEXT"),
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE recipes ADD COLUMN {col} {sqltype}")

        # Soft-Delete-Index NACH der Spalten-Migration (Index braucht die Spalte).
        c.execute("CREATE INDEX IF NOT EXISTS idx_recipes_deleted ON recipes(deleted_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_recipes_favorite ON recipes(is_favorite) WHERE is_favorite=1")
        c.execute("CREATE INDEX IF NOT EXISTS idx_recipes_rating ON recipes(rating) WHERE rating>0")

        rt_cols = {r[1] for r in c.execute("PRAGMA table_info(recipe_tags)").fetchall()}
        if "auto" not in rt_cols:
            # 0 = User-Tag (manuell gesetzt), 1 = Auto-Tag (vom KI/Regel-Pass).
            # Beim Re-Extract werden NUR Tags mit auto=1 ersetzt, User-Tags bleiben.
            c.execute("ALTER TABLE recipe_tags ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")

        ri_cols = {r[1] for r in c.execute("PRAGMA table_info(recipe_ingredients)").fetchall()}
        if "calories" not in ri_cols:
            # Geschätzte Gesamt-kcal für die genannte Menge dieser Zutat (KI,
            # ~). NULL = noch nicht berechnet. Wird beim Nährwert-Lauf befüllt.
            c.execute("ALTER TABLE recipe_ingredients ADD COLUMN calories REAL")

        df_cols = {r[1] for r in c.execute("PRAGMA table_info(download_failures)").fetchall()}
        if "content_type" not in df_cols:
            # recipe|wedding. Nötig seit Mails nach Verarbeitung gelöscht werden:
            # Retries kommen aus dieser Tabelle, der Typ muss überleben.
            c.execute("ALTER TABLE download_failures ADD COLUMN content_type TEXT NOT NULL DEFAULT 'recipe'")

        user_cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "session_version" not in user_cols:
            # Bestehende signierte Cookies enthalten keine Version und werden
            # nach dem Upgrade bewusst einmalig ungültig. Ab dann invalidieren
            # Passwortwechsel und Aktivstatusänderungen alle alten Sessions.
            c.execute(
                "ALTER TABLE users ADD COLUMN session_version "
                "INTEGER NOT NULL DEFAULT 0"
            )

        task_cols = {
            r[1] for r in c.execute("PRAGMA table_info(background_tasks)").fetchall()
        }
        if "dedupe_key" not in task_cols:
            c.execute("ALTER TABLE background_tasks ADD COLUMN dedupe_key TEXT")
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_background_tasks_active_dedupe "
            "ON background_tasks(kind, dedupe_key) "
            "WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')"
        )

        # Soft-Delete-Audit: gelöschte/quarantänierte Einträge (Härtung gegen
        # Datenverlust — Ordner landet in Quarantäne, hier bleibt die Herkunft).
        c.execute(
            "CREATE TABLE IF NOT EXISTS deleted_history ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  url TEXT, deleted_at REAL NOT NULL, content_type TEXT,"
            "  name TEXT, target_dir TEXT, quarantine_path TEXT,"
            "  reason TEXT, metadata TEXT)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_deleted_history_at ON deleted_history(deleted_at DESC)")

        # Bestehende Installationen haben evtl. noch den alten Trigger ohne
        # OF-Spaltenliste; selbst Flag-Updates re-tokenisierten dort das ganze
        # Rezept. Definition idempotent auf die schmale Variante heben.
        c.execute("DROP TRIGGER IF EXISTS recipes_fts_au")
        c.execute("""
            CREATE TRIGGER recipes_fts_au
            AFTER UPDATE OF name, description, type, category ON recipes BEGIN
              INSERT INTO recipes_fts(recipes_fts, rowid, name, description, type, category)
              VALUES ('delete', old.id, old.name, COALESCE(old.description, ''),
                      COALESCE(old.type, ''), COALESCE(old.category, ''));
              INSERT INTO recipes_fts(rowid, name, description, type, category)
              VALUES (new.id, new.name, COALESCE(new.description, ''),
                      COALESCE(new.type, ''), COALESCE(new.category, ''));
            END
        """)

        # Bei external-content FTS liefert SELECT COUNT(*) aus recipes_fts die
        # Zeilenzahl der Content-Tabelle, selbst wenn der Index leer ist. Die
        # docsize-Schattentabelle bildet dagegen wirklich indizierte Dokumente
        # ab. Bei jeder Abweichung ist ein vollständiger rebuild sicherer als
        # ein partieller Insert.
        try:
            fts_count = int(c.execute("SELECT COUNT(*) FROM recipes_fts_docsize").fetchone()[0])
            rec_count = int(c.execute("SELECT COUNT(*) FROM recipes").fetchone()[0])
            if rec_count != fts_count:
                c.execute("INSERT INTO recipes_fts(recipes_fts) VALUES('rebuild')")
        except Exception as e:
            # Bei sehr alter SQLite ohne FTS5 — nicht fatal, search-Endpoint
            # fällt dann auf den LIKE-Fallback (siehe recipe_list)
            import logging
            logging.getLogger(__name__).warning(f"FTS5-Backfill skipped: {e}")

        # FTS5-Integrity-Check + Auto-Rebuild bei Korruption.
        # Nach Crash/SIGKILL kann der FTS-Index zwischen recipes-Triggern und
        # FTS-Schreibvorgängen inkonsistent werden — späteres recipes-UPDATE
        # crashed dann mit 'database disk image is malformed' obwohl die
        # main-DB ok ist. Bei jedem Start einmal prüfen: bei Fehler komplett
        # rebuilden. Kosten: einmalig ~50ms bei 100 Rezepten.
        import logging as _log
        _logger = _log.getLogger(__name__)
        try:
            # rank=1 vergleicht bei external-content Tabellen zusätzlich den
            # Indexinhalt mit recipes; ohne rank prüft FTS nur seine Interna.
            c.execute(
                "INSERT INTO recipes_fts(recipes_fts, rank) VALUES('integrity-check', 1)"
            )
        except Exception as e:
            _logger.warning(
                f"FTS-Integrity-Check failed ({type(e).__name__}: {e}) — "
                f"rebuilding Index automatisch"
            )
            try:
                c.execute("INSERT INTO recipes_fts(recipes_fts) VALUES('rebuild')")
                _logger.info("FTS-Index erfolgreich rebuilt")
            except Exception as e2:
                _logger.error(f"FTS-Rebuild failed: {e2} — Volltext-Suche evtl kaputt")

        # Feature-Migrations werden zusätzlich protokolliert. Die eigentlichen
        # DDL-Schritte bleiben idempotent, damit auch sehr alte Installationen
        # sicher direkt auf den aktuellen Stand springen können.
        c.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (120, "admin_center_versions_search_pdf", time.time()),
        )

        # Fresh tomato varieties are interchangeable on the shopping list.
        # Keep processed tomato products (tomato paste, canned/passata) separate.
        tomato_migration = c.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (130,),
        ).fetchone()
        if tomato_migration is None:
            from .recipes.canonical import (
                TOMATO_CANONICAL,
                TOMATO_CANONICAL_ALIASES,
                TOMATO_SHOPPING_NAME,
            )

            tomato_aliases = tuple(sorted(TOMATO_CANONICAL_ALIASES))
            alias_slots = ",".join("?" for _ in tomato_aliases)

            c.execute(
                f"UPDATE recipe_ingredients SET canonical_name=? "
                f"WHERE lower(trim(COALESCE(canonical_name, name))) IN ({alias_slots})",
                (TOMATO_CANONICAL, *tomato_aliases),
            )

            cart_rows = c.execute(
                f"SELECT id, name, canonical_name, amount, unit, checked, "
                f"added_at, source_recipe_ids FROM shopping_cart "
                f"WHERE lower(trim(COALESCE(canonical_name, name))) IN ({alias_slots}) "
                f"ORDER BY id",
                tomato_aliases,
            ).fetchall()
            rows_by_unit: dict[Optional[str], list[tuple]] = {}
            for row in cart_rows:
                rows_by_unit.setdefault(row[4], []).append(row)

            for rows in rows_by_unit.values():
                target = next(
                    (
                        row for row in rows
                        if str(row[2] or "").strip().lower() == TOMATO_CANONICAL
                    ),
                    rows[0],
                )
                amounts = [row[3] for row in rows if row[3] is not None]
                merged_amount = sum(float(amount) for amount in amounts) if amounts else None
                merged_sources: list = []
                for row in rows:
                    try:
                        source_ids = json.loads(row[7] or "[]")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        source_ids = []
                    if isinstance(source_ids, list):
                        for source_id in source_ids:
                            if source_id not in merged_sources:
                                merged_sources.append(source_id)

                c.execute(
                    "UPDATE shopping_cart SET name=?, canonical_name=?, amount=?, checked=?, "
                    "added_at=?, source_recipe_ids=? WHERE id=?",
                    (
                        TOMATO_SHOPPING_NAME,
                        TOMATO_CANONICAL,
                        merged_amount,
                        1 if all(bool(row[5]) for row in rows) else 0,
                        max(float(row[6]) for row in rows),
                        json.dumps(merged_sources),
                        target[0],
                    ),
                )
                duplicate_ids = [row[0] for row in rows if row[0] != target[0]]
                if duplicate_ids:
                    duplicate_slots = ",".join("?" for _ in duplicate_ids)
                    c.execute(
                        f"DELETE FROM shopping_cart WHERE id IN ({duplicate_slots})",
                        duplicate_ids,
                    )

            excluded_rows = c.execute(
                f"SELECT created_at FROM shopping_exclusions "
                f"WHERE lower(trim(canonical_name)) IN ({alias_slots})",
                tomato_aliases,
            ).fetchall()
            if excluded_rows:
                earliest = min(float(row[0]) for row in excluded_rows)
                c.execute(
                    "INSERT OR IGNORE INTO shopping_exclusions "
                    "(canonical_name, created_at) VALUES (?, ?)",
                    (TOMATO_CANONICAL, earliest),
                )
                c.execute(
                    "UPDATE shopping_exclusions SET created_at=min(created_at, ?) "
                    "WHERE canonical_name=?",
                    (earliest, TOMATO_CANONICAL),
                )
                c.execute(
                    f"DELETE FROM shopping_exclusions "
                    f"WHERE lower(trim(canonical_name)) IN ({alias_slots}) "
                    f"AND lower(trim(canonical_name))<>?",
                    (*tomato_aliases, TOMATO_CANONICAL),
                )

            c.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (130, "merge_fresh_tomato_variants", time.time()),
            )

        c.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (140, "weekly_meal_plan", time.time()),
        )

        if int(c.execute("SELECT COUNT(*) FROM search_synonyms").fetchone()[0]) == 0:
            defaults = {
                "Hackfleisch": ["Hack", "Gehacktes", "Faschiertes"],
                "Kartoffel": ["Kartoffeln", "Erdapfel", "Erdäpfel"],
                "Tomate": ["Tomaten", "Paradeiser"],
                "Sahne": ["Rahm", "Schlagobers"],
                "Frühlingszwiebel": ["Lauchzwiebel", "Bundzwiebel"],
            }
            now = time.time()
            for term, synonyms in defaults.items():
                c.execute(
                    "INSERT OR IGNORE INTO search_synonyms(term, synonyms_json, updated_at, updated_by) "
                    "VALUES (?, ?, ?, 'system')",
                    (term, json.dumps(synonyms, ensure_ascii=False), now),
                )

    @contextmanager
    def conn(self):
        # SQLite: pro-Aufruf Verbindung, dank check_same_thread=False thread-safe genug.
        # journal_mode=WAL ist file-persistent (einmalig in __init__).
        # busy_timeout=10s per-Connection: SQLite-internes Polling wenn ein anderer
        # Writer die DB locked — wichtig bei 3× parallelen Worker-Threads. Ohne das
        # bekommt der Caller sofort 'database is locked' (SQLITE_BUSY).
        # synchronous=FULL: maximale Crash-/Stromausfall-Sicherheit (etwas langsamere
        # Writes, bei diesem Write-Volumen vernachlässigbar). wal_autocheckpoint=100:
        # WAL wird häufiger in die Haupt-DB übernommen → kleineres Verlustfenster.
        c = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA wal_autocheckpoint=100")
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
    def download_failure_record(self, url: str, error: str,
                                content_type: str = "recipe") -> int:
        """Zählt einen Download-Fehlversuch. Returnt die neue Versuchszahl."""
        now = time.time()
        with self.conn() as c:
            c.execute(
                "INSERT INTO download_failures (url, first_seen, last_try, attempts, last_error, content_type) "
                "VALUES (?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "  last_try=excluded.last_try, "
                "  attempts=attempts + 1, "
                "  last_error=excluded.last_error",
                (url, now, now, (error or "")[:500], content_type or "recipe"),
            )
            row = c.execute(
                "SELECT attempts FROM download_failures WHERE url=?", (url,)
            ).fetchone()
            return int(row["attempts"]) if row else 1

    def download_failure_reset(self, url: str) -> None:
        """Setzt den Versuchszähler zurück, behält die Zeile. Der nächste
        Scraper-Lauf nimmt die URL als Retry-Kandidat wieder auf — die
        Quell-Mail ist nach Auto-Delete nicht mehr nötig."""
        with self.conn() as c:
            c.execute(
                "UPDATE download_failures SET attempts=0, last_error='(retry angefordert)' WHERE url=?",
                (url,),
            )

    def download_failures_retry_candidates(self, max_attempts: int) -> List[Dict[str, Any]]:
        """URLs mit attempts < max — werden vom Scraper-Lauf erneut versucht.
        Ersetzt das frühere Re-Lesen aus der Mail (Mails werden gelöscht)."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT url, content_type, attempts FROM download_failures "
                "WHERE attempts < ? ORDER BY last_try ASC LIMIT 50",
                (max_attempts,),
            ).fetchall()
            return [dict(r) for r in rows]

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
        am unbegrenzten Wachsen (bei OnCalendar=*:0/30 = 17.500/Jahr).
        Plus: opportunistischer WAL-Checkpoint danach — bei langlaufendem
        Prozess kann die -wal-Datei sonst stetig wachsen."""
        cutoff = time.time() - days * 86400
        with self.conn() as c:
            cur = c.execute(
                "DELETE FROM jobs WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            )
            n = cur.rowcount or 0
        if n > 0:
            try:
                with self.conn() as c:
                    c.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
        return n

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
        Hindert die Pending-Liste am Vollstopfen mit toten Items. Die URL wird
        in derselben Transaktion in ``history`` vermerkt: ein späterer
        Mail-Lauf darf sie sonst erneut importieren und die Quellmail trotz
        fehlender Nutzerentscheidung löschen.
        """
        cutoff = time.time() - days * 86400
        now = time.time()
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT url, content_type, ai_suggestion FROM pending "
                "WHERE status='pending' AND created_at < ?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                name = ""
                try:
                    suggestion = json.loads(row["ai_suggestion"] or "{}")
                    if isinstance(suggestion, dict):
                        name = str(suggestion.get("name") or "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                c.execute(
                    "INSERT OR IGNORE INTO history "
                    "(url, processed_at, content_type, name, target_dir) "
                    "VALUES (?, ?, ?, ?, '')",
                    (row["url"], now, row["content_type"] or "", name),
                )
            if rows:
                c.executemany(
                    "UPDATE pending SET status='auto_skipped' "
                    "WHERE url=? AND status='pending'",
                    ((row["url"],) for row in rows),
                )
            return len(rows)

    # ---------------- Persistente Background-Tasks ----------------
    def background_task_enqueue(
        self,
        kind: str,
        payload: Dict[str, Any],
        *,
        dedupe_key: Optional[str] = None,
    ) -> int:
        """Reiht einen Task ein oder liefert den gleichartigen aktiven Task.

        ``BEGIN IMMEDIATE`` plus partieller Unique-Index verhindert, dass zwei
        Prozesse dieselbe Share-URL gleichzeitig als queued/running anlegen.
        Nach einem terminalen Status darf bewusst ein neuer Versuch entstehen.
        """
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if dedupe_key:
                existing = c.execute(
                    "SELECT id FROM background_tasks "
                    "WHERE kind=? AND dedupe_key=? "
                    "AND status IN ('queued', 'running') "
                    "ORDER BY id LIMIT 1",
                    (kind, dedupe_key),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = c.execute(
                "INSERT INTO background_tasks("
                "kind, payload_json, dedupe_key, status, created_at"
                ") VALUES (?, ?, ?, 'queued', ?)",
                (
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    dedupe_key,
                    time.time(),
                ),
            )
            return int(cur.lastrowid)

    def background_task_claim_next(self) -> Optional[Dict[str, Any]]:
        """Claimt atomar den ältesten queued Task für genau einen Worker."""
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM background_tasks WHERE status='queued' "
                "ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            now = time.time()
            updated = c.execute(
                "UPDATE background_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='queued'",
                (now, row["id"]),
            ).rowcount
            if not updated:
                return None
            task = dict(row)
            task["status"] = "running"
            task["started_at"] = now
            task["attempts"] = int(task.get("attempts") or 0) + 1
        try:
            task["payload"] = json.loads(task.pop("payload_json") or "{}")
        except Exception:
            task["payload"] = {}
        return task

    def background_task_finish(
        self, task_id: int, *, ok: bool, result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE background_tasks SET status=?, ended_at=?, result_json=?, error=? WHERE id=?",
                (
                    "ok" if ok else "error",
                    time.time(),
                    json.dumps(result or {}, ensure_ascii=False, default=str),
                    error,
                    int(task_id),
                ),
            )

    def background_task_get(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM background_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return None
        task = dict(row)
        for source, target in (("payload_json", "payload"), ("result_json", "result")):
            try:
                task[target] = json.loads(task.pop(source) or "{}")
            except Exception:
                task[target] = {}
        return task

    def background_task_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM background_tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [self._decode_background_task(dict(row)) for row in rows]

    @staticmethod
    def _decode_background_task(task: Dict[str, Any]) -> Dict[str, Any]:
        for source, target in (("payload_json", "payload"), ("result_json", "result")):
            try:
                task[target] = json.loads(task.pop(source) or "{}")
            except Exception:
                task[target] = {}
        return task

    def background_tasks_recover(self, *, max_attempts: int = 3) -> int:
        """Nach Neustart laufende Tasks erneut einreihen; Endlosschleifen begrenzen."""
        with self.conn() as c:
            retry = c.execute(
                "UPDATE background_tasks SET status='queued', started_at=NULL, error=NULL "
                "WHERE status='running' AND attempts < ?",
                (max_attempts,),
            ).rowcount or 0
            c.execute(
                "UPDATE background_tasks SET status='error', ended_at=?, "
                "error=COALESCE(error, 'Zu viele Neustartversuche') "
                "WHERE status='running' AND attempts >= ?",
                (time.time(), max_attempts),
            )
        return int(retry)

    def deleted_history_add(self, entry: Dict[str, Any], *, quarantine_path: str = "",
                            reason: str = "manual_delete", metadata: Optional[Dict[str, Any]] = None) -> int:
        """Audit-Log für Soft-Deletes. Der eigentliche Ordner wird in Quarantäne
        verschoben, dieser Eintrag bewahrt URL/Name/Zielpfad für spätere Suche."""
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO deleted_history "
                "(url, deleted_at, content_type, name, target_dir, quarantine_path, reason, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.get("url"), time.time(), entry.get("content_type"),
                    entry.get("name"), entry.get("target_dir"), quarantine_path, reason,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def deleted_history_list(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM deleted_history ORDER BY deleted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["metadata"] = json.loads(d.get("metadata") or "{}")
                except Exception:
                    d["metadata"] = {}
                out.append(d)
            return out

    def deleted_history_latest(
        self,
        target_dir: str,
        *,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM deleted_history WHERE target_dir=?"
        params: List[Any] = [target_dir]
        if reason:
            sql += " AND reason=?"
            params.append(reason)
        sql += " ORDER BY deleted_at DESC, id DESC LIMIT 1"
        with self.conn() as c:
            row = c.execute(sql, params).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["metadata"] = json.loads(result.get("metadata") or "{}")
        except Exception:
            result["metadata"] = {}
        return result

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

        # Beide temporären Dateien liegen im Zielverzeichnis. Damit ist das
        # abschließende os.replace auch über Mount-Grenzen hinweg atomar.
        nonce = f"{os.getpid()}.{time.time_ns()}"
        tmp_db = dest.parent / f".tmp-{dest.name}.{nonce}.db"
        tmp_out = dest.parent / f".tmp-{dest.name}.{nonce}.out"
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
                        raise RuntimeError(f"integrity_check failed: {row}")
                finally:
                    check.close()

            # Schritt 3: Komprimieren und das geschlossene Archiv einmal
            # vollständig lesen (CRC/Truncation), bevor das alte Ziel ersetzt
            # wird. So bleibt bei Fehlern das letzte gute Backup erhalten.
            if compress:
                with open(tmp_db, "rb") as fin, open(tmp_out, "wb") as raw:
                    with gzip.GzipFile(
                        filename=dest.name,
                        mode="wb",
                        fileobj=raw,
                        compresslevel=6,
                        mtime=0,
                    ) as fout:
                        shutil.copyfileobj(fin, fout)
                    raw.flush()
                    os.fsync(raw.fileno())
                with gzip.open(tmp_out, "rb") as check_gzip:
                    while check_gzip.read(1024 * 1024):
                        pass
                os.replace(tmp_out, dest)
                tmp_db.unlink(missing_ok=True)
            else:
                with open(tmp_db, "rb") as raw:
                    os.fsync(raw.fileno())
                os.replace(tmp_db, dest)

            return {
                "ok": True,
                "dest": str(dest),
                "size_bytes": dest.stat().st_size,
                "compressed": bool(compress),
                "verified": verified,
            }
        except Exception as e:
            for temporary in (tmp_db, tmp_out):
                try:
                    temporary.unlink(missing_ok=True)
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
        preserve_existing: Iterable[str] = (),
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
                preserve = set(preserve_existing)
                # Wenn der vorherige Sweep das Rezept auf 'skipped' gesetzt hat
                # (weil keine description gefunden wurde) und JETZT eine
                # description da ist (z.B. Fallback-Read findet caption.txt),
                # Status zurück auf 'pending' damit der Worker neu extrahiert.
                # User-Edits (status='ok' mit manuell gepflegten Zutaten) NICHT
                # antasten — nur skipped reaktivieren.
                reset_status = (
                    description and len(description.strip()) >= 20
                    and existing["ingredients_status"] == "skipped"
                )
                assignments = []
                update_params: List[Any] = []
                for field, value in (
                    ("url", url), ("name", name), ("type", type),
                    ("category", category), ("description", description),
                    ("thumb_filename", thumb_filename),
                    ("video_filename", video_filename),
                ):
                    if field in preserve:
                        continue
                    assignments.append(f"{field}=?")
                    update_params.append(value)
                sql = ("UPDATE recipes SET " + ", ".join(assignments) + ", "
                       "source_added_at=COALESCE(?, source_added_at)"
                       + (
                           ", ingredients_status='pending', "
                           "ingredients_extracted_at=NULL, extraction_claimed_at=NULL, "
                           "extraction_claim_owner=NULL"
                           if reset_status else ""
                       )
                       + " WHERE id=?")
                c.execute(sql, (*update_params, source_added_at, existing["id"]))
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
        """Endgültig aus DB löschen (HARD-DELETE). Nur für Cleanup-Job
        oder explizit-purge aus dem Papierkorb. Normales DELETE soll
        via recipe_soft_delete laufen, nicht hier."""
        with self.conn() as c:
            c.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))

    def recipe_soft_delete(self, recipe_id: int, files_deleted: bool = False) -> None:
        """Markiert Rezept als gelöscht (deleted_at = now). Wird in Listings
        gefiltert und in Trash-View sichtbar. files_deleted=True heißt der
        FS-Folder wurde zusätzlich entfernt → Restore kann Files nicht
        wiederherstellen, nur DB-Eintrag."""
        import time
        with self.conn() as c:
            c.execute(
                "UPDATE recipes SET deleted_at=?, files_deleted=? WHERE id=?",
                (time.time(), 1 if files_deleted else 0, recipe_id),
            )

    def recipe_restore(
        self,
        recipe_id: int,
        *,
        files_restored: bool = False,
    ) -> Dict[str, Any]:
        """Aus Papierkorb wiederherstellen (deleted_at = NULL).
        Ein Rezept mit quarantänierten Dateien darf erst nach erfolgreichem
        Filesystem-Restore aktiviert werden."""
        with self.conn() as c:
            row = c.execute(
                "SELECT folder_path, files_deleted, deleted_at "
                "FROM recipes WHERE id=?",
                (recipe_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "Rezept nicht gefunden"}
            if row["deleted_at"] is None:
                return {
                    "ok": True,
                    "folder_path": row["folder_path"],
                    "files_deleted": bool(row["files_deleted"]),
                    "already_active": True,
                }
            if row["files_deleted"] and not files_restored:
                return {
                    "ok": False,
                    "error": "Rezeptdateien müssen zuerst aus der Quarantäne wiederhergestellt werden",
                    "folder_path": row["folder_path"],
                    "files_deleted": True,
                }
            c.execute(
                "UPDATE recipes SET deleted_at=NULL, files_deleted=0 WHERE id=?",
                (recipe_id,)
            )
            return {"ok": True, "folder_path": row["folder_path"],
                    "files_deleted": bool(row["files_deleted"])}

    def recipe_list_trash_expired(self, days: int = 30) -> List[Dict[str, Any]]:
        """Alle Rezepte im Papierkorb die älter als N Tage sind.
        Verwendet vom Cleanup-Background-Job."""
        import time
        cutoff = time.time() - (days * 86400)
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM recipes WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,)
            ).fetchall()]

    def recipe_count_trash(self) -> int:
        with self.conn() as c:
            return int(c.execute(
                "SELECT COUNT(*) AS n FROM recipes WHERE deleted_at IS NOT NULL"
            ).fetchone()["n"])

    def _append_smart_search(self, where: List[str], params: List[Any], search: str) -> None:
        """Erweitert WHERE um Synonyme und Ausschlüsse. Positive Gruppen
        werden AND-verknüpft, Synonyme innerhalb einer Gruppe OR."""
        from .recipes.search import parse_search_query
        plan = parse_search_query(search, self.search_synonyms_map())
        for group in plan.positive_groups:
            fts_parts = [_build_fts_query(term) for term in group]
            fts_parts = [part for part in fts_parts if part]
            likes = [str(term).strip() for term in group if len(str(term).strip()) >= 2]
            clauses = []
            if fts_parts:
                clauses.append(
                    "r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)"
                )
                params.append(" OR ".join(f"({part})" for part in fts_parts))
            if likes:
                # FTS5 ist schnell, matcht aber nur Wortanfänge. Der ergänzende
                # LIKE-Pfad findet auch zusammengesetzte deutsche Begriffe wie
                # „Kartoffelpfanne“ bei der Suche nach „Pfanne“.
                broad_parts = []
                for term in likes:
                    like = f"%{term}%"
                    broad_parts.append(
                        "(COALESCE(r.name,'') LIKE ? OR COALESCE(r.description,'') LIKE ? "
                        "OR COALESCE(r.type,'') LIKE ? OR COALESCE(r.category,'') LIKE ? "
                        "OR EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id=r.id "
                        "AND (COALESCE(ri.canonical_name,'') LIKE ? OR COALESCE(ri.name,'') LIKE ?)))"
                    )
                    params.extend([like, like, like, like, like, like])
                clauses.append("(" + " OR ".join(broad_parts) + ")")
            if clauses:
                where.append("(" + " OR ".join(clauses) + ")")

        for term in plan.negative_terms:
            like = f"%{term}%"
            where.append(
                "NOT (COALESCE(r.name,'') LIKE ? OR COALESCE(r.description,'') LIKE ? "
                "OR COALESCE(r.type,'') LIKE ? OR COALESCE(r.category,'') LIKE ? "
                "OR EXISTS (SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id=r.id "
                "AND (COALESCE(ri.canonical_name,'') LIKE ? OR COALESCE(ri.name,'') LIKE ?)))"
            )
            params.extend([like, like, like, like, like, like])

    def recipe_list(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        folder_prefix: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        ingredient_canonical: Optional[List[str]] = None,
        ingredient_excluded: Optional[List[str]] = None,
        search: Optional[str] = None,
        ingredients_status: Optional[str] = None,
        verified: Optional[bool] = None,
        favorite_only: bool = False,
        min_rating: int = 0,
        needs_manual_care: Optional[bool] = None,
        include_deleted: bool = False,
        only_deleted: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Filterfähige Rezeptliste mit SQL-seitiger Suchrelevanz."""
        from .recipes.query_builder import build_recipe_filters, search_rank_sql
        where_sql, where_params = build_recipe_filters(self,
            type=type,
            category=category,
            folder_prefix=folder_prefix,
            tag_ids=tag_ids,
            ingredient_canonical=ingredient_canonical,
            ingredient_excluded=ingredient_excluded,
            search=search,
            ingredients_status=ingredients_status,
            verified=verified,
            favorite_only=favorite_only,
            min_rating=min_rating,
            needs_manual_care=needs_manual_care,
            include_deleted=include_deleted,
            only_deleted=only_deleted,
        )
        select_sql = (
            "SELECT r.*, "
            "(SELECT COUNT(*) FROM recipe_ingredients ri "
            " WHERE ri.recipe_id=r.id) AS ingredients_count, "
            "(SELECT COUNT(*) FROM recipe_steps rs "
            " WHERE rs.recipe_id=r.id) AS steps_count"
        )
        params: List[Any] = []
        if search:
            rank_sql, rank_params = search_rank_sql(self, search)
            select_sql += f", {rank_sql} AS _search_score"
            params.extend(rank_params)
            order_sql = "_search_score DESC, COALESCE(r.source_added_at, r.indexed_at) DESC"
        else:
            order_sql = (
                "r.deleted_at DESC" if only_deleted
                else "COALESCE(r.source_added_at, r.indexed_at) DESC"
            )
        params.extend(where_params)
        params.extend([limit, offset])
        sql = f"{select_sql} FROM recipes r{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?"
        with self.conn() as c:
            rows = [dict(row) for row in c.execute(sql, params).fetchall()]
        for row in rows:
            row.pop("_search_score", None)
        return rows

    def recipe_count(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        folder_prefix: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        ingredient_canonical: Optional[List[str]] = None,
        ingredient_excluded: Optional[List[str]] = None,
        search: Optional[str] = None,
        ingredients_status: Optional[str] = None,
        verified: Optional[bool] = None,
        favorite_only: bool = False,
        min_rating: int = 0,
        needs_manual_care: Optional[bool] = None,
        include_deleted: bool = False,
        only_deleted: bool = False,
    ) -> int:
        """Gleiche Filter wie ``recipe_list``; liefert nur die Trefferzahl."""
        from .recipes.query_builder import build_recipe_filters
        where_sql, params = build_recipe_filters(self,
            type=type,
            category=category,
            folder_prefix=folder_prefix,
            tag_ids=tag_ids,
            ingredient_canonical=ingredient_canonical,
            ingredient_excluded=ingredient_excluded,
            search=search,
            ingredients_status=ingredients_status,
            verified=verified,
            favorite_only=favorite_only,
            min_rating=min_rating,
            needs_manual_care=needs_manual_care,
            include_deleted=include_deleted,
            only_deleted=only_deleted,
        )
        with self.conn() as c:
            row = c.execute(f"SELECT COUNT(*) AS n FROM recipes r{where_sql}", params).fetchone()
        return int(row["n"])

    def recipes_pending_extraction(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alle pending Rezepte für den Worker. KEIN description-Filter hier —
        früher war 'AND length(description) >= 20' drin, was zu einem Counter-
        Mismatch führte: pending_count zählte ohne Filter, Worker-Batch mit
        Filter → bei 4 Rezepten ohne Description sah der Counter 'pending=4',
        der Worker fand 0 und endete sofort. ensure_extraction_running() sah
        wieder 'pending=4' und startete den Worker erneut → Endless-Loop.

        Statt zwei verschiedenen Queries: Worker pickt alle pending, und
        _extract_for_recipe() setzt Rezepte mit zu kurzer description selbst
        auf 'skipped'. Dadurch konsistent."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipes WHERE ingredients_status='pending' "
                "ORDER BY COALESCE(source_added_at, indexed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recipes_requeue_stale_extractions(self, lease_seconds: int = 1800) -> int:
        """Gibt nach Prozessabbruch verwaiste Worker-Claims wieder frei."""
        cutoff = time.time() - max(60, int(lease_seconds))
        with self.conn() as c:
            cur = c.execute(
                "UPDATE recipes SET ingredients_status='pending', "
                "extraction_claimed_at=NULL, extraction_claim_owner=NULL "
                "WHERE ingredients_status='running' "
                "AND COALESCE(extraction_claimed_at, 0) < ?",
                (cutoff,),
            )
            return int(cur.rowcount)

    def recipes_claim_extraction(
        self,
        *,
        limit: int,
        owner: str,
        lease_seconds: int = 1800,
    ) -> List[Dict[str, Any]]:
        """Claimt einen Batch in EINER Write-Transaktion.

        Dadurch können weder mehrere App-Prozesse noch überlappende Worker
        dieselben pending-Zeilen auswählen. Alte Claims werden vorher
        automatisch freigegeben.
        """
        if not owner:
            raise ValueError("owner darf nicht leer sein")
        limit = max(1, min(int(limit), 100))
        now = time.time()
        cutoff = now - max(60, int(lease_seconds))
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE recipes SET ingredients_status='pending', "
                "extraction_claimed_at=NULL, extraction_claim_owner=NULL "
                "WHERE ingredients_status='running' "
                "AND COALESCE(extraction_claimed_at, 0) < ?",
                (cutoff,),
            )
            ids = [
                int(r["id"])
                for r in c.execute(
                    "SELECT id FROM recipes WHERE ingredients_status='pending' "
                    "AND deleted_at IS NULL "
                    "ORDER BY COALESCE(source_added_at, indexed_at) DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            c.execute(
                f"UPDATE recipes SET ingredients_status='running', "
                f"extraction_claimed_at=?, extraction_claim_owner=? "
                f"WHERE id IN ({placeholders}) AND ingredients_status='pending'",
                (now, owner, *ids),
            )
            rows = c.execute(
                f"SELECT * FROM recipes WHERE id IN ({placeholders}) "
                "AND ingredients_status='running' AND extraction_claim_owner=?",
                (*ids, owner),
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
        *,
        claim_owner: Optional[str] = None,
    ) -> bool:
        """Setzt status + writes ingredients ATOMISCH. Bei status='ok' werden
        alte ingredients ersetzt; bei status='error'/'skipped' nur das Flag."""
        now = time.time()
        with self.conn() as c:
            if claim_owner is not None:
                owned = c.execute(
                    "SELECT 1 FROM recipes WHERE id=? "
                    "AND ingredients_status='running' AND extraction_claim_owner=?",
                    (recipe_id, claim_owner),
                ).fetchone()
                if not owned:
                    return False
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
                "UPDATE recipes SET ingredients_status=?, ingredients_extracted_at=?, "
                "extraction_claimed_at=NULL, extraction_claim_owner=NULL WHERE id=?",
                (status, now, recipe_id),
            )
            return True

    def recipe_apply_extraction_result(
        self,
        recipe_id: int,
        *,
        ingredients: List[Dict[str, Any]],
        steps: List[Dict[str, Any]],
        servings: Optional[int],
        auto_tags: List[str],
        claim_owner: Optional[str] = None,
    ) -> bool:
        """Ersetzt das komplette KI-Ergebnis atomar und setzt erst zuletzt ok."""
        if servings is not None:
            try:
                servings = int(servings)
                if servings <= 0:
                    servings = None
            except (TypeError, ValueError):
                servings = None

        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if claim_owner is not None:
                owned = c.execute(
                    "SELECT 1 FROM recipes WHERE id=? "
                    "AND ingredients_status='running' AND extraction_claim_owner=?",
                    (recipe_id, claim_owner),
                ).fetchone()
                if not owned:
                    return False

            c.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
            for idx, ing in enumerate(ingredients):
                c.execute(
                    "INSERT INTO recipe_ingredients (recipe_id, name, canonical_name, "
                    "amount, unit, raw, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        recipe_id,
                        ing.get("name") or "",
                        ing.get("canonical_name"),
                        ing.get("amount"),
                        ing.get("unit"),
                        ing.get("raw"),
                        idx,
                    ),
                )

            c.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,))
            for idx, step in enumerate(steps, start=1):
                instruction = (step.get("instruction") or "").strip()
                if not instruction:
                    continue
                timer = step.get("timer_seconds")
                try:
                    timer = int(timer) if timer is not None else None
                    if timer is not None and timer <= 0:
                        timer = None
                except (TypeError, ValueError):
                    timer = None
                c.execute(
                    "INSERT INTO recipe_steps "
                    "(recipe_id, step_number, instruction, timer_seconds) "
                    "VALUES (?, ?, ?, ?)",
                    (recipe_id, idx, instruction, timer),
                )

            c.execute("UPDATE recipes SET servings=? WHERE id=?", (servings, recipe_id))
            c.execute(
                "DELETE FROM recipe_tags WHERE recipe_id=? AND auto=1",
                (recipe_id,),
            )
            for raw in auto_tags:
                name = (raw or "").strip()
                if not name:
                    continue
                c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                tag = c.execute(
                    "SELECT id FROM tags WHERE name=? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if tag:
                    c.execute(
                        "INSERT OR IGNORE INTO recipe_tags "
                        "(recipe_id, tag_id, auto) VALUES (?, ?, 1)",
                        (recipe_id, int(tag["id"])),
                    )

            cur = c.execute(
                "UPDATE recipes SET ingredients_status='ok', "
                "ingredients_extracted_at=?, extraction_claimed_at=NULL, "
                "extraction_claim_owner=NULL WHERE id=?",
                (time.time(), recipe_id),
            )
            return bool(cur.rowcount)

    def recipe_ingredients_get(self, recipe_id: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order, id",
                (recipe_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def shopping_excluded_canonicals(self) -> set[str]:
        """Canonical-Namen, die nie automatisch eingekauft werden."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT canonical_name FROM shopping_exclusions"
            ).fetchall()
            return {str(row["canonical_name"]).strip().lower() for row in rows}

    def shopping_exclusion_set(self, canonical_name: str, excluded: bool) -> None:
        canonical = (canonical_name or "").strip().lower()
        if not canonical:
            raise ValueError("canonical_name darf nicht leer sein")
        with self.conn() as c:
            if excluded:
                c.execute(
                    "INSERT OR IGNORE INTO shopping_exclusions "
                    "(canonical_name, created_at) VALUES (?, ?)",
                    (canonical, time.time()),
                )
            else:
                c.execute(
                    "DELETE FROM shopping_exclusions WHERE canonical_name=?",
                    (canonical,),
                )

    # ─── Weekly meal plan ────────────────────────────────────────────────

    def meal_plan_entries(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT mp.id, mp.planned_for, mp.recipe_id, mp.planned_servings,
                       mp.sort_order, mp.created_at, mp.updated_at,
                       r.name AS recipe_name, r.servings AS recipe_servings,
                       r.thumb_filename, r.ingredients_status,
                       (SELECT COUNT(*) FROM recipe_ingredients ri
                        WHERE ri.recipe_id=r.id) AS ingredients_count
                FROM meal_plan_entries mp
                JOIN recipes r ON r.id=mp.recipe_id
                WHERE mp.planned_for BETWEEN ? AND ?
                  AND r.deleted_at IS NULL
                ORDER BY mp.planned_for, mp.sort_order, mp.id
                """,
                (start_date, end_date),
            ).fetchall()
            return [dict(row) for row in rows]

    def meal_plan_add(
        self,
        *,
        planned_for: str,
        recipe_id: int,
        planned_servings: int,
    ) -> Dict[str, Any]:
        now = time.time()
        with self.conn() as c:
            recipe = c.execute(
                "SELECT id FROM recipes WHERE id=? AND deleted_at IS NULL",
                (recipe_id,),
            ).fetchone()
            if not recipe:
                raise ValueError("Rezept nicht gefunden")
            next_order = int(c.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 "
                "FROM meal_plan_entries WHERE planned_for=?",
                (planned_for,),
            ).fetchone()[0])
            c.execute(
                """
                INSERT INTO meal_plan_entries
                    (planned_for, recipe_id, planned_servings, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(planned_for, recipe_id) DO UPDATE SET
                    planned_servings=excluded.planned_servings,
                    updated_at=excluded.updated_at
                """,
                (
                    planned_for,
                    recipe_id,
                    planned_servings,
                    next_order,
                    now,
                    now,
                ),
            )
            row = c.execute(
                "SELECT id, planned_for, recipe_id, planned_servings, sort_order "
                "FROM meal_plan_entries WHERE planned_for=? AND recipe_id=?",
                (planned_for, recipe_id),
            ).fetchone()
            return dict(row)

    def meal_plan_update(
        self,
        item_id: int,
        *,
        planned_for: Optional[str] = None,
        planned_servings: Optional[int] = None,
    ) -> bool:
        sets = ["updated_at=?"]
        params: List[Any] = [time.time()]
        if planned_for is not None:
            sets.append("planned_for=?")
            params.append(planned_for)
        if planned_servings is not None:
            sets.append("planned_servings=?")
            params.append(planned_servings)
        params.append(item_id)
        with self.conn() as c:
            try:
                cur = c.execute(
                    f"UPDATE meal_plan_entries SET {', '.join(sets)} WHERE id=?",
                    params,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Rezept ist für diesen Tag bereits eingeplant") from exc
            return cur.rowcount > 0

    def meal_plan_delete(self, item_id: int) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM meal_plan_entries WHERE id=?", (item_id,))
            return cur.rowcount > 0

    # ─── Steps + Servings ─────────────────────────────────────────────────

    def recipe_ingredient_set_calories(self, ingredient_id: int, calories) -> None:
        with self.conn() as c:
            c.execute("UPDATE recipe_ingredients SET calories=? WHERE id=?",
                      (calories, ingredient_id))

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

    def _recipe_where(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        folder_prefix: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        ingredient_canonical: Optional[List[str]] = None,
        ingredient_excluded: Optional[List[str]] = None,
        search: Optional[str] = None,
        ingredients_status: Optional[str] = None,
        verified: Optional[bool] = None,
        favorite_only: bool = False,
        min_rating: int = 0,
        include_deleted: bool = False,
        only_deleted: bool = False,
    ):
        """WHERE-Bedingungen + Params für Rezept-Filter (Alias r). Spiegelt die
        Logik aus recipe_count, damit die Facetten-Trefferzahlen exakt zur
        Listen-Filterung passen."""
        from .recipes.query_builder import build_recipe_filters

        where_sql, params = build_recipe_filters(
            self,
            type=type,
            category=category,
            folder_prefix=folder_prefix,
            tag_ids=tag_ids,
            ingredient_canonical=ingredient_canonical,
            ingredient_excluded=ingredient_excluded,
            search=search,
            ingredients_status=ingredients_status,
            verified=verified,
            favorite_only=favorite_only,
            min_rating=min_rating,
            include_deleted=include_deleted,
            only_deleted=only_deleted,
        )
        return ([where_sql.removeprefix(" WHERE ")] if where_sql else []), params

    def tag_facets(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        ingredient_canonical: Optional[List[str]] = None,
        ingredient_excluded: Optional[List[str]] = None,
        search: Optional[str] = None,
        ingredients_status: Optional[str] = None,
        verified: Optional[bool] = None,
        favorite_only: bool = False,
        min_rating: int = 0,
        **_ignore,
    ) -> List[Dict[str, Any]]:
        """Tags mit Recipe-Count unter den aktiven Filtern — der Tag-Filter selbst
        wird ausgeklammert (Standard-Facetten-Drilldown). Tags ohne Treffer
        fallen raus, die Liste schrumpft also passend mit."""
        where, params = self._recipe_where(
            type=type, category=category, ingredient_canonical=ingredient_canonical,
            ingredient_excluded=ingredient_excluded,
            search=search, ingredients_status=ingredients_status, verified=verified,
            favorite_only=favorite_only, min_rating=min_rating,
        )
        sql = (
            "SELECT t.id, t.name, COUNT(DISTINCT r.id) AS n "
            "FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id "
            "JOIN recipes r ON r.id = rt.recipe_id "
            "WHERE " + " AND ".join(where) +
            " GROUP BY t.id, t.name ORDER BY n DESC, t.name"
        )
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def ingredient_facets(
        self,
        *,
        type: Optional[str] = None,
        category: Optional[str] = None,
        tag_ids: Optional[List[int]] = None,
        search: Optional[str] = None,
        ingredients_status: Optional[str] = None,
        verified: Optional[bool] = None,
        favorite_only: bool = False,
        min_rating: int = 0,
        **_ignore,
    ) -> List[Dict[str, Any]]:
        """Zutaten mit Recipe-Count unter den aktiven Filtern — der Zutaten-Filter
        selbst wird ausgeklammert. Zutaten ohne Treffer fallen raus."""
        where, params = self._recipe_where(
            type=type, category=category, tag_ids=tag_ids,
            search=search, ingredients_status=ingredients_status, verified=verified,
            favorite_only=favorite_only, min_rating=min_rating,
        )
        sql = (
            "SELECT ing.canonical_name, MIN(ing.name) AS display_name, "
            "COUNT(DISTINCT r.id) AS n "
            "FROM recipe_ingredients ing JOIN recipes r ON r.id = ing.recipe_id "
            "WHERE ing.canonical_name IS NOT NULL AND ing.canonical_name != '' "
            "AND " + " AND ".join(where) +
            " GROUP BY ing.canonical_name ORDER BY n DESC, ing.canonical_name"
        )
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

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
        """Ersetzt alle USER-Tags eines Rezepts. Auto-Tags (auto=1) bleiben
        unangetastet — sonst würde der nächste KI-Re-Extract sie eh wieder
        anlegen, und User-Edits durch Re-Extract verlieren wäre unerwartet."""
        with self.conn() as c:
            c.execute("DELETE FROM recipe_tags WHERE recipe_id=? AND auto=0", (recipe_id,))
        for raw in tag_names:
            name = (raw or "").strip()
            if not name:
                continue
            tag_id = self.tag_get_or_create(name)
            with self.conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id, auto) VALUES (?, ?, 0)",
                    (recipe_id, tag_id),
                )

    def recipe_auto_tags_set(self, recipe_id: int, tag_names: List[str]) -> None:
        """Ersetzt nur die Auto-Tags. User-Tags bleiben unangetastet.
        Wenn Auto-Tag und User-Tag identisch sind: User-Tag gewinnt
        (UNIQUE-Constraint im PK verhindert Duplikat, INSERT IGNORE)."""
        with self.conn() as c:
            c.execute("DELETE FROM recipe_tags WHERE recipe_id=? AND auto=1", (recipe_id,))
        for raw in tag_names:
            name = (raw or "").strip()
            if not name:
                continue
            tag_id = self.tag_get_or_create(name)
            with self.conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id, auto) VALUES (?, ?, 1)",
                    (recipe_id, tag_id),
                )

    def recipe_tags_get(self, recipe_id: int) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT t.id, t.name, rt.auto FROM tags t "
                "JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id=? "
                "ORDER BY rt.auto DESC, t.name",
                (recipe_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── User-Verifikation ──────────────────────────────────────────────
    def recipe_set_verified(self, recipe_id: int, verified: bool,
                              username: Optional[str]) -> None:
        """Toggle für die 'manuell geprüft, ok'-Checkbox. Bei verified=True
        wird Timestamp + Username für den Audit-Trail mitgeschrieben, bei
        False werden beide gelöscht."""
        with self.conn() as c:
            if verified:
                c.execute(
                    "UPDATE recipes SET user_verified=1, verified_at=?, verified_by=? "
                    "WHERE id=?",
                    (time.time(), username, recipe_id),
                )
            else:
                c.execute(
                    "UPDATE recipes SET user_verified=0, verified_at=NULL, "
                    "verified_by=NULL WHERE id=?",
                    (recipe_id,),
                )

    # ─── Nährwerte ──────────────────────────────────────────────────────
    def recipe_set_nutrition(self, recipe_id: int, calories: int,
                              protein_g: float, carbs_g: float, fat_g: float) -> None:
        """Schreibt die KI-geschätzten Nährwerte + Zeitstempel.
        Aufgerufen vom Worker nach Extract und vom on-demand-Endpoint."""
        with self.conn() as c:
            c.execute(
                "UPDATE recipes SET calories_per_serving=?, protein_g=?, "
                "carbs_g=?, fat_g=?, nutrition_computed_at=? WHERE id=?",
                (calories, protein_g, carbs_g, fat_g, time.time(), recipe_id),
            )

    def recipes_pending_nutrition(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Rezepte mit Zutaten >= 3 aber ohne berechnete Nährwerte. Für
        Bulk-Compute (Audit-Trigger). Limit gegen Endlos-Listen."""
        with self.conn() as c:
            rows = c.execute("""
                SELECT r.id, r.name, r.servings,
                       (SELECT COUNT(*) FROM recipe_ingredients WHERE recipe_id=r.id) as ing_count
                FROM recipes r
                WHERE r.calories_per_serving IS NULL
                  AND r.ingredients_status = 'ok'
                GROUP BY r.id
                HAVING ing_count >= 3
                ORDER BY r.id
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ─── User-Verwaltung (Multi-User-Auth) ──────────────────────────────
    def user_get_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        """Liefert User-Row inkl. password_hash. Auch disabled-Users werden
        zurückgegeben — Caller entscheidet (Login: ablehnen; Settings: anzeigen).
        Username-Match ist case-insensitiv (COLLATE NOCASE) — 'Admin', 'admin'
        und 'ADMIN' treffen denselben User. Das UNIQUE-Constraint auf username
        bleibt case-sensitiv (BINARY), daher hier explizit NOCASE im WHERE."""
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            return dict(row) if row else None

    def user_list(self) -> List[Dict[str, Any]]:
        """Liste aller User für die Settings-UI. OHNE password_hash."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, username, disabled, created_at, last_login_at "
                "FROM users ORDER BY username"
            ).fetchall()
            return [dict(r) for r in rows]

    def user_create(self, username: str, password_hash: str,
                     role: str = "user") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, password_hash, role, time.time()),
            )
            return int(cur.lastrowid)

    def user_set_password(self, user_id: int, password_hash: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE users SET password_hash=?, "
                "session_version=session_version+1 WHERE id=?",
                (password_hash, user_id),
            )

    def user_set_role(self, user_id: int, role: str) -> None:
        """Legacy-Kompatibilität; Rollen werden nicht mehr ausgewertet."""
        with self.conn() as c:
            c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))

    def user_set_disabled(self, user_id: int, disabled: bool) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE users SET disabled=?, "
                "session_version=session_version+1 WHERE id=?",
                (1 if disabled else 0, user_id),
            )

    def user_delete(self, user_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM users WHERE id=?", (user_id,))

    def user_update_last_login(self, user_id: int) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE users SET last_login_at=? WHERE id=?",
                (time.time(), user_id),
            )

    def user_revoke_sessions(self, username: str) -> bool:
        """Macht alle bestehenden signierten Sessions eines Benutzers ungültig."""
        with self.conn() as c:
            cur = c.execute(
                "UPDATE users SET session_version=session_version+1 "
                "WHERE username=? COLLATE NOCASE",
                (username,),
            )
            return cur.rowcount > 0

    def user_count_active_admins(self) -> int:
        """Verhindert Lockout: vor delete/disable/role-change prüfen dass
        mindestens 1 aktiver Admin übrig bleibt."""
        with self.conn() as c:
            return int(c.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0"
            ).fetchone()[0])

    # ─── Sync-Errors (FS-Konflikte) ──────────────────────────────────────
    def sync_error_record(self, folder_path: str, error_type: str,
                           error_msg: str, conflict_with_id: Optional[int] = None) -> None:
        """Speichert einen FS-Sync-Fehler. UNIQUE auf folder_path — wenn der
        gleiche Folder erneut crasht, wird der bisherige Eintrag überschrieben
        (z.B. weil sich der Fehler geändert hat)."""
        with self.conn() as c:
            c.execute(
                "INSERT INTO sync_errors (folder_path, error_type, error_msg, "
                "conflict_with_id, detected_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(folder_path) DO UPDATE SET "
                "error_type=excluded.error_type, error_msg=excluded.error_msg, "
                "conflict_with_id=excluded.conflict_with_id, detected_at=excluded.detected_at",
                (folder_path, error_type, error_msg, conflict_with_id, time.time()),
            )

    def sync_errors_clear(self) -> None:
        """Löscht alle Sync-Errors. Vor jedem neuen Sync aufgerufen — sonst
        bleiben alte Conflicts hängen die der User schon gelöst hat."""
        with self.conn() as c:
            c.execute("DELETE FROM sync_errors")

    def sync_errors_list(self) -> List[Dict[str, Any]]:
        """Alle aktuellen FS-Konflikte mit Folder-Path und Konflikt-Info."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT s.*, r.name as conflict_name, r.folder_path as conflict_folder "
                "FROM sync_errors s "
                "LEFT JOIN recipes r ON r.id = s.conflict_with_id "
                "ORDER BY s.detected_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def sync_errors_count(self) -> int:
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM sync_errors").fetchone()[0])

    # ─── Audit-AI-Findings (KI-Sanity-Check) ────────────────────────────
    def audit_ai_finding_set(self, recipe_id: int, finding_type: str,
                              current_value: str, suggested_value: str,
                              reason: str) -> None:
        """Speichert/ersetzt ein KI-Finding. Neue Findings überschreiben alte
        (UPSERT auf recipe_id+finding_type) — bei jedem Audit-Lauf wird der
        Stand frisch berechnet. resolved=0 (offen)."""
        with self.conn() as c:
            c.execute(
                "INSERT INTO audit_ai_findings (recipe_id, finding_type, "
                "current_value, suggested_value, reason, resolved, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?) "
                "ON CONFLICT(recipe_id, finding_type) DO UPDATE SET "
                "current_value=excluded.current_value, "
                "suggested_value=excluded.suggested_value, "
                "reason=excluded.reason, resolved=0, created_at=excluded.created_at",
                (recipe_id, finding_type, current_value, suggested_value,
                 reason, time.time()),
            )

    def audit_ai_findings_list(self, finding_type: Optional[str] = None,
                                only_open: bool = True) -> List[Dict[str, Any]]:
        """Liefert KI-Findings, optional gefiltert nach Typ + Status."""
        sql = ("SELECT f.*, r.name as recipe_name, r.folder_path "
               "FROM audit_ai_findings f "
               "JOIN recipes r ON r.id = f.recipe_id WHERE 1=1")
        params: list = []
        if finding_type:
            sql += " AND f.finding_type=?"; params.append(finding_type)
        if only_open:
            sql += " AND f.resolved=0"
        sql += " ORDER BY f.created_at DESC"
        with self.conn() as c:
            rows = c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def audit_ai_finding_resolve(self, finding_id: int) -> None:
        """Markiert ein Finding als bearbeitet (ignoriert oder angewendet).
        User-Aktion via UI-Button."""
        with self.conn() as c:
            c.execute("UPDATE audit_ai_findings SET resolved=1 WHERE id=?", (finding_id,))

    def audit_ai_findings_count(self, only_open: bool = True) -> Dict[str, int]:
        """Counter pro finding_type für die Audit-Summary."""
        with self.conn() as c:
            sql = "SELECT finding_type, COUNT(*) FROM audit_ai_findings"
            if only_open:
                sql += " WHERE resolved=0"
            sql += " GROUP BY finding_type"
            return {r[0]: int(r[1]) for r in c.execute(sql).fetchall()}

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
        with self.conn() as c:
            # Lesen und Schreiben müssen in derselben Write-Transaction liegen.
            # Sonst können zwei schnelle Adds beide "nicht vorhanden" sehen und
            # doppelte Zeilen erzeugen bzw. Mengen verlieren.
            c.execute("BEGIN IMMEDIATE")
            existing = None
            if canonical_name:
                if unit is None:
                    existing = c.execute(
                        "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit IS NULL",
                        (canonical_name,),
                    ).fetchone()
                else:
                    existing = c.execute(
                        "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit=?",
                        (canonical_name, unit),
                    ).fetchone()
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

    # ─── Wiederkehrende Einkäufe ────────────────────────────────────
    def recurring_list(self) -> List[Dict[str, Any]]:
        from .recipes.cart_logic import display_amount

        today = date.today()
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM shopping_recurring ORDER BY active DESC, next_due_on, name COLLATE NOCASE"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["due_in_days"] = (date.fromisoformat(item["next_due_on"]) - today).days
            except (TypeError, ValueError):
                item["due_in_days"] = 0
            item["active"] = bool(item["active"])
            item["amount_base"] = item.get("amount")
            item["unit_base"] = item.get("unit")
            display_value, display_unit = display_amount(item.get("amount"), item.get("unit"))
            item["amount"] = display_value
            item["unit"] = display_unit
            # Kompatibler Feldname für bestehende Web- und Native-Clients.
            item["default_unit"] = display_unit
            result.append(item)
        return result

    def recurring_create(
        self,
        *,
        name: str,
        canonical_name: Optional[str],
        amount: Optional[float],
        unit: Optional[str],
        category: Optional[str],
        interval_days: int,
        next_due_on: str,
        active: bool,
    ) -> int:
        now = time.time()
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO shopping_recurring "
                "(name, canonical_name, amount, unit, category, interval_days, next_due_on, "
                "active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name, canonical_name, amount, unit, category, int(interval_days),
                    next_due_on, 1 if active else 0, now, now,
                ),
            )
            return int(cur.lastrowid)

    def recurring_get(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM shopping_recurring WHERE id=?", (int(item_id),)
            ).fetchone()
        return dict(row) if row else None

    def recurring_update(self, item_id: int, values: Dict[str, Any]) -> bool:
        allowed = {
            "name", "canonical_name", "amount", "unit", "category",
            "interval_days", "next_due_on", "active",
        }
        sets: List[str] = []
        params: List[Any] = []
        for key, value in values.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            params.append(1 if key == "active" and value else 0 if key == "active" else value)
        if not sets:
            return self.recurring_get(item_id) is not None
        sets.append("updated_at=?")
        params.extend((time.time(), int(item_id)))
        with self.conn() as c:
            cur = c.execute(
                f"UPDATE shopping_recurring SET {', '.join(sets)} WHERE id=?", params
            )
            return bool(cur.rowcount)

    def recurring_delete(self, item_id: int) -> bool:
        with self.conn() as c:
            cur = c.execute("DELETE FROM shopping_recurring WHERE id=?", (int(item_id),))
            return bool(cur.rowcount)

    def recurring_run_due(self, *, due_on: Optional[date] = None) -> List[Dict[str, Any]]:
        """Fällige Regeln und Cart-Merge als eine atomare Operation.

        ``next_due_on`` wird so oft um das Intervall erhöht, bis es nach dem
        Lauftag liegt. Dadurch erzeugt ein verspäteter Lauf keinen Stapel aus
        identischen Artikeln und ein wiederholter Aufruf am selben Tag ist
        idempotent.
        """
        run_day = due_on or date.today()
        now = time.time()
        added: List[Dict[str, Any]] = []
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT * FROM shopping_recurring "
                "WHERE active=1 AND next_due_on<=? ORDER BY next_due_on, id",
                (run_day.isoformat(),),
            ).fetchall()
            for row in rows:
                canonical = row["canonical_name"]
                unit = row["unit"]
                existing = None
                if canonical:
                    if unit is None:
                        existing = c.execute(
                            "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit IS NULL",
                            (canonical,),
                        ).fetchone()
                    else:
                        existing = c.execute(
                            "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit=?",
                            (canonical, unit),
                        ).fetchone()
                if existing:
                    amount = row["amount"]
                    old_amount = existing["amount"]
                    new_amount = (
                        (old_amount or 0) + (amount or 0)
                        if amount is not None else old_amount
                    )
                    c.execute(
                        "UPDATE shopping_cart SET amount=?, checked=0, added_at=? WHERE id=?",
                        (new_amount, now, existing["id"]),
                    )
                    cart_id = int(existing["id"])
                else:
                    cur = c.execute(
                        "INSERT INTO shopping_cart "
                        "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
                        "VALUES (?, ?, ?, ?, 0, ?, '[]')",
                        (row["name"], canonical, row["amount"], unit, now),
                    )
                    cart_id = int(cur.lastrowid)

                try:
                    next_due = date.fromisoformat(row["next_due_on"])
                except (TypeError, ValueError):
                    next_due = run_day
                interval = timedelta(days=max(1, int(row["interval_days"])))
                while next_due <= run_day:
                    next_due += interval
                c.execute(
                    "UPDATE shopping_recurring SET next_due_on=?, last_added_at=?, updated_at=? WHERE id=?",
                    (next_due.isoformat(), now, now, row["id"]),
                )
                added.append({"id": int(row["id"]), "cart_id": cart_id, "name": row["name"]})
        return added

    def cart_replace(self, items: List[Dict[str, Any]]) -> int:
        """Ersetzt den lokalen Warenkorb atomar durch bereits aggregierte Items."""
        now = time.time()
        with self.conn() as c:
            c.execute("DELETE FROM shopping_cart")
            for item in items:
                c.execute(
                    "INSERT INTO shopping_cart "
                    "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
                    "VALUES (?, ?, ?, ?, 0, ?, ?)",
                    (
                        item.get("name") or "?",
                        item.get("canonical_name"),
                        item.get("amount"),
                        item.get("unit"),
                        now,
                        json.dumps(item.get("source_recipe_ids") or []),
                    ),
                )
            return len(items)

    def cart_merge_many(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """Fügt aggregierte Wochenplan-Zutaten atomar zum Warenkorb hinzu.

        Bestehende manuelle Einträge bleiben erhalten. Gleiche Canonical-/
        Einheiten-Paare werden summiert und erneut als offen markiert.
        """
        added = 0
        merged = 0
        now = time.time()
        with self.conn() as c:
            for item in items:
                name = item.get("name") or "?"
                canonical = item.get("canonical_name")
                unit = item.get("unit")
                if canonical:
                    if unit is None:
                        existing = c.execute(
                            "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit IS NULL",
                            (canonical,),
                        ).fetchone()
                    else:
                        existing = c.execute(
                            "SELECT * FROM shopping_cart WHERE canonical_name=? AND unit=?",
                            (canonical, unit),
                        ).fetchone()
                else:
                    existing = None

                source_ids = list(item.get("source_recipe_ids") or [])
                if existing:
                    old_amount = existing["amount"]
                    amount = item.get("amount")
                    new_amount = (
                        (old_amount or 0) + (amount or 0)
                        if amount is not None
                        else old_amount
                    )
                    old_sources = json.loads(existing["source_recipe_ids"] or "[]")
                    for recipe_id in source_ids:
                        if recipe_id not in old_sources:
                            old_sources.append(recipe_id)
                    c.execute(
                        "UPDATE shopping_cart SET amount=?, checked=0, source_recipe_ids=? WHERE id=?",
                        (new_amount, json.dumps(old_sources), existing["id"]),
                    )
                    merged += 1
                else:
                    c.execute(
                        "INSERT INTO shopping_cart "
                        "(name, canonical_name, amount, unit, checked, added_at, source_recipe_ids) "
                        "VALUES (?, ?, ?, ?, 0, ?, ?)",
                        (
                            name,
                            canonical,
                            item.get("amount"),
                            unit,
                            now,
                            json.dumps(source_ids),
                        ),
                    )
                    added += 1
        return {"added": added, "merged": merged}

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


    def recipe_ingredients_for_ids(self, recipe_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Lädt Zutaten für eine Ergebnisliste in genau einer SQL-Abfrage.
        Vermeidet N+1-Queries beim Relevanz-Ranking der intelligenten Suche."""
        ids = sorted({int(v) for v in recipe_ids if int(v) > 0})[:500]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.conn() as c:
            rows = c.execute(
                f"SELECT recipe_id, name, canonical_name, amount, unit, raw, sort_order "
                f"FROM recipe_ingredients WHERE recipe_id IN ({placeholders}) "
                "ORDER BY recipe_id, sort_order, id",
                ids,
            ).fetchall()
        result: Dict[int, List[Dict[str, Any]]] = {recipe_id: [] for recipe_id in ids}
        for row in rows:
            item = dict(row)
            result.setdefault(int(item.pop("recipe_id")), []).append(item)
        return result

    # ─── Recipe versions / Undo ──────────────────────────────────────────
    def recipe_snapshot(self, recipe_id: int) -> Optional[Dict[str, Any]]:
        """Kompletter logischer Rezept-Snapshot ohne Binärdateien."""
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
            if not row:
                return None
            ingredients = [dict(r) for r in c.execute(
                "SELECT name, canonical_name, amount, unit, raw, sort_order, calories "
                "FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order, id",
                (recipe_id,),
            ).fetchall()]
            steps = [dict(r) for r in c.execute(
                "SELECT step_number, instruction, timer_seconds FROM recipe_steps "
                "WHERE recipe_id=? ORDER BY step_number, id", (recipe_id,),
            ).fetchall()]
            tags = [dict(r) for r in c.execute(
                "SELECT t.name, rt.auto FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id "
                "WHERE rt.recipe_id=? ORDER BY rt.auto, t.name", (recipe_id,),
            ).fetchall()]
            return {
                "recipe": dict(row),
                "ingredients": ingredients,
                "steps": steps,
                "tags": tags,
            }

    def recipe_version_create(self, recipe_id: int, *, created_by: str = "system",
                              source: str = "user", reason: str = "Änderung",
                              max_versions: int = 50) -> Optional[int]:
        snapshot = self.recipe_snapshot(recipe_id)
        if not snapshot:
            return None
        with self.conn() as c:
            next_no = int(c.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM recipe_versions WHERE recipe_id=?",
                (recipe_id,),
            ).fetchone()[0])
            cur = c.execute(
                "INSERT INTO recipe_versions (recipe_id, version_no, created_at, created_by, "
                "source, reason, snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (recipe_id, next_no, time.time(), created_by, source, reason,
                 json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))),
            )
            if max_versions > 0:
                c.execute(
                    "DELETE FROM recipe_versions WHERE recipe_id=? AND id NOT IN ("
                    "SELECT id FROM recipe_versions WHERE recipe_id=? "
                    "ORDER BY version_no DESC LIMIT ?)",
                    (recipe_id, recipe_id, int(max_versions)),
                )
            return int(cur.lastrowid)

    def recipe_versions_list(self, recipe_id: Optional[int] = None,
                             limit: int = 200) -> List[Dict[str, Any]]:
        sql = (
            "SELECT v.id, v.recipe_id, v.version_no, v.created_at, v.created_by, "
            "v.source, v.reason, r.name AS recipe_name "
            "FROM recipe_versions v LEFT JOIN recipes r ON r.id=v.recipe_id"
        )
        params: List[Any] = []
        if recipe_id is not None:
            sql += " WHERE v.recipe_id=?"; params.append(int(recipe_id))
        sql += " ORDER BY v.created_at DESC LIMIT ?"; params.append(max(1, min(1000, int(limit))))
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def recipe_version_get(self, version_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM recipe_versions WHERE id=?", (version_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            try:
                out["snapshot"] = json.loads(out.pop("snapshot_json"))
            except Exception:
                out["snapshot"] = None
            return out

    def recipe_version_attach_media(self, version_id: int, media: Dict[str, Any]) -> bool:
        """Ergänzt einen logischen Snapshot um sichere Dateisicherungs-Metadaten."""
        with self.conn() as c:
            row = c.execute(
                "SELECT snapshot_json FROM recipe_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            if not row:
                return False
            try:
                snapshot = json.loads(row["snapshot_json"])
            except Exception:
                return False
            snapshot["media"] = dict(media or {})
            c.execute(
                "UPDATE recipe_versions SET snapshot_json=? WHERE id=?",
                (
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    version_id,
                ),
            )
            return True

    def _backup_thumbnail_for_version(self, recipe: Dict[str, Any], version_id: int) -> None:
        """Sichert das aktuelle Cover im Rezeptordner und verknüpft es mit der Version."""
        from pathlib import Path
        from .core.safety import atomic_copy_file

        folder = Path(str(recipe.get("folder_path") or "")).resolve()
        filename = str(recipe.get("thumb_filename") or "").strip()
        if not filename:
            self.recipe_version_attach_media(version_id, {"thumbnail_absent": True})
            return
        source = (folder / filename).resolve()
        try:
            source.relative_to(folder)
        except ValueError:
            self.recipe_version_attach_media(version_id, {"thumbnail_absent": True})
            return
        if not source.is_file() or source.is_symlink():
            self.recipe_version_attach_media(version_id, {"thumbnail_absent": True})
            return
        relative = Path(".versions") / str(version_id) / f"thumbnail{source.suffix.lower()}"
        backup = folder / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy_file(source, backup)
        self.recipe_version_attach_media(
            version_id,
            {
                "thumbnail_backup": relative.as_posix(),
                "thumbnail_filename": filename,
            },
        )

    def recipe_version_restore(self, version_id: int, *, restored_by: str = "system") -> Dict[str, Any]:
        version = self.recipe_version_get(version_id)
        if not version or not version.get("snapshot"):
            return {"ok": False, "error": "Version nicht gefunden oder beschädigt"}
        recipe_id = int(version["recipe_id"])
        current = self.recipe_get(recipe_id)
        if not current:
            return {"ok": False, "error": "Rezept existiert nicht mehr"}
        snap = version["snapshot"]
        media = snap.get("media") or {}
        original_folder = Path(str(current.get("folder_path") or "")).resolve()
        backup_relative: Optional[Path] = None
        if media.get("thumbnail_backup"):
            backup_relative = Path(str(media["thumbnail_backup"]))
            backup = (original_folder / backup_relative).resolve()
            try:
                backup.relative_to(original_folder)
            except ValueError:
                return {"ok": False, "error": "Cover-Sicherung liegt außerhalb des Rezeptordners"}
            if not backup.is_file() or backup.is_symlink():
                return {"ok": False, "error": "Cover-Sicherung der Version fehlt"}
        # Undo-Snapshot des aktuellen Stands anlegen, bevor zurückgerollt wird.
        undo_version_id = self.recipe_version_create(
            recipe_id, created_by=restored_by, source="restore",
            reason=f"Stand vor Wiederherstellung von Version {version['version_no']}",
        )
        if undo_version_id is not None:
            self._backup_thumbnail_for_version(current, int(undo_version_id))
        recipe = snap.get("recipe") or {}
        # Name/Typ/Kategorie/Description bilden zugleich die Sidecars und die
        # NAS-Ordnerstruktur. Sie dürfen deshalb nicht nur in SQLite
        # zurückgeschrieben werden; sonst setzt der nächste FS-Sync den Restore
        # wieder zurück. Der Manager verschiebt und rollt diese Kerndaten
        # konsistent zurück.
        from .recipes.manage import safe_update_recipe_metadata

        try:
            safe_update_recipe_metadata(
                self,
                recipe_id,
                name=str(recipe.get("name") or current.get("name") or "Unbekannt"),
                recipe_type=str(recipe.get("type") or current.get("type") or "Sonstiges"),
                category=str(recipe.get("category") or current.get("category") or "Allgemein"),
                description=str(recipe.get("description") or ""),
                servings=recipe.get("servings"),
                url=recipe.get("url"),
                target_folder_override=recipe.get("folder_path"),
            )
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "error": f"Rezeptpfad konnte nicht wiederhergestellt werden: {exc}"}

        allowed = [
            "source_added_at", "ingredients_extracted_at",
            "ingredients_status", "calories_per_serving", "protein_g",
            "carbs_g", "fat_g", "nutrition_computed_at", "user_verified",
            "verified_at", "verified_by",
        ]
        sets, params = [], []
        for col in allowed:
            if col in recipe:
                sets.append(f"{col}=?"); params.append(recipe.get(col))
        params.append(recipe_id)
        try:
            with self.conn() as c:
                if sets:
                    c.execute(f"UPDATE recipes SET {', '.join(sets)} WHERE id=?", params)
                c.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
                for idx, ing in enumerate(snap.get("ingredients") or []):
                    c.execute(
                        "INSERT INTO recipe_ingredients (recipe_id, name, canonical_name, amount, unit, raw, sort_order, calories) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (recipe_id, ing.get("name") or "", ing.get("canonical_name"),
                         ing.get("amount"), ing.get("unit"), ing.get("raw"),
                         ing.get("sort_order", idx), ing.get("calories")),
                    )
                c.execute("DELETE FROM recipe_steps WHERE recipe_id=?", (recipe_id,))
                for idx, step in enumerate(snap.get("steps") or [], start=1):
                    c.execute(
                        "INSERT INTO recipe_steps (recipe_id, step_number, instruction, timer_seconds) VALUES (?, ?, ?, ?)",
                        (recipe_id, int(step.get("step_number") or idx),
                         step.get("instruction") or "", step.get("timer_seconds")),
                    )
                c.execute("DELETE FROM recipe_tags WHERE recipe_id=?", (recipe_id,))
                for tag in snap.get("tags") or []:
                    name = str(tag.get("name") or "").strip()
                    if not name:
                        continue
                    c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
                    tag_id = int(c.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()[0])
                    c.execute(
                        "INSERT OR IGNORE INTO recipe_tags(recipe_id, tag_id, auto) VALUES (?, ?, ?)",
                        (recipe_id, tag_id, 1 if tag.get("auto") else 0),
                    )
        except Exception as exc:
            try:
                safe_update_recipe_metadata(
                    self,
                    recipe_id,
                    name=str(current.get("name") or "Unbekannt"),
                    recipe_type=str(current.get("type") or "Sonstiges"),
                    category=str(current.get("category") or "Allgemein"),
                    description=str(current.get("description") or ""),
                    servings=current.get("servings"),
                    url=current.get("url"),
                    target_folder_override=current.get("folder_path"),
                )
            except Exception:
                logger.exception("Recipe #%s: Restore-Rollback unvollständig", recipe_id)
            return {"ok": False, "error": f"Versionsdaten konnten nicht wiederhergestellt werden: {exc}"}

        restored = self.recipe_get(recipe_id) or current
        folder = Path(str(restored.get("folder_path") or "")).resolve()
        backup = (folder / backup_relative).resolve() if backup_relative else None
        media_restored = False
        if media.get("thumbnail_absent"):
            current_thumb = str(restored.get("thumb_filename") or "").strip()
            if current_thumb:
                candidate = (folder / Path(current_thumb).name).resolve()
                try:
                    candidate.relative_to(folder)
                    if candidate.is_file() and not candidate.is_symlink():
                        candidate.unlink(missing_ok=True)
                except ValueError:
                    pass
            for candidate in folder.glob("thumb.*"):
                if candidate.is_file() and not candidate.is_symlink():
                    candidate.unlink(missing_ok=True)
            with self.conn() as c:
                c.execute("UPDATE recipes SET thumb_filename=NULL WHERE id=?", (recipe_id,))
            media_restored = True
        elif media.get("thumbnail_backup"):
            from .core.safety import atomic_copy_file

            filename = Path(str(media.get("thumbnail_filename") or "thumb.jpg")).name
            target = folder / filename
            if backup is None or not backup.is_file() or backup.is_symlink():
                return {"ok": False, "error": "Cover-Sicherung der Version fehlt"}
            atomic_copy_file(backup, target)
            with self.conn() as c:
                c.execute("UPDATE recipes SET thumb_filename=? WHERE id=?", (filename, recipe_id))
            media_restored = True
        return {
            "ok": True,
            "recipe_id": recipe_id,
            "restored_version": version["version_no"],
            "media_restored": media_restored,
        }

    # ─── Intelligent search administration ──────────────────────────────
    def search_synonyms_list(self) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM search_synonyms ORDER BY term COLLATE NOCASE").fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    d["synonyms"] = json.loads(d.pop("synonyms_json") or "[]")
                except Exception:
                    d["synonyms"] = []
                out.append(d)
            return out

    def search_synonym_upsert(self, term: str, synonyms: List[str], *, updated_by: str = "system") -> int:
        term = str(term or "").strip()
        clean = []
        seen = {term.casefold()}
        for value in synonyms or []:
            value = str(value or "").strip()
            if value and value.casefold() not in seen:
                clean.append(value); seen.add(value.casefold())
        if not term:
            raise ValueError("Begriff fehlt")
        with self.conn() as c:
            c.execute(
                "INSERT INTO search_synonyms(term, synonyms_json, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(term) DO UPDATE SET synonyms_json=excluded.synonyms_json, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (term, json.dumps(clean, ensure_ascii=False), time.time(), updated_by),
            )
            return int(c.execute("SELECT id FROM search_synonyms WHERE term=? COLLATE NOCASE", (term,)).fetchone()[0])

    def search_synonym_delete(self, synonym_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM search_synonyms WHERE id=?", (synonym_id,))

    def search_synonyms_map(self) -> Dict[str, List[str]]:
        from .recipes.search import fold
        mapping: Dict[str, List[str]] = {}
        for row in self.search_synonyms_list():
            group = [row["term"], *(row.get("synonyms") or [])]
            group = [str(v).strip() for v in group if str(v).strip()]
            for value in group:
                mapping[fold(value)] = group
        return mapping

    def search_vocabulary(self, limit: int = 3000) -> List[str]:
        with self.conn() as c:
            values = []
            values.extend(r[0] for r in c.execute(
                "SELECT DISTINCT canonical_name FROM recipe_ingredients "
                "WHERE canonical_name IS NOT NULL AND canonical_name != '' LIMIT ?", (limit,),
            ).fetchall())
            values.extend(r[0] for r in c.execute(
                "SELECT DISTINCT name FROM tags WHERE name != '' LIMIT ?", (limit,),
            ).fetchall())
            for row in c.execute("SELECT name FROM recipes WHERE deleted_at IS NULL LIMIT ?", (limit,)).fetchall():
                values.extend(str(row[0] or "").replace("-", " ").split())
        return sorted({str(v).strip() for v in values if len(str(v).strip()) >= 3}, key=str.casefold)

    # ─── Maintenance audit trail ────────────────────────────────────────
    def maintenance_start(self, kind: str, started_by: str = "system") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO maintenance_runs(kind, started_at, status, started_by) VALUES (?, ?, 'running', ?)",
                (kind, time.time(), started_by),
            )
            return int(cur.lastrowid)

    def maintenance_progress(self, run_id: int, result: Dict[str, Any]) -> None:
        """Persistiert Zwischenstände langer Wartungsläufe.

        ``status`` bleibt bewusst ``running``. Dadurch kann das Frontend einen
        PDF-Lauf nach einem Proxy-Timeout, Tab-Wechsel oder Handy-Sperren weiter
        verfolgen, ohne den eigentlichen Worker abzubrechen.
        """
        with self.conn() as c:
            c.execute(
                "UPDATE maintenance_runs SET result_json=? WHERE id=? AND status='running'",
                (json.dumps(result, ensure_ascii=False, default=str), run_id),
            )

    def maintenance_finish(self, run_id: int, *, ok: bool, result: Dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE maintenance_runs SET ended_at=?, status=?, result_json=? WHERE id=?",
                (time.time(), "ok" if ok else "error",
                 json.dumps(result, ensure_ascii=False, default=str), run_id),
            )

    def maintenance_get(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM maintenance_runs WHERE id=?", (int(run_id),)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["result"] = json.loads(item.pop("result_json") or "{}")
        except Exception:
            item["result"] = {}
        return item

    def maintenance_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM maintenance_runs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    d["result"] = json.loads(d.pop("result_json") or "{}")
                except Exception:
                    d["result"] = {}
                out.append(d)
            return out


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
