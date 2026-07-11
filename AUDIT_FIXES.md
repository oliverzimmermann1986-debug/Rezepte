# Audit- und Änderungsbericht – Recipe Focus 1.2.0

## Produktfokus

- Rezeptbibliothek zur Startseite gemacht
- Navigation auf Rezepte, Import, Prüfen, Historie und Einstellungen reduziert
- allgemeine Dateisynchronisierung samt Worker, API, Scheduler, Units, Konfiguration und Tests ausgebaut
- Update-Migration für bestehende Installationen ergänzt

## Neue Rezeptbibliothek

- stabile Rezept-IDs in der History-Datenbank
- additive Migration bestehender SQLite-Datenbanken
- Volltextsuche über Name, Typ, Kategorie und Beschreibung
- Filterlisten direkt aus dem Datenbestand
- Sortierung und Pagination
- Nachindexierung vorhandener `info.json`-Dateien
- sichere Medienausgabe innerhalb des konfigurierten Rezeptstamms
- Video-, Bild- und PDF-Detailansicht

## Mobile-First-Oberfläche

- Rezeptsuche als primäre mobile Ansicht
- kompakte Karten auf Smartphones und Raster auf größeren Displays
- feste Bottom-Navigation und Safe-Area-Unterstützung
- große Touch-Ziele und iOS-zoomfeste Eingaben
- responsive Filter und horizontale Schnellfilter
- mobile Dialoge, Tabellenkarten und Einstellungs-Accordions
- Tastatur- und Escape-Bedienung

## Backend und Betrieb

- Import-, Pending-, Historien- und Konfigurations-APIs auf den reduzierten Umfang abgestimmt
- Status und Server-Sent Events nur noch für Import und Neuanalyse
- Installer und systemd auf Webdienst, Import, Datenbanksicherung und begrenzte Helfer reduziert
- veralteter Konfigurationsblock wird beim Update mit Sicherung entfernt
- Python-Abhängigkeit des ehemaligen Pair-Schedulers entfernt

## Bestehende Härtungen bleiben erhalten

- sichere Pending-Dateiablage
- Prozessgruppen-Abbruch für Downloads
- atomare Config-Saves
- Sitzungserneuerung nach Zugangsdatenänderung
- Same-Origin-, Proxy- und Pfadschutz
- geschützte Metriken
- verifizierte SQLite-Sicherungen
- gehärtete systemd-Dienste

## Testabdeckung

Die Suite prüft unter anderem:

- Authentifizierung und Sitzungen
- Konfigurationsvalidierung
- Download-Abbruch und URL-Grenzen
- Pending-Dateien und KI-Antworten
- Root-Helfer und Mountpoint-Grenzen
- Rezeptmigration, Suche, Filter, stabile IDs und Medienausgabe
- Recipe-First-HTML, JavaScript, Manifest und Responsive-CSS
- Login-, Health- und Metrics-End-to-End-Fluss

## Im Zielsystem zu prüfen

- echte IMAP-Zugänge
- echte TikTok-/Instagram-Downloads
- Ollama oder OpenAI-kompatibler Provider
- systemd-Zeitplanänderung
- Reverse-Proxy beziehungsweise Tunnel
- optionale Shelly-/HDD-Funktion

## v1.3 · Butter-Yellow UI

- bestätigten Butter-Yellow-Entwurf app-weit umgesetzt
- Rezeptseite visuell als primäre Startseite hervorgehoben
- Desktop-Sidebar und mobile Bottom-Navigation vereinheitlicht
- warme helle Formulare, Karten, Dialoge und Statuskomponenten
- neuer Rezeptliebe-Markenauftritt inklusive Login
- sichtbarer Suchbutton und kompakter mobiler Suchmodus
- keine externen Fonts oder Assets; vollständig lokal und offline-tauglich


## v1.3.1 · Bereinigtes Design und Mobile Footer

- alte Stylesheets `style.css`, `mobile-first.css`, `recipe-focus.css` und `butter-yellow.css` vollständig entfernt
- neues einheitliches Stylesheet `app/static/rezeptliebe.css` als alleinige Designquelle
- mobile Bottom-Navigation bündig und vollständig deckend am unteren Viewport-Rand
- zusätzlicher Inhaltsabstand verhindert verdeckte letzte Rezeptkarten
- Safe-Area-Unterstützung bleibt erhalten
- Alpine-Ausdrücke für noch nicht geladene Import- und Wartungsdaten null-sicher gemacht
- Versions- und Cache-Marker auf v1.3.1 aktualisiert
