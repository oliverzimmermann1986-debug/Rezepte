"""Indexer + Background-Extraction für den Recipe-Browser.

Zwei separate Aufgaben:

1. **FS→DB-Sync** (`sync_filesystem`):
   Scannt `/mnt/rezepte/*/*/<Name>/` und legt für jeden Ordner mit info.json +
   description.txt eine `recipes`-Zeile an. Idempotent — bestehende Einträge
   werden upserted, keine neu-Extraktion. Source-of-Truth bleibt das Filesystem.

   Aufruf: passiert bei jedem `/api/recipes` GET (lazy), und/oder explizit
   per `/api/recipes/sync` Button im Frontend.

2. **Background-Extraction** (`run_extraction_loop`):
   Pickt sich pro Schleifen-Iteration N Rezepte mit `ingredients_status='pending'`,
   schickt deren `description` durch den AI-Analyzer, schreibt Zutaten in
   `recipe_ingredients`, setzt Status auf 'ok' (oder 'error' bei Fehler).

   Läuft als Thread, gestartet beim ersten `/api/recipes` mit pending-Bestand
   (siehe ensure_extraction_running). Stoppt selbständig wenn keine pending
   mehr da sind.

Wir verwenden KEINEN systemd-Timer dafür — der Job ist UI-getrieben (User
öffnet Browser → Job läuft an), und stoppt von selbst.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..core.analyzer import build_analyzer
from ..config_store import get_config
from ..db import Database, get_db
from .canonical import canonical_name as _canonical
from .units import normalize_unit
from .image_cache import ensure_pdf_first_page, ensure_thumbnail
from .video_recipe_extract import analyze_recipe_with_video_fallback

logger = logging.getLogger(__name__)

# Globaler Singleton für den Extraction-Worker.
_worker_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()


# ════════════════════════════════════════════════════════════════════════
# FS → DB Sync
# ════════════════════════════════════════════════════════════════════════

def sync_filesystem(db: Optional[Database] = None) -> dict:
    """Scannt die Recipe-Roots und legt fehlende `recipes`-Einträge an.

    Returns: {"scanned": n, "added": n, "updated": n, "skipped": n}
    """
    db = db or get_db()
    cfg = get_config()
    recipe_root = Path(cfg.get("paths", "recipe_dir", default="/mnt/rezepte"))
    if not recipe_root.exists():
        logger.warning(f"sync_filesystem: recipe_dir existiert nicht: {recipe_root}")
        return {"scanned": 0, "added": 0, "updated": 0, "skipped": 0, "error": "recipe_dir missing"}

    counters = {"scanned": 0, "added": 0, "updated": 0, "skipped": 0, "errors": 0}

    # Sync-Errors-Tabelle leeren — alte Konflikte könnten vom User schon
    # gelöst worden sein. Wir tracken nur den AKTUELLEN FS-Zustand.
    try:
        db.sync_errors_clear()
    except Exception as e:
        logger.warning(f"sync_errors_clear: {e}")

    # Layout: /mnt/rezepte/<Typ>/<Kategorie>/<Name>/{name.mp4, name.jpg, info.json, description.txt}
    # Wir gehen 3 Ebenen tief.
    for type_dir in _safe_iterdir(recipe_root):
        if not type_dir.is_dir() or type_dir.name.startswith("."):
            continue
        type_name = type_dir.name
        for cat_dir in _safe_iterdir(type_dir):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            cat_name = cat_dir.name
            for recipe_dir in _safe_iterdir(cat_dir):
                if not recipe_dir.is_dir() or recipe_dir.name.startswith("."):
                    continue
                counters["scanned"] += 1
                # try/except um _index_one — sonst bricht ein einzelner Crash
                # (z.B. UNIQUE constraint auf folder_path bei manuell duplizierten
                # Foldern, oder defekte info.json) den GANZEN Sync ab und nur
                # die ersten paar Rezepte landen in der DB. Mit try/except wird
                # der Folder geskippt, der Sync läuft weiter.
                try:
                    result = _index_one(db, recipe_dir, type_name, cat_name)
                    counters[result] = counters.get(result, 0) + 1
                except Exception as e:
                    counters["errors"] += 1
                    error_msg = f"{type(e).__name__}: {e}"
                    logger.warning(
                        f"sync_filesystem: _index_one({recipe_dir}) crashed: {error_msg}"
                    )
                    # In sync_errors persistieren damit der Audit das zeigen kann.
                    # Bei UNIQUE-URL-Konflikt: bestehenden Eintrag finden für
                    # cross-reference im UI.
                    error_type = "other"
                    conflict_id = None
                    if "recipes.url" in str(e):
                        error_type = "unique_url"
                        # URL aus dem Folder lesen um den existing zu finden
                        try:
                            info = json.loads((recipe_dir / "info.json").read_text(encoding="utf-8"))
                            existing_url = info.get("url")
                            if existing_url:
                                with db.conn() as c:
                                    row = c.execute(
                                        "SELECT id FROM recipes WHERE url=?",
                                        (existing_url,),
                                    ).fetchone()
                                    if row:
                                        conflict_id = int(row["id"])
                        except Exception:
                            pass
                    elif "recipes.folder_path" in str(e):
                        error_type = "unique_folder"
                    try:
                        db.sync_error_record(
                            str(recipe_dir), error_type, error_msg, conflict_id
                        )
                    except Exception as e2:
                        logger.warning(f"sync_error_record failed: {e2}")

    logger.info(f"sync_filesystem: {counters}")
    return counters


def _safe_iterdir_checked(p: Path):
    """Sicheres ``iterdir`` ohne Symlink-Folgen.

    Der Rezeptbaum ist ein Datenimport-Grenzbereich. Ein dort platzierter
    Symlink darf weder beim Indexieren externe Verzeichnisse betreten noch
    später eine externe Datei als Rezeptmedium registrieren.
    """
    try:
        items = []
        for child in p.iterdir():
            if child.is_symlink():
                logger.warning("Indexer überspringt Symlink: %s", child)
                continue
            items.append(child)
        return items, True
    except (PermissionError, FileNotFoundError, OSError) as e:
        logger.warning(f"iterdir({p}): {e}")
        return [], False


def _safe_iterdir(p: Path):
    items, _ok = _safe_iterdir_checked(p)
    return items


def _try_media_extract(folder: Path, analyzer) -> Optional[str]:
    """Sucht PDF/Bild im Folder und extrahiert eine Description daraus.
    Wird aus dem Worker aufgerufen wenn description.txt + .txt-Fallback
    leer waren. PDF zuerst (text-extract gratis), dann Bilder (Vision).

    Returns: extrahierter Text oder None.
    """
    media_candidates = [
        f for f in _safe_iterdir(folder)
        if f.is_file()
        and f.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png", ".webp")
    ]
    # PDF zuerst probieren (text-extract gratis, falls Text-Layer da)
    media_candidates.sort(key=lambda f: 0 if f.suffix.lower() == ".pdf" else 1)

    for media in media_candidates:
        try:
            extracted = analyzer.extract_description_from_media(media)
        except Exception as e:
            logger.warning(f"Media-Extract crashed bei {media}: {e}")
            continue
        if extracted and len(extracted) >= 20:
            return extracted
    return None


def _needs_media_extract(recipe: dict, description: str) -> bool:
    """Erkennt Quellen, deren Beschreibung kein Rezeptinhalt sein kann.

    Mail-Anhänge erhalten beim Import eine technische Platzhalterbeschreibung.
    Diese kann länger als die allgemeine Mindestlänge sein, darf den eigentlichen
    Bild-/PDF-Scan aber nicht verhindern.
    """
    source_url = str(recipe.get("url") or "").strip().lower()
    return len(description.strip()) < 20 or source_url.startswith("mail-attachment://")


def _pdf_thumb(folder: Path) -> Optional[str]:
    """Rendert Seite 1 des ersten PDFs im Ordner nach thumb.jpg (pdftoppm).
    Für PDF-Rezepte ohne Bild. Returns 'thumb.jpg' bei Erfolg, sonst None.
    Nicht-fatal: fehlendes pdftoppm/Fehler → None, Sync läuft weiter.
    Einmalig pro Rezept — sobald thumb.jpg existiert, greift schon die
    Bild-Erkennung in _index_one und _pdf_thumb wird nicht mehr aufgerufen."""
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        return None
    target = folder / "thumb.jpg"
    try:
        ensure_pdf_first_page(pdfs[0], target, dpi=150, timeout=60)
    except FileNotFoundError:
        logger.warning("pdftoppm nicht installiert — PDF-Thumbnail übersprungen")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"pdftoppm Timeout: {folder.name}")
        return None
    except (OSError, RuntimeError) as exc:
        logger.warning(f"pdftoppm fehlgeschlagen ({folder.name}): {str(exc)[:120]}")
        return None
    if target.exists():
        logger.info(f"PDF-Thumbnail erzeugt: {folder.name}")
        return target.name
    return None


def _is_resized_thumbnail_cache(name: str) -> bool:
    normalized = str(name or "").casefold()
    return (
        normalized.startswith("thumb-w")
        and normalized.endswith(".jpg")
        and normalized[len("thumb-w"):-len(".jpg")].isdigit()
    )


def _select_recipe_thumbnail(
    folder_items: list[Path],
    existing_filename: Optional[str],
) -> Optional[str]:
    """Wählt das aktive Bild deterministisch und ignoriert Resize-Caches.

    Ein FS-Sync darf ``thumb-generated.jpg`` nicht durch ein zufällig zuletzt
    gelistetes ``thumb-w400.jpg`` ersetzen. Ein bewusst wiederhergestelltes
    oder hochgeladenes aktives Bild aus der DB bleibt dagegen vorrangig.
    """
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    candidates = {
        item.name: item
        for item in folder_items
        if item.is_file()
        and not item.name.startswith(".")
        and item.suffix.casefold() in image_suffixes
        and not _is_resized_thumbnail_cache(item.name)
    }
    existing_name = Path(str(existing_filename or "")).name
    if existing_name and existing_name in candidates:
        return existing_name

    by_casefold = {name.casefold(): name for name in candidates}
    for preferred in (
        "thumb-generated.jpg",
        "thumb.jpg",
        "thumb.jpeg",
        "thumb.png",
        "thumb.webp",
        "pdf-page1.jpg",
    ):
        if preferred in by_casefold:
            return by_casefold[preferred]

    folder_names = {
        f"{item.parent.name}{suffix}".casefold()
        for item in candidates.values()
        for suffix in image_suffixes
    }
    named_like_folder = sorted(
        name for name in candidates if name.casefold() in folder_names
    )
    if named_like_folder:
        return named_like_folder[0]
    return sorted(candidates, key=str.casefold)[0] if candidates else None


def _index_one(db: Database, folder: Path, type_name: str, cat_name: str) -> str:
    """Legt EINEN Recipe-Ordner als DB-Zeile an oder aktualisiert ihn.
    Returns: 'added' | 'updated' | 'skipped'."""
    info_file = folder / "info.json"
    desc_file = folder / "description.txt"

    preserve_existing = set()
    info = {}
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
            # Alte Rezeptordner besitzen häufig eine info.json ohne URL-Feld.
            # Ein fehlender Key bedeutet "unbekannt" und darf eine bereits in
            # der DB bekannte Quell-URL nicht löschen. Ein explizites
            # ``"url": null`` bleibt dagegen wirksam (z. B. bei Varianten).
            if "url" not in info:
                preserve_existing.add("url")
        except Exception as e:
            logger.warning(f"info.json kaputt in {folder}: {e}")
            preserve_existing.update(("name", "url"))
    else:
        preserve_existing.add("url")

    description = None
    if desc_file.exists():
        try:
            description = desc_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"description.txt unlesbar in {folder}: {e}")
            preserve_existing.add("description")
    else:
        # Das Dateisystem ist für dieses Feld nur dann autoritativ, wenn ein
        # Sidecar tatsächlich existiert. Ein fehlendes description.txt darf
        # keine zuvor in der DB gespeicherte Beschreibung löschen.
        preserve_existing.add("description")

    folder_items, folder_scan_ok = _safe_iterdir_checked(folder)
    if not folder_scan_ok:
        preserve_existing.update(("description", "thumb_filename", "video_filename"))

    # Fallback: wenn description.txt fehlt oder leer ist, prüfe ob es eine
    # andere .txt-Datei im Folder gibt. Häufige Fälle:
    #   - <folder-name>.txt (Brokkoli_mit_knoblauch.txt) — alte Scrapes vor
    #     Standardisierung des Filenamens, oder Case-Mismatch
    #   - caption.txt — von älteren Scraper-Versionen
    #   - irgendwas.txt — manuell hineingelegt
    # Wir nehmen die größte verfügbare .txt (höchster Informationsgehalt).
    # description_original.txt wird ausgelassen — das ist unser auto-translate-
    # Backup vom Original, der deutsche Pfad steckt schon in description.txt.
    if not description:
        candidates = [
            f for f in folder_items
            if f.is_file() and f.suffix.lower() == ".txt"
            and f.name not in ("description.txt", "description_original.txt")
        ]
        if candidates:
            best = max(candidates, key=lambda f: f.stat().st_size)
            try:
                description = best.read_text(encoding="utf-8").strip()
                if description:
                    preserve_existing.discard("description")
                logger.info(f"Description-Fallback in {folder.name}: {best.name}")
            except Exception as e:
                logger.warning(f"Fallback-Text {best} unlesbar: {e}")

    # ACHTUNG: Media-Extract (PDF/Bild → Vision) passiert NICHT mehr hier
    # im Indexer, sondern im Worker (siehe _extract_for_recipe). Grund:
    # Vision-Calls dauern 5-10s und blockierten den Sync — bei 100+ Foldern
    # ist das Frontend ewig gefroren. Indexer bleibt jetzt FS-only/.txt-only
    # und ist Sekunden statt Minuten durch.

    name = info.get("name") or folder.name
    url = info.get("url")
    # processed_at aus info.json wenn vorhanden, sonst mtime des Ordners
    source_added_at = info.get("processed_at")
    if source_added_at is None:
        try:
            source_added_at = folder.stat().st_mtime
        except OSError:
            source_added_at = None

    existed = db.recipe_get_by_folder(str(folder))

    # Das aktive Original oder generierte Bild bleibt stabil; abgeleitete
    # thumb-w*-Dateien sind reine HTTP-Caches und niemals eine Bildquelle.
    thumb = _select_recipe_thumbnail(
        folder_items,
        existed.get("thumb_filename") if existed else None,
    )
    video_candidates = sorted(
        item.name
        for item in folder_items
        if item.is_file() and item.suffix.casefold() in (".mp4", ".webm", ".mov", ".mkv")
    )
    existing_video = Path(str(existed.get("video_filename") or "")).name if existed else ""
    video = existing_video if existing_video in video_candidates else (
        video_candidates[0] if video_candidates else None
    )

    # PDF-Rezepte ohne Bild: Seite 1 nach thumb.jpg rendern (einmalig).
    if thumb is None:
        thumb = _pdf_thumb(folder)

    # Kartenbilder im ohnehin asynchronen FS-Sync vorberechnen. Der Webrequest
    # muss dadurch beim ersten Scrollen keinen Bildprozess mehr starten.
    if thumb:
        try:
            source_thumb = folder / thumb
            ensure_thumbnail(source_thumb, 400)
            ensure_thumbnail(source_thumb, 800)
        except Exception as e:
            logger.debug("Thumbnail-Prewarm für %s fehlgeschlagen: %s", folder.name, e)

    db.recipe_upsert(
        url=url,
        name=name,
        type=type_name,
        category=cat_name,
        folder_path=str(folder),
        description=description,
        thumb_filename=thumb,
        video_filename=video,
        source_added_at=source_added_at,
        preserve_existing=preserve_existing,
    )
    return "updated" if existed else "added"


# ════════════════════════════════════════════════════════════════════════
# Background Extraction Worker
# ════════════════════════════════════════════════════════════════════════

def ensure_extraction_running() -> bool:
    """Startet den Worker-Thread, falls er noch nicht läuft UND es pending
    Rezepte gibt. Wird vom /api/recipes-Endpoint aufgerufen — idempotent."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return False
        db = get_db()
        recovered = db.recipes_requeue_stale_extractions()
        if recovered:
            logger.warning(
                "Extraction-Worker: %d verwaiste Claims erneut eingeplant",
                recovered,
            )
        stats = db.recipes_extraction_stats()
        if not stats.get("pending"):
            return False  # nichts zu tun
        _worker_stop.clear()
        _worker_thread = threading.Thread(target=_extraction_loop, name="recipe-extractor", daemon=True)
        _worker_thread.start()
        logger.info(f"Recipe-Extraction-Worker gestartet ({stats.get('pending', 0)} pending)")
        return True


def stop_extraction() -> None:
    """Signalisiert dem Worker zu stoppen. Aufruf bei Shutdown."""
    _worker_stop.set()


def is_extraction_running() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


def _extraction_loop() -> None:
    """Worker-Hauptschleife. Stoppt von selbst wenn keine pending mehr sind."""
    db = get_db()
    cfg = get_config()
    ai_cfg = cfg.get("ai", default={}) or {}

    try:
        analyzer = build_analyzer(ai_cfg)
    except Exception as e:
        logger.error(f"Extraction-Worker: kann Analyzer nicht bauen: {e}")
        return

    # Concurrency: 3 KI-Calls gleichzeitig. SQLite ist thread-safe weil
    # check_same_thread=False (siehe db.py L216) und jeder Aufruf eine neue
    # Connection mit context-manager nutzt. OpenAI-API hat keine kritischen
    # Rate-Limits bei diesen Volumen. _extract_for_recipe raised NIE
    # (try/except inside) — Future.result() ist daher safe.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    batch_size = 9   # 3 Worker × 3 Items pro Batch — Batches schnell durch
    max_workers = 3
    idle_loops = 0
    claim_owner = f"{os.getpid()}:{uuid.uuid4().hex}"
    while not _worker_stop.is_set():
        batch = db.recipes_claim_extraction(limit=batch_size, owner=claim_owner)
        if not batch:
            # 3x in Folge leer = wirklich nichts mehr, beenden
            idle_loops += 1
            if idle_loops >= 3:
                logger.info("Extraction-Worker: keine pending Rezepte mehr, beende Loop")
                return
            time.sleep(2)
            continue
        idle_loops = 0
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="extract") as ex:
            futures = {
                ex.submit(_extract_for_recipe, db, analyzer, r, claim_owner): r
                for r in batch
            }
            for f in as_completed(futures):
                if _worker_stop.is_set():
                    # Restliche Futures nicht abbrechen — laufen aus, Loop endet danach
                    break
                try:
                    f.result()
                except Exception as e:
                    # _extract_for_recipe sollte selbst nichts raisen, aber falls
                    # doch (z.B. OOM): nur loggen, anderen futures weiter laufen lassen
                    logger.exception(f"_extract_for_recipe future failed: {e}")
                    failed_recipe = futures[f]
                    db.recipe_set_extraction_result(
                        int(failed_recipe["id"]),
                        status="error",
                        ingredients=[],
                        claim_owner=claim_owner,
                    )


def _extract_for_recipe(
    db: Database,
    analyzer,
    recipe: dict,
    claim_owner: str,
) -> None:
    """Holt Zutaten für EIN Rezept und schreibt sie ins DB. Niemals raised —
    Fehler werden auf 'error'-Status gesetzt, sodass die Schleife weiterläuft.

    Wenn die Beschreibung nicht-deutsch ist, wird sie vor der Zutaten-
    Extraktion übersetzt (Original kommt parallel als description_original.txt
    in den Rezept-Ordner, DB-Feld wird auf deutsche Variante geupdatet).
    Das ist für Bestands-Rezepte die einzige Stelle, an der eine retro-
    Übersetzung passiert — der Scraper macht das beim Save selbst.
    """
    rid = recipe["id"]
    desc = recipe.get("description") or ""

    # Wenn description leer/zu kurz: erst Media-Extract aus dem Folder versuchen
    # (PDF text-extract gratis, Bild via OpenAI Vision). Das war früher im
    # Indexer eingebaut, blockierte aber den Sync bei vielen Bilder-Rezepten.
    # Jetzt hier im Worker — der läuft asynchron und macht jeweils 1 Rezept,
    # Frontend bleibt responsive.
    if _needs_media_extract(recipe, desc):
        from pathlib import Path as _P
        folder = _P(recipe.get("folder_path") or "")
        if folder.exists():
            extracted = _try_media_extract(folder, analyzer)
            if extracted and len(extracted) >= 20:
                desc = extracted
                # Persistent als description.txt + DB-Feld updaten
                try:
                    (folder / "description.txt").write_text(desc, encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Rezept #{rid}: description.txt nicht schreibbar: {e}")
                with db.conn() as c:
                    c.execute("UPDATE recipes SET description=? WHERE id=?", (desc, rid))
                logger.info(f"Rezept #{rid}: Media-Extract erfolgreich ({len(desc)} chars)")

    # Pre-translate für Bestands-Rezepte: italienische/englische Captions
    # werden hier nachträglich nach Deutsch umgesetzt, damit die Zutaten-
    # Extraktion auf dem konsistenten deutschen Text läuft.
    translated = None
    if len(desc.strip()) >= 20:
        try:
            translated = analyzer.translate_to_german(desc)
        except Exception as e:
            logger.warning(f"Rezept #{rid}: Translate-Call failed (behalte Original): {e}")

    if translated:
        desc = translated
        # Beschreibungs-File im Rezept-Ordner aktualisieren — Original sichern
        from pathlib import Path as _P
        folder = _P(recipe.get("folder_path") or "")
        if folder.exists():
            orig_file = folder / "description_original.txt"
            desc_file = folder / "description.txt"
            try:
                if desc_file.exists() and not orig_file.exists():
                    orig_file.write_text(desc_file.read_text(encoding="utf-8"), encoding="utf-8")
                desc_file.write_text(desc, encoding="utf-8")
            except Exception as e:
                logger.warning(f"Rezept #{rid}: Konnte description-Files nicht updaten: {e}")
        # DB-Feld description auf die deutsche Variante setzen
        with db.conn() as c:
            c.execute("UPDATE recipes SET description=? WHERE id=?", (desc, rid))
        logger.info(f"Rezept #{rid}: Caption nach DE übersetzt ({len(translated)} chars)")

    # Existing-Stammdaten als KI-Hint mitgeben — verhindert dass die KI für
    # bekannte Tags ('pasta') neue Varianten ('Pasta', 'Pasta-Gerichte') erfindet
    # und für bekannte Zutaten ('Tomate') neue canonical-Formen ('Tomaten',
    # 'tomato'). Liste wird vom Caller pro Rezept neu gelesen — bei <1000
    # Tags/canonical ist das vernachlässigbar. Cached wir nicht, weil die
    # Liste sich während der Extraktion erweitert (frisch extrahierte
    # canonicals sollen den nächsten Calls helfen).
    try:
        with db.conn() as c:
            tag_rows = c.execute("SELECT name FROM tags").fetchall()
            existing_tags = [r[0] for r in tag_rows]
            existing_canonical = db.ingredient_name_hints()
    except Exception as e:
        logger.warning(f"Rezept #{rid}: existing-Stammdaten-Lookup failed: {e}")
        existing_tags, existing_canonical = [], []

    current_ingredients = db.recipe_ingredients_get(rid)
    current_steps = db.recipe_steps_get(rid)
    current_tags = db.recipe_tags_get(rid)
    ai_cfg = get_config().get("ai", default={}) or {}
    recipe_root = Path(
        get_config().get("paths", "recipe_dir", default="/mnt/rezepte")
    )

    try:
        video_result = analyze_recipe_with_video_fallback(
            analyzer,
            recipe,
            recipe_root=recipe_root,
            ai_config=ai_cfg,
            existing_tags=existing_tags,
            existing_canonical=existing_canonical,
            description=desc,
        )
        content = video_result.content
    except Exception as e:
        logger.warning(f"Rezept #{rid}: KI-/Video-Call failed: {e}")
        db.recipe_set_extraction_result(
            rid, status="error", ingredients=[], claim_owner=claim_owner
        )
        return

    # KI hat None returnt = _call failed (timeout/length-trunc/etc).
    # Lieber als error markieren damit der Audit-Tab das sichtbar macht.
    if content is None:
        status = "skipped" if len(desc.strip()) < 20 and not video_result.used_video else "error"
        logger.warning(
            "Rezept #%s: kein verwertbares KI-Ergebnis (%s)",
            rid,
            video_result.reason,
        )
        db.recipe_set_extraction_result(
            rid, status=status, ingredients=[], claim_owner=claim_owner
        )
        return

    # canonical_name + unit-normalize beim Insert mit dranhängen
    extracted_ingredients = []
    for it in (content.get("ingredients") or []):
        extracted_ingredients.append({
            "name": it.get("name") or "",
            "canonical_name": _canonical(it.get("name") or ""),
            "amount": it.get("amount"),
            "unit": normalize_unit(it.get("unit")),
            "raw": it.get("raw"),
        })

    # Sicherheits-Netz: bei langer Description aber 0 Zutaten ist ziemlich
    # sicher etwas schiefgelaufen (KI hat verweigert oder nichts erkannt).
    # Status auf 'error' damit Audit das aufzeigt und User es nochmal triggern
    # kann. Vorher wurde status='ok' gesetzt → Rezept fiel durchs Raster.
    prepared = current_ingredients or extracted_ingredients
    steps = current_steps or (content.get("steps") or [])
    servings = recipe.get("servings") or content.get("servings")

    if not prepared and (len(desc.strip()) > 100 or video_result.used_video):
        logger.warning(
            f"Rezept #{rid}: 0 Zutaten extrahiert obwohl description "
            f"{len(desc)} chars hat — markiere als 'error' statt 'ok'"
        )
        db.recipe_set_extraction_result(
            rid, status="error", ingredients=[], claim_owner=claim_owner
        )
        return

    # Auto-Tags: KI-Tags (stilistisch) + Regel-Tags (Diät/Allergene)
    # Vereinigt unter einer Tabelle, mit auto=1 markiert. User-Tags
    # (auto=0) bleiben dabei unangetastet.
    from .auto_tags import compute_diet_tags
    ki_tags = content.get("tags") or []
    diet_tags = compute_diet_tags(
        [p["canonical_name"] for p in prepared],
        allergen_info=content.get("allergen_info"),
    )
    previous_auto_tags = [t["name"] for t in current_tags if t.get("auto")]
    all_auto_tags = sorted(set(previous_auto_tags) | set(ki_tags) | set(diet_tags))
    final_status = "ok" if prepared and steps else "error"
    applied = db.recipe_apply_extraction_result(
        rid,
        ingredients=extracted_ingredients,
        steps=content.get("steps") or [],
        servings=servings,
        auto_tags=all_auto_tags,
        claim_owner=claim_owner,
        status=final_status,
        replace_ingredients=not bool(current_ingredients),
        replace_steps=not bool(current_steps),
        replace_servings=not bool(recipe.get("servings")),
    )
    if not applied:
        logger.warning(
            "Rezept #%s: Claim ging während der Extraktion verloren; "
            "veraltetes Ergebnis wird verworfen",
            rid,
        )
        return

    # Nährwerte berechnen — nur wenn genug Zutaten da sind (>=3, sonst meist
    # KI-Halbextrakt). +1 KI-Call, ~$0.0005. Skip wenn schon berechnet
    # (force-recompute geht über den dedicated Endpoint).
    nutrition_msg = ""
    nutrition_owner = f"nutrition-extract:{rid}:{time.time_ns()}"
    try:
        if len(prepared) >= 3 and not recipe.get("calories_per_serving"):
            if not db.recipe_claim_nutrition(rid, nutrition_owner):
                raise RuntimeError("Nährwert-Claim konnte nicht übernommen werden")
            nutrition_ingredients = db.recipe_ingredients_get(rid)
            nutr = analyzer.compute_nutrition(nutrition_ingredients, servings)
            if nutr:
                db.recipe_set_nutrition(
                    rid, nutr["calories"], nutr["protein_g"],
                    nutr["carbs_g"], nutr["fat_g"],
                    claim_owner=nutrition_owner,
                )
                nutrition_msg = f", ~{nutr['calories']} kcal/Portion"
    except Exception as e:
        logger.warning(f"Rezept #{rid}: compute_nutrition failed: {e}")
    finally:
        db.recipe_release_nutrition_claim(rid, nutrition_owner)

    logger.info(
        f"Rezept #{rid} '{recipe.get('name')}': "
        f"{len(prepared)} Zutaten, {len(steps)} Schritte, status={final_status}, "
        f"servings={servings or '?'}, "
        f"{len(all_auto_tags)} Auto-Tags ({len(ki_tags)} KI + {len(diet_tags)} Diät)"
        f", Video={video_result.reason}, Frames={video_result.frame_text_count}, "
        f"Audio={'ja' if video_result.transcribed else 'nein'}{nutrition_msg}"
    )
