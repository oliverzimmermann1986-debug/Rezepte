# Audit-Nacharbeit 1.8.5

Stand: 04.10.2026. Backend/Web **1.8.5**, Schema **263**, ist nach erfolgreicher
Linux-Reviewprüfung auf Produktion CT200 bereitgestellt und am laufenden Dienst
verifiziert. Review CT117 ist wieder ausgeschaltet und nicht eingehängt.

## Korrekturen

- **K3 — Mail-TLS:** `app/core/email_processor.py` verlangt Zertifikatsketten-
  und Hostnamenprüfung vor LOGIN. Der explizite Verbindungstest liest nur und
  meldet Fehler, statt sie als leeres erfolgreiches Postfach darzustellen.
  Der gespeicherte Aktivstatus und die Mailkonfiguration werden nicht verändert.
- **K4 — Pfade:** `app/core/safety.py` bereinigt Ordnerteile gemeinsam für
  Scraper und manuelle Bearbeitung: Punktpfade, Trennzeichen, Steuerzeichen,
  Windows-Gerätenamen und überlange UTF-8-Komponenten. Normale deutsche Namen
  bleiben verwendbar.
- **Canonical-Identität:** `app/core/recipe_web.py` akzeptiert bei HTML nur
  Canonical-URLs des tatsächlich abgerufenen HTTPS-Origin. Validierte Redirects
  und die separate TikTok-Kurzlinkauflösung funktionieren weiterhin.
- **Bodylimits:** `app/security.py` begrenzt alle Bodies vor dem Form-/JSON-
  Parsing, einschließlich Login und Registrierung. Regulär 1 MiB, Dateiimporte
  25 MiB plus Overhead, Foto-/Coverimporte 10 MiB plus Overhead.
- **PDF-Vorschau:** `app/recipes/image_cache.py` begrenzt die Rasterung auf
  1600 Pixel an der längeren Seite. Die Linux-Probe fand zusätzlich das fehlende
  `pdftoppm` auf Review; Installation/Update ergänzen dafür `poppler-utils`.
  Die vorhandene Produktion hatte diese Abhängigkeit bereits installiert.
  Die echte Ausgabeprüfung fand außerdem eine falsche temporäre JPEG-Endung:
  Poppler hängt die Endung an den vollständigen Prefix an. Der tatsächliche
  Ausgabepfad wird nun atomar übernommen und bereinigt; der alte Mock ist
  entsprechend korrigiert. Details: M21/M22 in AUDIT.md.
- **Videoressourcen:** `app/core/downloader.py` begrenzt bekannte Downloadgrößen
  und überwacht unbekannte Streams samt Fehlerlog. Überschreitung/Timeout beendet
  auf Linux die Prozessgruppe und bereinigt ausschließlich den eigenen Ordner.
  100 MiB ist eine überwachte Abbruchschwelle; zwischen Messintervallen kann die
  Grenze überschritten werden. Keine Aussage über jeden anderen Mediendecoder.
- **Web:** `runBackupNow` ist wieder vorhanden, Beitritt bestätigt die Übernahme
  privater Daten, Einkaufsübermittlung blockiert doppelte laufende Sends auch
  für externe Listen. Fehler geben die Buttons frei und bleiben sichtbar.
- **Expo-Quelle:** Startup-Sitzungscheck wartet maximal 5 Sekunden. Bei 401
  werden auch Speicher-/Festplattenbilder bereinigt, selbst wenn der API-Cache
  einen Fehler meldet. Ein Netzfehler löscht weder Sitzung noch Offline-Daten.
- **Dokumentation:** Der README-Ollama-Block ist auf den vorhandenen OpenAI-Pfad
  korrigiert. Alle alten Auditpunkte sind mit aktuellem Status in [AUDIT.md](AUDIT.md)
  und [FIXPLAN.md](FIXPLAN.md) übernommen.

K1 (private Druck-PDFs) und K2 (Migration 232) waren im integrierten Arbeitsstand
bereits korrigiert. Ihre Regressionen und Schema-263-Haushaltsprüfungen bleiben
Bestandteil der Prüfung. Der nachgereichte Bericht ist kein aktueller Nachweis
von acht fehlschlagenden Tests oder einer fehlgeschlagenen Migration.

## Lokale Nachweise

- Vor der Korrektur: **8 Fehler, 2 bestanden** in zehn neuen Prüfungen für TLS,
  Punktpfade, Canonical-Origin, Bodylimits und PDF-Vorschau.
- Hauptprüfung nach Korrektur: **103 bestanden**. Ergänzte Ressourcen-/Mailtests:
  **28 bestanden**, abschließende echte TLS-Prüfung **16 bestanden**.
- Endgültiger isolierter Backendlauf nach sämtlichen Backendkorrekturen:
  **819 bestanden, 2 übersprungen** in **338,97 s**, Coverage **63,52 %**,
  über der Workflow-Schwelle von 48 %.
  Übersprungen sind die beiden lokalen Tesseract-OCR-Fälle.
- **39 Webtests**, **27 Expo-Tests**, Expo-TypeScript und ESLint bestanden.
- Python-Compileall, Ruff, JavaScript-Syntax und Diff-Whitespaceprüfung bestanden.
- Nach Ergänzung der fehlenden Linux-Abhängigkeit: **30 Installations-/Release-
  Guardtests bestanden**. Nach dem JPEG-Pfadfix: **45 PDF-/Bild-/Versionsfälle
  bestanden**. Beide Korrekturen gehören zum danach vollständig geprüften
  finalen Paket; ein früherer grüner Lauf vor dem JPEG-Fix ist nicht das Gate.
- Im ersten Gesamtlauf scheiterte nur die neue TLS-Testserver-Prüfung an einem
  Windows-Verbindungsreset nach korrekter Zertifikatsablehnung. Die App sendete
  kein LOGIN. Der Test akzeptiert nun sowohl TLS-Alert als auch TCP-Abbruch
  ausschließlich im erwarteten Negativfall; der komplette Wiederholungslauf ist
  oben dokumentiert. Der fehlgeschlagene Lauf ist kein Freigabenachweis.

Tests verwenden temporäre Datenbanken, synthetische Konten/Prozesse und einen
lokalen TLS-Server. Es wurden keine echten Mailzugangsdaten verwendet, keine
externen KI-Aufträge gestartet und keine realen Konten oder Einladungen für Tests
angelegt. Windows/Python 3.12 ist kein neuer GitHub-CI-Lauf auf Ubuntu/Python 3.11.
Die Änderungen sind weiterhin nicht committet oder gepusht.

## Paket und Rolloutgate

Paket: `.tmp/releases/1.8.5-security/rezepte-1.8.5.tar.gz`, **132 Dateien**.
SHA-256: `46f0f300f22b6f8ad37b522bd960128b84d752955e0846ba4d134d7488c82f4d`.
Schema bleibt 263. Das endgültige Paket umfasst die nach dem ersten
Reviewversuch ergänzte Poppler-Abhängigkeit. Das erste Paket und seine
fehlgeschlagene Review-Probe bleiben als Diagnoseartefakte erhalten.

Lokale Belege: `.tmp/backend-final-1.8.5.xml`,
`.tmp/coverage-final-1.8.5.json`, `.tmp/web-final-1.8.5.log`,
`.tmp/expo-final-1.8.5.log`, `.tmp/deployment-guards-1.8.5.log`.
Linux-Review CT117 bestanden: 132 installierte Dateien entsprechen dem Manifest;
Version 1.8.5, Schema 263, SQLite/Fremdschlüssel, bestehende Konten, Gastzugang,
acht Schreib-/Adminverbote und globale Druck-PDFs sind geprüft. Eine anonyme
60-MB-Formularankündigung wird vor Parsing mit 413 abgelehnt. Synthetische Tests
prüfen den tatsächlichen Speichervorgang mit `..`, fremde Canonical-Identität,
eine echte 20000×20000-Punkt-PDF mit begrenztem JPEG sowie Downloadabbruch,
Kindprozesse und Bereinigung. Alle früheren Identitäts-/Haushalts-/Bild-
Nebenläufigkeitsproben bestehen weiterhin. Review ist wieder ausgeschaltet.
Bericht: `.tmp/releases/1.8.5-security/ct117-report.json`.
Review-Rückfallstand: `/opt/scrapper-code-backups/state-20261004-144936-331`.

Produktion CT200 bestanden: ebenfalls alle **132 installierten Dateien**
hashgeprüft und dieselben **23 synthetischen Prüffelder** bestanden. Die
**229 aktiven globalen Rezepte** und ursprünglichen Werte in **22 Tabellen**
bleiben erhalten, ebenso alle bestehenden Konten und die Konfiguration.
Keine Quellenmetadaten wurden verändert, kein Rezept entfernt. Die 57
bestehenden Hintergrundaufträge bleiben abgeschlossen; kein aktiver Import.
Der historische unklar zugeordnete Kochzwischenstand bleibt mit seinen
ursprünglichen Feldern erhalten und keinem neuen Benutzer zugewiesen.
Keine privaten Waisen oder neuen realen Konten/Einladungen durch die Tests.

Abschließende Liveprüfung: `/healthz` und `/readyz` liefern **HTTP 200**, `ok: true`
und **1.8.5**. `scrapper-web.service`, `scrapper-job.timer` und
`scrapper-db-backup.timer` sind aktiv. Review ist gestoppt und ausgehängt.
Produktion-Rückfallstand mit Code, Datenbank, Konfiguration und Units:
`/opt/scrapper-code-backups/state-20261004-145554-17311`.
Weitere Belege: `.tmp/releases/1.8.5-security/ct200-report.json`,
`.tmp/releases/1.8.5-security/checks.json`, `.tmp/final-live-1.8.5.log`
und `.tmp/final-accounts-1.8.5.log`.

## Offene Punkte und Plattformgrenzen

- Mailabsenderprüfung braucht die erlaubten Adressen und einen überprüften
  Provider-/Authentifizierungspfad; die dazu gestellte Betreiberfrage ist offen.
- Kontoselbstlöschung, Einladungstoken in URLs/Logs, statische Cacheoptimierung
  und CSP ohne `unsafe-eval` bleiben offen.
- Hintergrundtimer und restliche visuelle/Begriffs-Konsistenz benötigen eine
  eigene Geräte-/Clientabnahme. `ios-swift` ist der primäre native Client.
- Expo-Quelländerungen sind geprüft, aber kein neuer App-Build wurde erstellt
  oder veröffentlicht. Kein Xcode, XCTest, Simulator, IPA oder TestFlight.
- Externe Datenschutz-/Vertragsnachweise sind nicht aus dem Quellcode bestätigt.

Integrierter Arbeitsstand:
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout bleibt erhalten. Kein Commit, Push oder Branchwechsel.
