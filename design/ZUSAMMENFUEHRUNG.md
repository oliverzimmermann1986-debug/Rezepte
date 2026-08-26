# Zusammenführung Rezepte + Einkaufsliste

Ziel des Nutzers: **eine Webseite, ein Container, ein Tunnel** — ohne die
gehärtete, stabile Einkaufsliste-App wegzuwerfen oder ihre Features neu zu bauen.

Leitentscheidung: **Präsentation vereinen, Backends trennen.** Ein Nutzer sieht
eine App; intern laufen zwei FastAPI-Prozesse mit unterschiedlichem Risikoprofil
(schwerer Scraper/yt-dlp vs. leichte, latenzsensible Liste). Ein echter
Ein-Prozess-/Ein-Codebase-Merge wird bewusst **nicht** gemacht — er würde
PWA-Offline, Siri/CalDAV, `stamm`-Katalog, Snapshots, CSP und das
80%-Coverage-Gate der Einkaufsliste neu erfinden und die stabile App in das
volatilere Rezepte-Deployment ziehen.

## Stand 2026-08-26

- **Bereits umgesetzt:** Die Rezepte-Weboberfläche zeigt und bearbeitet die
  Einkaufsliste über den Proxy `/api/einkauf/*`; damit ist die Präsentation für
  den Nutzer bereits in einer Oberfläche vereint.
- **Noch getrennt:** `einkauf.api_url` verweist weiterhin auf einen eigenen
  Einkaufslisten-Dienst. Lokaler Zugriff ist möglich, Cloudflare-Access-Header
  werden für getrennte Installationen weiterhin unterstützt.
- **Noch offen:** ein gemeinsamer Container/ein Origin sowie `stamm` als
  gemeinsame Kanonik-Quelle. `app/recipes/canonical.py` bleibt derzeit ein
  separates System.

Die folgenden Phasen dokumentieren deshalb sowohl die ursprüngliche Entscheidung
als auch die noch offenen Integrationsschritte; Phase 3 ist für das Web-Frontend
bereits weitgehend erreicht, ohne die Backends zusammenzulegen.

## Ist-Zustand (belegt)

Beide Apps sind strukturell Zwillinge — FastAPI + SQLite + PWA, gehärtet,
localhost/Token, eigener Service-Worker auf Scope `/`.

| | Rezepte | Einkaufsliste |
|---|---|---|
| Framework | FastAPI + SQLite-Index über Datei-Ordnern | FastAPI + reines SQLite |
| Katalog/Kanonik | `app/recipes/canonical.py` — hardcodierte Synonym-Map + Plural-Heuristik | `stamm` (name, category, default_unit, aliases) + `stamm_terms` (indizierte Match-Terme), user-pflegbar in DB |
| Einkauf | Rezept-**Warenkorb** `shopping_cart` (Smart-Merge über `canonical_name`+Einheit), `cook/{recipe_id}` | Vollwert-Liste: `items`→`consolidated`, `recurring`, `templates`, `trash`, Snapshots |
| PWA | `static/`: index.html, app.js, sw.js, manifest.json, CSP in `app/security.py:177`, SW-Scope `/` | `static/`: index.html, app.js, app-core.js, sw.js, manifest.webmanifest, CSP in `main.py:457`, SW-Scope `/` |
| Kopplung heute | `/api/einkauf/*` und `POST /api/shopping/push-to-einkauf` sprechen die in `einkauf.api_url` konfigurierte Einkaufsliste an; Cloudflare-Access-Header sind nur für einen externen Origin nötig | empfängt `/items`, konsolidiert gegen `stamm` |

**Doppelte Pflege** (Kernproblem des Nutzers): Zwei getrennte Kanonik-Systeme
für dieselbe Aufgabe. `canonical.py` ist eine hardcodierte 80%-Heuristik;
`stamm`+`stamm_terms` ist das reichere, user-pflegbare Modell (Kategorie,
Standard-Einheit, Aliasse, indizierte Terme). → **`stamm` wird Single Source of
Truth.**

## Zielarchitektur (3 Schichten)

1. **Ein Frontend (umgesetzt)** — eine PWA-Hülle: ein `index.html`, ein `app.js`, **ein**
   `sw.js`, ein `manifest`, eine CSP, eine Navigation mit den Bereichen
   „Rezepte" und „Einkaufsliste". Spricht beide Backends über deren APIs an.
   *Zwingend eine Hülle, nicht zwei nebeneinander — sonst SW-Scope-Kollision auf `/`.*
2. **Zwei Backends, ein Container** — beide FastAPI-Prozesse hinter einem
   internen Reverse-Proxy (z.B. Caddy/nginx im selben Image, oder ein
   Supervisor + zwei uvicorns). Ein Cloudflare-Tunnel auf einen Origin, Routing
   per Pfad-Präfix (`/api/rezepte/*`, `/api/einkauf/*`). Zwei getrennte
   SQLite-DBs/Datenverzeichnisse. Risiko-Isolation bleibt.
3. **Gemeinsamer Katalog** — `stamm`+`stamm_terms` als Kanonik-Quelle; Rezepte
   matcht Zutaten dagegen, statt eine eigene Map zu pflegen.

## Phasen (risikoarm geordnet — jede Phase liefert eigenständig Wert)

### Phase 0 — Entscheidungen fixieren (kein Code)
- CSP beider Apps abgleichen → **eine** gemeinsame, strikte CSP (keine
  Inline-Handler; Alpine/`alpine.min.js` in Rezepte prüfen — braucht ggf.
  `unsafe-eval`, das die Einkaufsliste heute nicht erlaubt → Konflikt klären).
- SW-Strategie festlegen: **ein** SW, cached ausschließlich die App-Hülle,
  **nie** Listen-/Rezept-/Backup-Daten (Härtungsregel der Einkaufsliste).
- Ports, Datenverzeichnisse, Env/Token-Layout pro Prozess festlegen.

### Phase 1 — Ein Container, ein Tunnel (Ops-Gewinn, kein Code-Merge)
- Ein Image mit beiden Apps + internem Proxy + Prozess-Supervisor; ein
  `docker-compose.yml`; ein Tunnel auf einen Origin.
- **Push auf `localhost` umstellen** → `einkauf.api_url = http://127.0.0.1:<port>`.
  Damit fallen Cloudflare-Access-Token, 302-Redirect-Problem und die stille
  Push-Fehlfunktion komplett weg. *Sofortiger, sichtbarer Nutzen.*
- Ergebnis: ein Container, ein Tunnel, keine Cross-Container-Kopplung — ohne
  jede Änderung an der App-Logik.

### Phase 2 — Gemeinsamer Katalog (bessere Zutaten-Pflege)
- `stamm`/`stamm_terms` als Kanonik-Quelle. Read-only-Endpoint der
  Einkaufsliste (Match: Freitext → `stamm`-Eintrag inkl. category/default_unit).
- Rezepte nutzt diesen Match beim Zutaten-Indexieren und im Cart; `canonical.py`
  bleibt nur als Offline-Fallback. Aliasse einmal in `stamm` pflegen → beide
  Seiten profitieren.
- Push wird **strukturiert** (name, canonical/stamm_id, amount, unit, category)
  statt `raw_text` → beim Konsolidieren geht nichts verloren.

### Phase 3 — Ein Frontend (die „eine Webseite", weitgehend umgesetzt)
- Die Rezepte-Shell enthält Rezept- und Einkaufslisten-Views in einer Navigation
  und spricht die Einkaufsliste ausschließlich über relative `/api/einkauf/*`-
  Pfade an.
- Offen bleibt die betriebliche Zusammenführung unter einem Origin. Erst dann
  können die eigenständige Einkaufslisten-PWA und ihre alten SW-Registrierungen
  vollständig abgelöst werden.

## Leitplanken (nicht verhandelbar)
- CSP **nicht** absenken; die strikte Policy der Einkaufsliste gilt für die Hülle.
- SW cached **nie** Nutzdaten (Listen, Rezepte, Backups).
- Coverage-Gate (80%) und fail-closed-Deployment der Einkaufsliste bleiben.
- Zwei DBs bleiben getrennt; nur der Katalog wird geteilt/gespiegelt.

## Bewusst NICHT gemacht
- Kein Ein-Prozess-Merge (Blast-Radius: Scraper-Hänger würde Liste mitreißen).
- Kein Nachbau der Einkaufsliste-Features in Rezepte.
- Keine Zusammenlegung der beiden SQLite-DBs.
