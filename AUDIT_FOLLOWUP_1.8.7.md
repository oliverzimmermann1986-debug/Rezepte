# Gegenprüfung und Korrekturen nach dem fünften Prüflauf

Stand: 04.10.2026. Integrierter Worktree `household-release`; Originalcheckout
und parallele WIP bleiben erhalten. 1.8.7 / Schema 264 wurde nach grünem
Gesamtlauf auf Review und mit identischem Paket auf Produktion geprüft.
Kein Commit, Push oder neuer CI-/Gerätenachweis.

## Bestätigte und korrigierte Befunde

| Befund | Gegenprüfung und Korrektur |
|---|---|
| F5-H1: Auth-Schalter als String | Nur `True` als Boolean schaltet die Anmeldung ab. Strings, Zahlen, null und andere Typen bleiben fail-closed; API weist falsche Typen vor dem Speichern zurück. |
| F5-H2: Tests auf Betreiberpfaden | Direkte pytest-Aufrufe waren ungesichert. `tests/conftest.py` installiert vor App-Import eine eigene Konfiguration, der Runner nutzt dieselbe Isolierung. Ein eigener Prozess mit absichtlich gesetzter Betreiberkonfiguration prüft das, ohne diese zu lesen oder zu verändern. |
| F5-H3: KI-Zutaten ohne Beleg | Namen, Mengen, Einheiten und tatsächliche raw-Ausschnitte werden lokal verglichen; fehlende Quellenzutaten und niedrige/ungültige Confidence führen zur manuellen Pflege (`error`). PDF behält eine vorhandene lokale Liste. Indexer und manueller Extraktionsendpunkt sind ebenfalls abgesichert. |
| F5-H3: abgeschnittene Vision | Regulärer Abschluss `stop` erforderlich, auch für JSON-Klassifikation und Audit-Namensvorschläge. Teilweise gelesene mehrseitige Scans werden verworfen; mehr als drei Vision-Seiten verlangen eine andere Quelle/manuelle Pflege. Vor Rasterung maximal 1600 Pixel Kantenlänge. |
| F5-H4: PDF-Mengen | `1.5 kg` → 1,5 kg; `1.000 g` → 1000 g; `1/2 Bund` → 0,5 Bund; `1 1/2 EL` → 1,5 EL. Unicode, deutsche Dezimalwerte, gemischte Brüche, Bereiche und echte Aufzählungen getestet. `1 Dose (400 g)` bleibt eine Dose mit dem Originaltext, nicht 400 Dosen. |
| F5-H5: Suche | Negatives `Ei` matcht keinen Reis/Weißwein. Zutatenbezeichnungen und Pluralformen werden berücksichtigt. Phrasen behalten Leerzeichen/Reihenfolge; Liste und Gesamtzahl werden zusammen geprüft. |
| F5-H6: gekaufte Cart-Position | Neuer Bedarf setzt `checked=0`, sowohl im Basismodell als auch im Haushaltsmodell. Mengen und Herkunft bleiben erhalten. |
| F5-M1: Katalog | Paprikapulver, Eisbergsalat und Olivenöl waren bereits korrigiert. Tomatenmark und Teelicht wurden jetzt ergänzt; alle fünf Beispiele haben Regressionen. |
| F5-M2: Zutatenvereinheitlichung | Bekannte Zubereitungszusätze werden entfernt, Produktmerkmale wie laktosefrei bleiben erhalten. Rote Bete bleibt erhalten. Cart-Aufbereitung repariert bekannte automatische Altfehler; eigene Canonical-Zuordnungen und Einkaufsausschlüsse bleiben erhalten. Bestehende Rezeptzeilen werden nicht blind umgeschrieben. |
| F5-M3: Volumenanzeige | Volumen im Warenkorb wird in ml oder l angezeigt, 250 ml bleiben 250 ml. |
| F5-M4: Quellcover/KI-Label | Automatische Bildaufträge ersetzen vorhandene Cover nicht; auch alte Queue-Aufträge werden vor API-Aufruf abgefangen. Explizite Generierung/Backfill bleiben nach Originalsicherung möglich. Rezeptdetails kennzeichnen generierte Bilder in Web-, SwiftUI- und Expo-Quelle. |
| F5-M5: KI-Kosten | Alle KI-POST-Pfade einschließlich älterer Audit-Namensvorschläge sind begrenzt. SQLite serialisiert Kontingente über Prozesse/Threads, standardmäßig 1000 Versuche und 40 Bildversuche pro rollierenden 24 Stunden. Retries/Timeouts zählen. Das ist ausdrücklich keine monetäre Obergrenze für frei konfigurierbare Modelle. |
| F5-M6: Mail/Datenschutzhinweise | Erkennbare Mail-Signaturen aus dem KI-Hinweistext entfernt. Tatsächlichen Medienabruf, Videoframes, Audiospuren und Bildgenerierung beschrieben. Verarbeitung in den USA ist je nach Projekt/Anbieter/Region möglich; Betreiberverträge und Region sind nicht aus Tests ableitbar. |
| F5-M7: öffentliche Fehler | Health-/Readiness-Fehler und HTTP-Fehler ab 500 geben keine internen Diagnosen weiter. Der fest definierte, hilfreiche 507-Speichermangeltext bleibt sichtbar; beliebige 507-Details wären weiterhin maskiert. |

## Aussagen, die für den aktuellen Stand nicht bestätigt sind

- **„Alle fünf Sicherheitsbugs ohne Regressionstest“:** Für 1.8.6 nicht zutreffend.
  Private PDF-Pfade werden mit echten Sitzungen, Gast, fremdem Haushalt und
  Render-Sperre geprüft (`tests/test_tenants.py::test_print_pdf_is_scoped_like_api_and_does_not_render_foreign_recipes`).
  Bestandsmigrationen mit Daten/Tupel-Verbindungen sind in `tests/test_accounts.py`
  abgesichert. IMAP-Zertifikate, Punktpfade, Canonical-Origin, Bodylimits und
  Rasterlimits stehen in `tests/test_audit_import_security.py`; TLS enthält
  zusätzlich einen echten lokalen Handshake. `tests/test_security_rbac.py` und
  die Haushalts-/Gastsuiten entfernen die fachlichen Auth-Overrides gezielt.
  Der Standardclient bleibt für isolierte fachliche Tests ein Admin-Override.
- **„C:\opt\scrapper wurde heute angelegt“:** Der Hauptordner, `data` und
  `logs` existieren seit Juli; `temp` seit August. Unter `files` wurden heute
  fünf synthetische Dateien (85 Bytes) angelegt. Löschfreigabe ist nur für diese
  Dateien und danach leere Unterordner angefragt. Kein Gesamtordner gelöscht.
- **0,75 Eier / 0,25 Prise:** Das sind mathematisch richtige Skalierungen.
  Aufrunden beim Kochen verändert das Rezeptverhältnis. Eine separate Kauf-
  oder Küchenregel wäre eine Produktentscheidung; keine stille Rundung eingebaut.
- Die gemeldete **Coverage von 61 %** stammt aus dem älteren Prüflauf.
  1.8.6 hatte lokal 63,89 %. Für 1.8.7 wird der vollständige neue Lauf verwendet.
  Alte Token-/Plan-/Kostenangaben wurden nicht als aktuelle Kontodaten bestätigt.

## Nachweise und Grenzen

- Fachliche Reproduktionen und Negativfälle: `tests/test_audit_functional_safety.py`.
  Die erste gezielte Ausführung reproduzierte Suche, beide Cart-Varianten und
  öffentliche Diagnoselecks (sechs rote Tests). Danach wurden KI-/Quotennachweise
  sowie vorhandene Sicherheits- und Bildregressionen ergänzt.
- Ein angefangener vollständiger Lauf fand drei Integrationsprobleme:
  aktualisierte Schemaerwartung, fehlendes Belegfeld in einem alten Mock und
  maskierter hilfreicher 507-Text. Das wurde korrigiert; der Konkurrenztest
  verwendet nun echte Zutatenbelege, statt A/B/C als fiktive Zutaten.
- Der spätere Confidence-Abgleich schließt NaN/Infinity und ungültige Titelwerte
  aus; PDF nutzt die konfigurierte Schwelle. Der vollständige Abschlusslauf
  wird erst bei Exit 0 als bestanden gezählt.
- Ein vollständiger Lauf meldete 900 bestandene, sechs fehlgeschlagene und drei
  übersprungene Tests. Drei Gegenbeispiele zeigten ein überschriebenes eigenes
  Canonical-Mapping; diese Zuordnungen werden nun ausdrücklich erhalten. Drei
  Archiver-Tests liefen wegen nur etwa 1,5 GB freiem Rechnerplatz nicht bis zu
  ihren simulierten Download-/Abbruchfällen. Sie verwenden nun kontrollierte
  Kapazitätswerte; der echte Platzmangeltest und Produktionsschutz bleiben bestehen.
  Der gezielte Nachlauf dieser Bereiche bestand mit 94 Tests.
- Der nächste Gesamtlauf bestand mit 905 Tests und fand zwei Regressionen im
  Wochenplan: die bisherige Normalisierung `Pasta` → `nudeln` war überschrieben.
  Der Wochenplan normalisiert diesen bestehenden Eigennamen wieder, während
  abweichende gepflegte Mappings und Ausschlüsse erhalten bleiben. Gezielt
  bestanden danach 123 Tests; auch eine wieder freigegebene eigene Zuordnung
  bleibt im Wochenplan erhalten.
- **Endgültiger lokaler Gesamtlauf:** 907 bestanden, drei übersprungen, Exit 0
  in 261,69 Sekunden. Coverage inklusive Branches: 64,34 % (Gate: 48 %).
  39 Web- und 32 Expo-Regressionsprüfungen, TypeScript, ESLint, Compileall,
  Ruff, elf JavaScript-Syntaxprüfungen und `git diff --check` bestanden.
  Die drei lokalen Skips sind Tesseract- und Linux-Installergrenzen; ein alter
  grüner CI-Lauf wird dadurch nicht als neuer Nachweis gewertet.
- Das automatische Entfernen von sieben abgeschlossenen, ausschließlich in
  dieser Runde erzeugten Testverzeichnissen unter `.tmp` wurde mit „blocked by
  policy“ abgelehnt. Nichts gelöscht, kein anderer Löschweg versucht. Berichte
  sind separat erhalten; aktuelle Tests bleiben gegen Umgebungseinflüsse isoliert.
- `tools/probe_functional_safety.py` prüft installierten Code mit temporärer
  Konfiguration/Datenbank und Mock-Antworten. Keine echten KI-Aufrufe oder
  Produktivkonto-Änderungen. Vorhandene Lifecycle-Prüfungen sichern Mandanten,
  Sitzungswiderruf, Dateiauslieferung und Importbudgets zusätzlich ab.
- Belegprüfung ist ein konservativer Quervergleich, kein Beweis vollständiger
  OCR-/Rezept-/Allergenrichtigkeit. Übersetzungen, unklare Einheiten oder Formate
  können daher zusätzliche manuelle Prüfung verlangen.
- Native Änderungen sind Quellcode. Xcode-/Geräte-/VoiceOver-/Hintergrundtimer-
  Prüfung und App-Store-Schritte sind damit nicht erledigt. Separates Archiv
  und Supportdienst erhalten in dieser Runde keine neue Implementierung.

Technische Referenzen: [OpenAI-Antwortabschluss](https://platform.openai.com/docs/api-reference/chat/create)
und [API-Datenkontrollen/Regionen](https://developers.openai.com/api/docs/guides/your-data).
Die konkrete Anbieter-/Vertragskonfiguration wurde nicht eingesehen.

## Endgültige Abnahme und Rollout

- **Lokales Gate:** 907 Backendtests bestanden, drei Skips, Exit 0. Coverage
  64,34 %, 39 Webtests und 32 Expo-Tests bestanden. TypeScript/ESLint und die
  dokumentierten Syntax-/Lintprüfungen ebenfalls grün. Die Testläufe verwendeten
  temporäre Konfigurationen, Mock-KI und synthetische Inhalte.
- **Paket:** 146 Dateien; SHA-256
  `18c9c35c8d68620f8c178afa61a32726d8ea43bc6919eb579486a0b956aacb6a`.
  Review CT 117 und Produktion CT 200 bestätigten sämtliche installierten
  Dateihashes, Version 1.8.7 und Schema 264. Der lokale Quellstand wurde nach
  dem Rollout erneut gegen das Manifest verglichen.
- **Installierter Code:** In beiden Umgebungen zehn neue Fachlogik-/KI-Prüfungen
  und 25 bestehende Lifecycle-/Sicherheitsprüfungen bestanden. Sie verwenden
  ausschließlich temporäre Daten; weder echte KI-Aufrufe noch reale Konten
  oder Einladungen wurden dafür angelegt. Gastzugriff und acht verbotene
  Gastaktionen wurden zusätzlich gegen den laufenden Webservice geprüft.
- **Datenerhalt auf Produktion:** 229 aktive globale Rezepte und bestehende
  Konten unverändert. Alle 22 Bestandsdatentabellen gegen den Snapshot vor dem
  Update verglichen; keine entfernten Papierkorbeinträge oder Datumsänderungen.
  SQLite-/Fremdschlüsselprüfungen bestanden, keine privaten Waisen und keine
  neuen laufenden Importjobs. Der eine historische Kochfortschritt ohne klare
  Identität bleibt unzugeordnet erhalten.
- **Betrieb:** Health und Readiness HTTP 200 / `ok` / 1.8.7. Webservice, Jobtimer
  und DB-Backuptimer aktiv; bestehende Aktivierung und Konfiguration bewahrt.
  Review wieder ausgeschaltet und ausgehängt.
- **Separate Dienste:** Neun Archiver-Code-/Unit-/Probe-Dateien sind bytegleich
  mit dem unter 1.8.6 geprüften Stand. Kein erneuter Archiver-Rollout oder echter
  Download in dieser Runde; die 15 bisherigen Linux-Installerprüfungen bleiben
  ein ausdrücklich älterer Nachweis. Support CT 118 blieb unangetastet.
- **Rückfallstände:** Review
  `/opt/scrapper-code-backups/state-20261004-182920-331`, Produktion
  `/opt/scrapper-code-backups/state-20261004-183050-20441`.

Maschinelles Abschlussgate: `.tmp/releases/1.8.7-security/checks.json`.
Einzelberichte: `ct117-report.json` und `ct200-report.json` im selben Verzeichnis;
vollständiger lokaler Testnachweis: `.tmp/backend-final-1.8.7.xml`.

Die gemeldeten schwachen Module sind weiterhin nicht vollständig abgedeckt:

| Modul | Aktuell gemessene Coverage inklusive Branches |
|---|---:|
| `email_processor` | 46,61 % |
| `scraper` | 48,41 % |
| `auth` | 66,01 % |
| `sharing` | 66,44 % |

**Offen:** native Geräte-/Xcode-Abnahme und die Betreiberentscheidungen sowie
Restbefunde in [AUDIT_HANDOFF.md](AUDIT_HANDOFF.md). KI-Kontingente sind keine
Euro-Grenze und Quellvergleiche kein Vollständigkeitsbeweis. Bereinigungen
wurden nicht durchgeführt: Löschung der fünf synthetischen Dateien unter
`C:\opt\scrapper` ist noch angefragt; automatische Freigabe für sieben neue
`.tmp`-Testordner und das ältere Browserprofil war blockiert.
