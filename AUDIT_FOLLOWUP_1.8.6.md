# Vierter Prüflauf – Korrekturen 1.8.6

Stand: 04.10.2026. Umsetzung im integrierten Worktree `household-release`.
Der ursprüngliche Checkout und weitere Worktrees behalten ihre Arbeitsstände.
Prüfungen verwenden synthetische Konten, SQLite-Dateien und Downloads.

## Umgesetzte Korrekturen

| ID | Korrektur | Nachweis / Grenze |
|---|---|---|
| F4-H1 | Permanente venv unter `.venv.*`; der Launcher wird nicht umgezogen. | Linux-Probe reproduziert den alten Shebang-Fehler und startet den neuen Launcher nach Installation und Update. Die aktuell installierte Produktions-venv war bereits funktionsfähig. |
| F4-H2 | Kein automatischer Jobstart als Installationsgate. `SuccessExitStatus=1` akzeptiert abgearbeitete Fehler, `rc=2` bleibt ein Infrastrukturfehler. | Installation mit fehlgeschlagenem synthetischen Queue-Eintrag gelingt. Fehler beim Unit-Reload stellt Code, venv, Requirements und Units wieder her. |
| F4-H3 | Private Thumbnail-Revalidierung mit `max-age=0, must-revalidate`, ETag und `Vary: Cookie, Authorization`. | HTTP-Test: eigener Haushalt 200/304, fremder Haushalt 404, fehlende Sitzung 401. Fehler bleiben `no-store`; kein öffentlicher Cache. |
| F4-H4 | `require_auth` und `require_admin` laufen im Worker; Middleware und Dependencies teilen einen requestlokalen Benutzersnapshot. | Tests zählen eine Benutzerabfrage und prüfen den Thread. Neue Requests und ein offener SSE-Stream lehnen eine widerrufene Sitzung ab. Kein Cache über Requests hinweg. |
| F4-H5 | Fokusgebundener Keep-Awake-Hook und defensive Anzeige bei fehlenden Schritten im Expo-Kochmodus. | TSX-Harness prüft den fokussierten/ausgeblendeten Zustand. Der Load-Pfad fing fehlende Schritte bereits vorher ab; die behauptete alte Absturzstelle ist für diesen Stand nicht bestätigt. Kein Gerätetest. |
| F4-H6 | Rezeptkarten melden Name, Status, Bewertung und Prüfung; echte Überschriften bekommen `header`. Große Schrift hebt die Zeilenbegrenzung an Karten auf. | TSX-Harness, TypeScript und ESLint. Status war vor dieser Runde bereits Teil des Labels; ergänzt ist insbesondere die Bewertung. VoiceOver/Dynamic Type auf iPhone offen. |
| F4-M1 | Cart-Read ohne fällige Regel nimmt keine Schreibsperre; tatsächliche Fälligkeiten werden erneut innerhalb der Transaktion geprüft. | SQLite-Test liest trotz gleichzeitig gehaltener Schreibsperre. Bestehende Parallel-/Haushaltswechseltests prüfen die Mutation. |
| F4-M2 | Kandidaten-IDs aus FTS, Teilwort- und Zutatensuche werden einmal ermittelt. | 2000 Rezepte mit je 12 Zutaten; sechs Suchanfragen ergeben identische IDs, Reihenfolge und Counts. Fünf werden schneller, eine geringfügig langsamer. Kein Versprechen indexierter Teilwortsuche. |
| F4-M3 | Unit-Timeout 3900 s für vier Jobs mit je 900 s plus Reserve; SIGTERM löst Bereinigung und Wiederholung aus. | Echter Linux-SIGTERM gegen synthetischen Downloader: Prozess beendet, `.work` leer, Auftrag wieder `queued`. SIGKILL/Stromausfall können keine Python-Bereinigung garantieren. |
| F4-M4 | Restore prüft beide Timer zusätzlich zu den schreibenden Services. | Test mit ausschließlich aktivem Timer lehnt vor Backup/DB-Austausch ab. Kein Restore realer Daten durchgeführt. |

Der Standardinstaller bewahrt jetzt auch vorhandene Workerargumente, Grenzwerte
und Timerzyklen bei einem Update. Die Linux-Probe setzt abweichende Werte und
acht Jobs mit je 900 s: Einstellungen bleiben erhalten, das Budget steigt auf
7500 s. Größere bereits numerisch konfigurierte Zeitbudgets werden erhalten;
nicht auflösbare Variablen in den Jobargumenten lehnen das Update ab.

Referenzen: [Python-venv-Dokumentation](https://docs.python.org/3.11/library/venv.html),
[Expo SDK 55 KeepAwake](https://docs.expo.dev/versions/v55.0.0/sdk/keep-awake/).
Der Displayschutz gilt nur während des aktiven Kochbildschirms; die zuverlässige
Timer-Benachrichtigung bei gesperrtem Gerät ist ein eigener offener Punkt M14.

## Messung der Suche

Median aus sieben Wiederholungen, zusammen Rezeptliste (60 Einträge) und Count.
Synthetische Windows-/SQLite-Messung; keine Produktions-Latenzmessung.

| Suche | Vorher ms | Nachher ms | Treffer |
|---|---:|---:|---:|
| Pfanne | 26,80 | 29,16 | 100 |
| Faschiertes | 51,94 | 46,22 | 117 |
| Erdapfel | 50,46 | 37,31 | 100 |
| Pfanne ohne Zwiebel | 31,87 | 30,15 | 50 |
| Kartoffel Zwiebel | 54,19 | 46,77 | 50 |
| NoMatchUnicorn | 34,25 | 24,57 | 0 |

Der Plan verwendet die Kandidaten-IDs für Primärschlüsselzugriffe. FTS verwendet
seinen Index; beliebige Teilwörter in Rezepttext und Zutaten bleiben Scans.
Die ursprüngliche Angabe „146 ms“ wird nicht als aktuelle eigene Messung übernommen.

## Support-Portal und Profilordner

**F4-M5 bleibt offen:** CT 118 betreibt den separaten Supportdienst. Im lokal
vorhandenen Release-Snapshot gibt es keinen automatischen Retention-Prozess.
Die Datenschutzerklärung bietet ausdrücklich Löschung **auf Anfrage mit Referenz**
an, keine bestimmte automatische Frist. Aus dem Quellcode allein folgt nicht,
ob der Betreiber Anfragen manuell bearbeitet. Für eine Umsetzung sind der
aktuelle Support-Checkout, Zuständigkeit, Identitätsprüfung und die
Aufbewahrungsregel zu klären. Kein realer Ticketinhalt wurde gelesen oder gelöscht.
Die alten Positiva zu Turnstile/Access/XSS sind kein neuer vollständiger Live-Audit.

`.codex-tmp*/` ist in beiden Checkout-Ignoredateien ergänzt. Der genannte
Browserprofilordner hat keine versionierten Dateien und wurde von keinem
ermittelten Browserprozess verwendet. **Er wurde nicht gelöscht:** Die automatische
Freigabeprüfung blockierte die Löschung mit „blocked by policy“. Die ausdrücklich
angefragte Bestätigung ist noch offen; kein anderer Löschweg wurde verwendet.

Kosten-/Kontingentwerte aus dem eingefügten Altbericht wurden nicht als neue
Abrechnung bestätigt. Accountkontingent, Sessionverbrauch und API-Tokenpreise
sind unterschiedliche Angaben.

## Abnahme und Rollout

**Abgeschlossen:** Endgültiges Paket auf Review CT 117 und Produktion CT 200
geprüft und installiert. Schema bleibt 263.

- Vollständiger isolierter Backendlauf: **830 bestanden, 3 übersprungen**,
  **518,44 s**, Coverage **63,89 %** (Workflow-Schwelle 48 %).
  Übersprungen: zwei Tesseract-Fälle und die Linux-root-Installerprüfung unter
  Windows. Diese Installerprüfung wurde anschließend unter Linux durchgeführt.
- **39 Webtests**, **32 Expo-Tests**, TypeScript, ESLint, Python-Compileall,
  Ruff und Syntaxprüfung aller 13 Web-JavaScript-Dateien bestanden.
  Der gezielte Impeccable-Detektor hat für die geänderten Hauptkomponenten
  keine Befunde ausgegeben; das ersetzt keinen Geräte-/VoiceOver-Test.
- **15 Linux-Installerprüfungen** auf dem endgültig installierten Code in beiden
  Containern bestanden: permanenter Launcher, fremde Worker-/Timerkonfiguration,
  größeres Budget, fehlgeschlagener Job, Rollback, echter SIGTERM und Bereinigung.
  Eine separate echte systemd-Unit bestätigt `rc=1` mit `Result=success`.
- Neuer HTTP-Regressionssatz für Revalidierung/Haushalte: **48 bestanden**.
  Nach der letzten Installerkorrektur **9 Archiver-Pytests bestanden**, ein
  Windows-Linux-Skip. Die letzten Änderungen betrafen den Installer, seinen
  Linux-Test und Betriebsdokumentation; API-/Backendcode blieb seit dem
  vollständigen Lauf unverändert.
- Der erste vollständige Lauf hatte **einen Fehler**: Ein älterer Bildtest
  erwartete weiterhin `no-store`. Er prüft jetzt den beabsichtigten privaten
  Revalidierungs-Cache; Nanosekunden-ETag-Wechsel und die separaten Fremdhaushalt-/
  Widerrufstests bleiben bestehen. Die vollständige Wiederholung ist oben
  dokumentiert. Eine erste Übernahme der Linux-Probe ins Repository beschädigte
  die Shell-Template-Einrückung; der Test wurde repariert und danach neu geprüft.
  Frühere Paket-/Prüfstände sind als Diagnoseartefakte erhalten.

Endgültiges Paket: `.tmp/releases/1.8.6-security/rezepte-1.8.6.tar.gz`,
**140 Dateien**, SHA-256
`e2bda6608abdbd56c2a925632f2696a29d4939349ac36ce5042f60dd5496e7df`.
Manifest, Quellstand und beide installierten Dateisätze sind gegengeprüft.
Das Paket umfasst jetzt auch das separate Archiver-Paket und seine Linux-Probe.

Produktion: **229 globale Rezepte, bestehende Konten und 22 Tabellen erhalten**,
SQLite-/Foreign-Key-Prüfung bestanden. Keine privaten Waisen, keine aktiven
Importaufträge. Der historische nicht eindeutig zuordenbare Kochfortschritt
bleibt unverändert erhalten. Health/Readiness jeweils HTTP 200 / `ok` / 1.8.6,
Webservice und beide bisherigen App-Timer aktiv. Review wieder ausgeschaltet
und ausgehängt. In den Containerproben wurden nur synthetische Daten verändert.

Das separate Produktionsarchiv verwendet jetzt eine permanente venv; Launcher
2026.08.19 funktioniert. **Queue und 215 Archivdateien erhalten**, einschließlich
106 abgeschlossener und 57 bereits vorher fehlgeschlagener Aufträge. Die alten
Fehler wurden nicht als reale Downloads erneut gestartet. Der installierte
Produktionsdienst verarbeitet einen Job pro Lauf; seine Argumente und sein
Timer wurden bewahrt. Der Timeoutkonflikt des Altberichts betraf die
Standardvorlage mit vier Jobs, nicht diese vorhandene Ein-Job-Einstellung.
Geladene Zeit-/Exit-Policy und aktive/enabled Timer-Einstellung sind geprüft.
Die neue leere Sync-Marker-Tabelle ist ergänzt; vorhandene Queuezeilen bleiben
unverändert. Rolloutadapter: `.tmp/deploy_archiver_186.py`.

Rückfallstände:

- Review: `/opt/scrapper-code-backups/state-20261004-164500-331`
- Produktion: `/opt/scrapper-code-backups/state-20261004-164934-19587`
- Archiver: `/opt/video-archiver-code-backups/state-20261004-165714-20232`

Maschineller Abschlussnachweis: `.tmp/releases/1.8.6-security/checks.json`.
Keine echten KI-/Mail-/Downloadaufträge für Tests; kein Commit, Push oder
GitHub-CI-Lauf für diesen uncommittierten Stand.
Expo-Quellkorrekturen sind kein neuer iOS-Build, kein App-Store-Upload und kein
Nachweis von VoiceOver oder Keep-Awake auf einem echten Gerät.
