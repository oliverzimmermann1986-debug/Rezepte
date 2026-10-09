# Product

<!-- impeccable:product-schema 1 -->

## Platform

adaptive (Web/PWA und native iOS-App)

## Users

Getrennte private Haushalte mit Login-Accounts und globaler Rezeptsammlung, in drei bestätigten Situationen:

1. **Kochend, einhändig am Handy in der Küche** — der Hauptfall. Rezept ist in der nativen iOS-App oder PWA offen, Hände sind beschäftigt, Blickabstand ist größer als am Schreibtisch.
2. **Haushalt/Partner an Handy und Desktop** — gemeinsame Bibliothek, gemeinsame Einkaufsliste, gemischte Geräte. Pflege- und Prüfarbeit (Import auflösen, PDF/Scan, Wartung) passiert am Desktop.
3. **Gäste ohne Account** — können per Gastanmeldung die Bibliothek, Einkaufsliste und den Wochenplan ansehen. Das Backend sperrt Schreibzugriffe und Verwaltung. Share-Links (`app/routes/sharing.py`) zeigen weiterhin ein einzelnes geteiltes Rezept.

## Product Purpose

Rezepte aus TikTok-/Instagram-Links, Texten, Fotos, Videos und PDFs werden eingesammelt, mit `yt-dlp`, OCR sowie der konfigurierten OpenAI-Analyse verarbeitet und in eine durchsuchbare Bibliothek einsortiert. Erfolg heißt: ein gefundener Rezeptname reicht für einen ehrlichen Import; Zutaten und Zubereitung werden aus Caption, Bildtext, Videoframes oder Sprache ergänzt, soweit belastbare Belege vorhanden sind. Die Quelle (Video, Caption und Scan-Belege) bleibt am Rezept erhalten.

## Positioning

Kein Rezept-Manager, in den man Rezepte tippt, sondern eine **Auffang-Anlage für Social-Video- und Bildrezepte**: Direktlink, Texteingabe und Datei-Upload als Eingänge, Dateisystem plus SQLite als kontrolliertes Archiv. Anwendung und Daten laufen self-hosted im eigenen LXC; OpenAI, die Quellplattformen und optional Cloudflare sind klar abgegrenzte externe Dienste. Es gibt keine externen Fonts oder Design-CDNs und keine Telemetrie. Unsichere oder unvollständige Ergebnisse landen bewusst in einer manuellen Prüfung, statt erfundene Zutaten oder Schritte zu speichern.

## Operating Context

- Ausdrücklich gestartete Importe in Web und App; Dateisperren und die Hintergrundwarteschlange verhindern konkurrierende Verarbeitung. Kein Mailabruf oder Mailzeitplan.
- Externe Erreichbarkeit über HTTPS und optional Cloudflare-Tunnel; Anmeldung und Rollenprüfung erfolgen immer im Rezeptserver.
- Kochen am Handy (native iOS-App oder PWA, iOS-Safe-Area, Bottom-Navigation); Verwalten am Desktop oder in der Admin-Zentrale der App.
- Einkaufsliste entsteht aus Rezept-Zutaten und wird über `canonical_name` zu einem Eintrag pro Zutat verschmolzen.
- Zusätzliche Quelle neben Video: PDF-/Scan-Import mit Ausrichtung, OCR, Randbeschnitt und Seiteneditor.

## Capabilities and Constraints

**Bestätigte Funktionen:** Volltextsuche (SQLite FTS) mit Synonymen, Ausschlüssen (`-Zutat` / „ohne Zutat") und Tippfehlerkorrektur; Filter über Typ, Kategorie und Zutaten; Favoriten; Einkaufsliste; Portionen/Skalierung und Nutrition-Felder; Rezept-Versionen mit Vergleich und Wiederherstellung; Pending-Auflösung fehlgeschlagener oder unsicherer Importe; manueller URL-Direktimport; Jobs & Logs; Stammdaten; Papierkorb; Share-Links.

**Datenmodell (Auszug, `app/db.py`):** `recipes(name, type, category, folder_path, description, thumb_filename, video_filename, source_added_at, indexed_at, ingredients_status)`; `recipe_ingredients(name, canonical_name, amount, unit, raw, sort_order)`; `recipe_steps(step_number, …)`. `ingredients_status` kennt `pending | running | ok | error | skipped` — **unfertige Rezepte sind ein Normalzustand, kein Fehler**, und die Oberfläche muss diesen Zustand ehrlich zeigen.

**Technische Constraints:** FastAPI + SQLite (WAL, `synchronous=FULL`); Frontend bleibt ohne Build-Pipeline (`app/static/index.html`, ein Stylesheet `rezepte.css`, lokales Alpine.js). `app.js` enthält Zustand und Infrastruktur, `app/static/features/` die Fachmethoden für Rezepte, Einkauf, Wochenplan und Administration; strikte CSP und Security-Header, deshalb **keine externen Fonts, Skripte oder Design-CDNs**; Touch-Ziele ≥ 44 px; Formfelder mobil 16 px gegen iOS-Autozoom.

**Konten:** Eigene Registrierung und eine Einladung für eine zweite Person mit
separater Anmeldung. Der Link gilt sieben Tage, ist einmal verwendbar und
widerrufbar. Jeder Haushalt ist ein getrennter Mandant. Globale Rezepte sind für alle lesbar;
private Rezepte, Sammlung, Favoriten, Bewertungen, Kochverlauf, Einkauf und
Wochenplan gehören zum Haushalt. Private Importe vorhandener globaler Links
speichern einen Verweis ohne erneuten Download. Gastzugang und Registrierung
verwenden die eigene Kontenverwaltung. Ein vertrauenswürdiger Proxy ersetzt
keine Sitzung; Cloudflare-Gerätezugangsdaten sind im Rezeptclient nicht nötig.

## Brand Commitments

- Name: **Rezepte**.
- **Pflaume ist die Basisfarbe** (Nutzerentscheidung, 30.08.2026): `#8A577F` für Primäraktionen und Auswahl, `#6B3D63` für gedrückte Zustände, Creme `#FFFAF0` / `#FFFDF8` als ruhige Arbeitsfläche und tiefes Pflaumenbraun `#3E2B39` für Text. Butter bleibt in der nativen Farbauswahl als Alternative erhalten.
- Oberflächensprache: Deutsch.

## Evidence on Hand

- Echte Bibliothek im Dateisystem plus SQLite-Index; echte Thumbnails (`{name}.jpg`) und Videos (`{name}.mp4`) je Rezeptordner.
- Vorhandene Dokumentation: `README.md`, `ADMIN_CENTER.md`, `PDF_PROCESSING.md`, `BUTTER_YELLOW_DESIGN.md`.
- **Nicht vorhanden und nicht zu erfinden:** Nutzerzahlen, Bewertungen von Dritten, Preise, Lizenzen, Benchmarks, Presse. Rezeptinhalte in Mockups sind Demonstrationsmaterial und als solches zu kennzeichnen.

## Product Principles

1. **Die Küche gewinnt.** Der kochende Handy-Fall schlägt jede Desktop-Eleganz; Lesbarkeit auf Armlänge und einhändige Erreichbarkeit sind Pflicht, nicht Feinschliff.
2. **Unfertig ehrlich zeigen.** Ein Rezept ohne extrahierte Zutaten, ein fehlgeschlagener Download, ein Pending-Item sind reguläre Zustände und brauchen eine sichtbare, ruhige Form.
3. **Die Quelle bleibt am Rezept.** Video, Caption und Herkunft sind Teil des Rezepts, nicht Metadaten-Beiwerk.
4. **Lokal kontrolliert.** Rezeptdaten und Archiv bleiben im eigenen LXC. Externe Analyse- und Abrufdienste sind konfigurierbar, sichtbar und dürfen fehlende Belege nicht durch erfundene Inhalte ersetzen.
5. **Verwalten getrennt vom Kochen.** Technische und qualitätssichernde Funktionen leben in der Admin-Zentrale, damit die Rezeptansicht ruhig bleibt.

## Accessibility & Inclusion

Touch-Ziele ≥ 44 px, 16-px-Formfelder mobil, vollständig freigehaltene Bottom-Navigation inklusive iPhone-Safe-Area. Kontrast muss unter Küchenlicht und mit Displayhelligkeit auf Mittelstellung tragen.
