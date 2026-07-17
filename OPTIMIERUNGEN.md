# Umgesetzte Optimierungen

Stand: 17. Juli 2026

Dieses Update konzentriert sich auf Geschwindigkeit, Stabilität, Bedienbarkeit und Wartbarkeit. Sicherheitsänderungen waren ausdrücklich nicht Teil der Priorisierung.

## 1. Rezeptbibliothek und Dateisynchronisierung

- Rezeptlisten werden sofort aus SQLite ausgeliefert und lösen keinen blockierenden HDD-/NAS-Komplettscan mehr aus.
- Die Dateisynchronisierung läuft dedupliziert im Hintergrund.
- Mehrere gleichzeitige Sync-Anforderungen starten nur einen Lauf.
- Neuer Status-Endpunkt für laufende, abgeschlossene und fehlgeschlagene Synchronisierungen.
- Der initiale Sync wird beim Anwendungsstart eingeplant.
- Beim Herunterfahren wartet die Anwendung kurz auf einen laufenden Sync.
- Die Oberfläche zeigt den Sync-Status und fragt ihn mit adaptivem Polling ab.

Relevante Dateien:

- `app/recipes/sync_manager.py`
- `app/recipes/indexer.py`
- `app/routes/api_recipes.py`
- `app/main.py`

## 2. Suche, Filter und Datenbankzugriff

- Suchrelevanz wird jetzt vor `LIMIT` und `OFFSET` direkt in SQLite berechnet.
- Relevante Treffer bleiben damit auch bei Pagination korrekt sortiert.
- Gemeinsame Filterlogik wurde aus `db.py` in einen Query-Builder ausgelagert.
- Facetten werden für fünf Sekunden in einem begrenzten TTL-Cache gehalten.
- Gelöschte Datensätze werden bei Typ- und Kategorie-Facetten ausgeschlossen.

Relevante Dateien:

- `app/recipes/query_builder.py`
- `app/core/ttl_cache.py`
- `app/db.py`
- `app/routes/api_recipes.py`

## 3. Bilder und Thumbnails

- Thumbnail-Erzeugung verwendet eine zentrale Pillow-basierte Cache-Pipeline.
- Pro Bild/Größe verhindert ein Lock parallele Doppelberechnungen.
- 400- und 800-Pixel-Varianten werden beim Hintergrund-Sync vorbereitet.
- Bild-Uploads werden gestreamt, tatsächlich dekodiert, normalisiert und atomar ersetzt.
- Ein fehlgeschlagener Upload löscht das vorhandene Bild nicht mehr.
- Veraltete Varianten werden nach erfolgreichem Austausch invalidiert und neu erzeugt.

Relevante Dateien:

- `app/recipes/image_cache.py`
- `app/recipes/indexer.py`
- `app/routes/api_recipes.py`

## 4. Persistente Hintergrundaufgaben

- Share-Importe laufen nicht mehr in losen, kurzlebigen Request-Threads.
- Neue persistente SQLite-Warteschlange mit Status, Ergebnis und Fehlermeldung.
- Unterbrochene Aufgaben werden nach einem Neustart erneut eingereiht.
- Ein kontrollierter Einzel-Worker startet und stoppt mit der Anwendung.
- Neue API-Endpunkte listen Aufgaben und liefern ihren Status.

Relevante Dateien:

- `app/jobs/task_queue.py`
- `app/routes/api_share.py`
- `app/routes/api_jobs.py`
- `app/db.py`

## 5. Frontend-Performance und Stabilität

- Admin-Status, Jobs, Statistiken, HDD-Status und SSE werden nur noch auf der Admin-Seite aktiviert.
- Polling pausiert bei unsichtbarem Tab und erhöht sein Intervall bei Inaktivität.
- Rezept-, Facetten- und Infinite-Scroll-Anfragen verwenden `AbortController`.
- Veraltete Antworten dürfen neuere Filter- oder Suchergebnisse nicht mehr überschreiben.
- Infinite Scroll verhindert doppelte IDs und getrenntes Nachladen blockiert nicht die gesamte Rezeptansicht.
- Die Suche verwendet korrektes Debouncing.
- Unnötige doppelte Facettenabfragen wurden entfernt.
- Skeleton-Karten verbessern den wahrgenommenen Seitenaufbau.

Relevante Dateien:

- `app/static/runtime.js`
- `app/static/app.js`
- `app/static/index.html`
- `app/static/rezepte.css`

## 6. GUI und Barrierefreiheit

- Gemeinsame Dialog-Laufzeit ergänzt automatisch Dialogrollen und modale Attribute.
- Escape-Taste, Fokusfalle und Fokuswiederherstellung für modale Ansichten.
- Schließen-Elemente können über `data-dialog-close` erkannt werden.
- Toastmeldungen besitzen eine Live-Region.
- Der Detaildialog hat eine beschriftete Schließen-Schaltfläche.
- Laufende Sync- und Nachladevorgänge werden getrennt dargestellt.

## 7. Start, Shutdown und Codequalität

- Migrationen, Cleanup und Benutzer-Migration laufen im FastAPI-Lifespan statt beim Modulimport.
- Doppelte `bulk-skip`-Route entfernt.
- Fehlende Typimporte ergänzt.
- Globale Filter-SQL-Erzeugung aus der großen Datenbankklasse ausgelagert.
- Background-Worker fährt kontrolliert herunter.

## 8. Qualitätssicherung

Neu ergänzt:

- GitHub-Actions-Workflow für Compile-Check, Ruff, JavaScript-Syntax und Tests.
- Ruff-Konfiguration für kritische Pythonfehler.
- Coverage-Konfiguration.
- Tests für Suchpagination, Sync-Deduplizierung, Task-Recovery, Thumbnail-Cache und Frontend-Runtime.

Final geprüft:

- `79 passed`
- Python `compileall`: erfolgreich
- Ruff: erfolgreich
- JavaScript-Syntaxprüfung: erfolgreich
- Shell-Syntaxprüfung: erfolgreich
- Gesamtabdeckung beim letzten Lauf: ungefähr 32 %

## Neue oder geänderte API-Endpunkte

- `POST /api/recipes/sync` – plant einen Hintergrund-Sync ein und antwortet mit HTTP 202.
- `GET /api/recipes/sync/status` – liefert Zustand und Ergebnis des Syncs.
- `GET /api/jobs/tasks/list` – listet persistente Hintergrundaufgaben.
- `GET /api/jobs/tasks/{task_id}` – liefert eine einzelne Hintergrundaufgabe.

## Bewusst nicht als Komplettumbau umgesetzt

Die Anwendung bleibt kompatibel zur bisherigen Oberfläche und Datenbank. Deshalb wurden einige sehr große Umbauten nicht in einem einzigen risikoreichen Schritt durchgeführt:

- `app.js`, `index.html`, `rezepte.css` und `db.py` sind noch nicht vollständig in fachliche Komponenten zerlegt. Mit `runtime.js`, Query-Builder, Sync-Manager, Bildpipeline und Taskqueue ist die Grundlage geschaffen.
- Bestehende native `confirm()`-Dialoge wurden nicht alle ersetzt.
- Noch nicht jede lange Admin-/Medienoperation verwendet die neue persistente Queue; Share-Importe wurden als erster zentraler Pfad migriert.
- Die Testabdeckung wurde um kritische neue Pfade erweitert, ist mit rund 32 % aber noch nicht flächendeckend.

Diese Punkte sollten schrittweise erfolgen, damit bestehende Installationen und Arbeitsabläufe nicht durch einen großen Rewrite brechen.

## Lokale Prüfung

```bash
python -m compileall -q app tests
python -m ruff check .
node --check app/static/runtime.js
node --check app/static/app.js
node --check app/static/sw.js
python -m pytest -q
```
