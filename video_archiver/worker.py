"""Standalone-Queue und Worker für ein privates, nicht ausgeliefertes Videoarchiv.

Dieses Modul importiert bewusst nichts aus ``app``. Die iPhone-App und das
Rezepte-Backend kennen weder Archivpfad noch Cookies oder Videodateien.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit


_TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}
_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}

# Das Archiv liegt hinter einem zugriffsgeschützten SMB-Share. Verzeichnisse
# und fertige Sidecars/Videos müssen deshalb für den Samba-Prozess lesbar sein,
# ohne Schreibrechte an andere lokale Benutzer zu vergeben.
_ARCHIVE_DIR_MODE = 0o750
_ARCHIVE_FILE_MODE = 0o640


def normalize_supported_url(value: str) -> Optional[str]:
    """Akzeptiert nur konkrete TikTok-/Instagram-Beiträge über HTTPS."""
    try:
        parsed = urlsplit((value or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None

    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path or "/"
    path_lower = path.lower()
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        if path == "/":
            return None
    elif host in _TIKTOK_HOSTS:
        if "/video/" not in path_lower and "/photo/" not in path_lower:
            return None
    elif host in _INSTAGRAM_HOSTS:
        if not any(marker in path_lower for marker in ("/reel/", "/p/", "/tv/")):
            return None
    else:
        return None
    return urlunsplit(("https", host, path, "", ""))


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def load_recipe_links(recipes_db: Path | str) -> list[tuple[int, str]]:
    """Liest aktive Rezept-IDs und Links aus einer SQLite-DB ohne Schreibzugriff."""
    source = Path(recipes_db).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Rezeptdatenbank nicht gefunden: {source}")

    uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as recipes:
        recipes.row_factory = sqlite3.Row
        columns = {
            str(row[1]) for row in recipes.execute("PRAGMA table_info(recipes)").fetchall()
        }
        if not {"id", "url"}.issubset(columns):
            raise ValueError("Rezeptdatenbank enthält keine kompatible recipes-Tabelle")
        where = "WHERE url IS NOT NULL"
        if "deleted_at" in columns:
            where += " AND deleted_at IS NULL"
        rows = recipes.execute(
            f"SELECT id, url FROM recipes {where} ORDER BY id"  # noqa: S608
        ).fetchall()
    return [(row["id"], row["url"]) for row in rows]


class ArchiveQueue:
    """Kleine SQLite-Queue mit atomarem Claim und begrenzten Wiederholungen."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.initialize()

    def initialize(self) -> None:
        with _connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_jobs (
                    recipe_id INTEGER PRIMARY KEY CHECK(recipe_id > 0),
                    url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'downloading', 'completed', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    archive_path TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipe_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def enqueue(self, recipe_id: int, url: str) -> dict[str, Any]:
        recipe_id = int(recipe_id)
        if recipe_id <= 0:
            raise ValueError("recipe_id muss größer als 0 sein")
        normalized = normalize_supported_url(url)
        if not normalized:
            raise ValueError("Nur konkrete HTTPS-Links von TikTok oder Instagram sind erlaubt")
        now = time.time()
        with _connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO archive_jobs
                    (recipe_id, url, status, attempts, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, 'queued', 0, 0, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                    url=excluded.url,
                    status=CASE
                        WHEN archive_jobs.url=excluded.url
                         AND archive_jobs.status='completed' THEN 'completed'
                        ELSE 'queued'
                    END,
                    attempts=CASE WHEN archive_jobs.url=excluded.url
                                  THEN archive_jobs.attempts ELSE 0 END,
                    next_attempt_at=0,
                    archive_path=CASE WHEN archive_jobs.url=excluded.url
                                      THEN archive_jobs.archive_path ELSE NULL END,
                    error=NULL,
                    updated_at=excluded.updated_at
                """,
                (recipe_id, normalized, now, now),
            )
            connection.execute(
                "INSERT INTO archive_events(recipe_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (recipe_id, "info", "Auftrag eingeplant", now),
            )
        return self.get(recipe_id)

    def sync_from_recipes_db(self, recipes_db: Path | str) -> dict[str, int]:
        """Übernimmt neue Plattform-Links aus der Rezept-DB in die private Queue.

        Die Quelldatenbank wird ausschließlich read-only geöffnet. Bereits
        bekannte Kombinationen aus Rezept-ID und normalisiertem Link bleiben
        unverändert; dadurch erzeugt der regelmäßige Abgleich weder doppelte
        Jobs noch wiederkehrende Ereignisse.
        """
        return self.sync_recipe_links(load_recipe_links(recipes_db))

    def sync_recipe_links(self, rows: list[tuple[int, str]]) -> dict[str, int]:
        """Gleicht bereits gelesene Rezept-Links idempotent mit der Queue ab."""
        with _connect(self.path) as connection:
            known = {
                int(row["recipe_id"]): str(row["url"])
                for row in connection.execute("SELECT recipe_id, url FROM archive_jobs")
            }

        result = {
            "seen": len(rows),
            "eligible": 0,
            "enqueued": 0,
            "unchanged": 0,
            "ignored": 0,
        }
        for row in rows:
            try:
                recipe_id = int(row[0])
            except (TypeError, ValueError):
                result["ignored"] += 1
                continue
            normalized = normalize_supported_url(str(row[1] or ""))
            if recipe_id <= 0 or not normalized:
                result["ignored"] += 1
                continue
            result["eligible"] += 1
            if known.get(recipe_id) == normalized:
                result["unchanged"] += 1
                continue
            self.enqueue(recipe_id, normalized)
            known[recipe_id] = normalized
            result["enqueued"] += 1
        return result

    def get(self, recipe_id: int) -> dict[str, Any]:
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM archive_jobs WHERE recipe_id=?", (int(recipe_id),)
            ).fetchone()
        if row is None:
            raise KeyError(recipe_id)
        return dict(row)

    def claim(self, *, max_attempts: int = 3, stale_after: int = 3600) -> Optional[dict[str, Any]]:
        now = time.time()
        stale_before = now - max(60, int(stale_after))
        with _connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            exhausted = connection.execute(
                """
                SELECT recipe_id FROM archive_jobs
                 WHERE status='downloading' AND updated_at < ? AND attempts >= ?
                """,
                (stale_before, int(max_attempts)),
            ).fetchall()
            connection.execute(
                """
                UPDATE archive_jobs
                   SET status='failed', error='Download abgebrochen; maximale Versuche erreicht',
                       next_attempt_at=0, updated_at=?
                 WHERE status='downloading' AND updated_at < ? AND attempts >= ?
                """,
                (now, stale_before, int(max_attempts)),
            )
            for stale in exhausted:
                connection.execute(
                    """
                    INSERT INTO archive_events(recipe_id, level, message, created_at)
                    VALUES (?, 'error', 'Festgefahrener Download endgültig fehlgeschlagen', ?)
                    """,
                    (int(stale["recipe_id"]), now),
                )
            connection.execute(
                """
                UPDATE archive_jobs
                   SET status='queued', error='Unterbrochenen Download erneut eingeplant',
                       next_attempt_at=0, updated_at=?
                 WHERE status='downloading' AND updated_at < ? AND attempts < ?
                """,
                (now, stale_before, int(max_attempts)),
            )
            row = connection.execute(
                """
                SELECT * FROM archive_jobs
                 WHERE status='queued' AND attempts < ? AND next_attempt_at <= ?
                 ORDER BY created_at, recipe_id LIMIT 1
                """,
                (int(max_attempts), now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            recipe_id = int(row["recipe_id"])
            connection.execute(
                """
                UPDATE archive_jobs
                   SET status='downloading', attempts=attempts+1, updated_at=?
                 WHERE recipe_id=? AND status='queued'
                """,
                (now, recipe_id),
            )
            claimed = connection.execute(
                "SELECT * FROM archive_jobs WHERE recipe_id=?", (recipe_id,)
            ).fetchone()
            connection.commit()
        return dict(claimed) if claimed else None

    def complete(self, recipe_id: int, archive_path: Path) -> None:
        with _connect(self.path) as connection:
            connection.execute(
                """
                UPDATE archive_jobs SET status='completed', archive_path=?, error=NULL,
                       next_attempt_at=0, updated_at=? WHERE recipe_id=?
                """,
                (str(archive_path), time.time(), int(recipe_id)),
            )
            connection.execute(
                "INSERT INTO archive_events(recipe_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (int(recipe_id), "info", "Archivierung abgeschlossen", time.time()),
            )

    def fail(self, recipe_id: int, error: str, *, max_attempts: int = 3) -> None:
        now = time.time()
        with _connect(self.path) as connection:
            row = connection.execute(
                "SELECT attempts FROM archive_jobs WHERE recipe_id=?", (int(recipe_id),)
            ).fetchone()
            attempts = int(row["attempts"]) if row else max_attempts
            retry = attempts < int(max_attempts)
            delay = min(3600, 60 * (2 ** max(0, attempts - 1))) if retry else 0
            connection.execute(
                """
                UPDATE archive_jobs SET status=?, error=?, next_attempt_at=?, updated_at=?
                 WHERE recipe_id=?
                """,
                (
                    "queued" if retry else "failed",
                    (error or "Unbekannter Fehler")[:2000],
                    now + delay if retry else 0,
                    now,
                    int(recipe_id),
                ),
            )
            connection.execute(
                "INSERT INTO archive_events(recipe_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (int(recipe_id), "error", (error or "Unbekannter Fehler")[:2000], now),
            )

    def counts(self) -> dict[str, int]:
        result = {name: 0 for name in ("queued", "downloading", "completed", "failed")}
        with _connect(self.path) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM archive_jobs GROUP BY status"
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        return result

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self.path) as connection:
            rows = connection.execute(
                "SELECT * FROM archive_events ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]


@dataclass(frozen=True)
class VideoArchiver:
    queue: ArchiveQueue
    archive_dir: Path
    ytdlp_path: str = "yt-dlp"
    cookies_file: Optional[Path] = None
    timeout_seconds: int = 900
    max_attempts: int = 3
    max_bytes: int = 1_000_000_000
    free_space_reserve_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        archive = Path(self.archive_dir).expanduser().resolve()
        object.__setattr__(self, "archive_dir", archive)
        if self.cookies_file is not None:
            cookies = Path(self.cookies_file).expanduser().resolve()
            if not cookies.is_file():
                raise ValueError(f"Cookie-Datei nicht gefunden: {cookies}")
            object.__setattr__(self, "cookies_file", cookies)
        executable = shutil.which(self.ytdlp_path) or (
            str(Path(self.ytdlp_path).resolve()) if Path(self.ytdlp_path).is_file() else None
        )
        if not executable:
            raise ValueError(f"yt-dlp nicht gefunden: {self.ytdlp_path}")
        object.__setattr__(self, "ytdlp_path", executable)
        if int(self.max_bytes) <= 0:
            raise ValueError("max_bytes muss größer als 0 sein")
        if int(self.free_space_reserve_bytes) < 0:
            raise ValueError("free_space_reserve_bytes darf nicht negativ sein")

    def process_one(self) -> Optional[dict[str, Any]]:
        job = self.queue.claim(max_attempts=self.max_attempts)
        if job is None:
            return None
        recipe_id = int(job["recipe_id"])
        try:
            path = self._archive(recipe_id, str(job["url"]))
            self.queue.complete(recipe_id, path)
            return {"recipe_id": recipe_id, "status": "completed", "path": str(path)}
        except Exception as exc:
            self.queue.fail(recipe_id, str(exc), max_attempts=self.max_attempts)
            current = self.queue.get(recipe_id)
            return {"recipe_id": recipe_id, "status": current["status"], "error": str(exc)}

    def _archive(self, recipe_id: int, url: str) -> Path:
        normalized = normalize_supported_url(url)
        if not normalized:
            raise ValueError("Ungültiger Plattform-Link in der Queue")
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.archive_dir.chmod(_ARCHIVE_DIR_MODE)
        except OSError:
            pass
        final_video = self.archive_dir / f"{recipe_id}.mp4"
        final_metadata = self.archive_dir / f"{recipe_id}.json"
        if final_video.exists() or final_metadata.exists():
            return self._accept_existing(recipe_id, normalized, final_video, final_metadata)

        # Während der MP4-Rekodierung können Quelldatei und Ziel gleichzeitig
        # existieren. Vor dem Start genug Platz für beides plus Reserve sichern.
        required_free = (2 * int(self.max_bytes)) + int(self.free_space_reserve_bytes)
        available_free = shutil.disk_usage(self.archive_dir).free
        if available_free < required_free:
            raise RuntimeError(
                "Zu wenig freier Speicher für Videoarchivierung "
                f"({available_free} verfügbar, {required_free} benötigt)"
            )

        work_root = self.archive_dir / ".work"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            work_root.chmod(0o700)
        except OSError:
            pass
        with tempfile.TemporaryDirectory(prefix=f"{recipe_id}-", dir=work_root) as temp_name:
            temp_dir = Path(temp_name)
            output_template = temp_dir / "download.%(ext)s"
            command = [
                self.ytdlp_path,
                normalized,
                "--no-playlist",
                "--no-overwrites",
                "--no-write-comments",
                "--no-write-info-json",
                "--no-write-thumbnail",
                "--recode-video",
                "mp4",
                "--max-filesize",
                str(int(self.max_bytes)),
                "--output",
                str(output_template),
            ]
            if self.cookies_file is not None:
                command.extend(["--cookies", str(self.cookies_file)])
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(60, int(self.timeout_seconds)),
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "yt-dlp fehlgeschlagen").strip()
                raise RuntimeError(detail[-1500:])
            candidates = [
                item for item in temp_dir.glob("download.*")
                if item.is_file() and item.suffix.lower() == ".mp4"
            ]
            if len(candidates) != 1:
                raise RuntimeError("yt-dlp hat keine eindeutige MP4-Datei erzeugt")

            source = candidates[0]
            if source.stat().st_size > int(self.max_bytes):
                raise RuntimeError("Videodatei überschreitet die konfigurierte Maximalgröße")
            digest = _sha256(source)
            metadata = {
                "recipe_id": recipe_id,
                "source_url": normalized,
                "filename": final_video.name,
                "bytes": source.stat().st_size,
                "sha256": digest,
                "downloaded_at": time.time(),
            }
            metadata_temp = temp_dir / "metadata.json"
            metadata_temp.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if final_video.exists() or final_metadata.exists():
                raise FileExistsError(f"Archivdatei für Rezept {recipe_id} wurde parallel angelegt")
            os.replace(source, final_video)
            try:
                os.replace(metadata_temp, final_metadata)
            except Exception:
                final_video.unlink(missing_ok=True)
                raise
            for item in (final_video, final_metadata):
                try:
                    item.chmod(_ARCHIVE_FILE_MODE)
                except OSError:
                    pass
        return final_video

    @staticmethod
    def _accept_existing(
        recipe_id: int,
        url: str,
        video_path: Path,
        metadata_path: Path,
    ) -> Path:
        if not video_path.is_file() or not metadata_path.is_file():
            raise FileExistsError(f"Unvollständiger Archivkonflikt für Rezept {recipe_id}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError(f"Metadatenkonflikt für Rezept {recipe_id}") from exc
        if metadata.get("recipe_id") != recipe_id or metadata.get("source_url") != url:
            raise FileExistsError(f"Rezept-ID {recipe_id} ist bereits einem anderen Link zugeordnet")
        if metadata.get("sha256") != _sha256(video_path):
            raise FileExistsError(f"Prüfsumme für Rezept {recipe_id} stimmt nicht")
        return video_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
