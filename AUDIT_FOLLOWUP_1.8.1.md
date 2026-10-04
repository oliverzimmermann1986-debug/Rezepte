# Audit-Nacharbeit 1.8.1

Historischer Bericht. Aktueller Stand: [Audit-Nacharbeit 1.8.2](AUDIT_FOLLOWUP_1.8.2.md).

Stand: 03.10.2026. Backend und Web laufen auf Produktion CT200 mit Version
**1.8.1**, Datenbankschema **261**. Review CT117 wurde ebenfalls geprüft und
anschließend wieder ausgeschaltet und ausgehängt.

Weitere Prüfungen haben zwei zusätzliche Grenzfälle bei wiedervergebenen
Benutzernamen und beim Haushaltsbeitritt während eines Importaufrufs bestätigt.
Sie sind in 1.8.2 nachgearbeitet; die ursprünglichen Reproduktionen stehen in
[weitere Kontenprüfungen](AUDIT_ACCOUNT_EDGES_1.8.1.md).

Der integrierte Arbeitsstand liegt unter
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout enthält weiterhin den älteren Stand 1.5.10;
seine Arbeitsdateien wurden erhalten. Die eingereichte Prüfung bezog sich auf
diesen älteren Checkout. Für weitere Arbeiten den angehängten Worktree verwenden.

## Kritische Befunde

| Befund | Ergebnis | Nachweis |
| --- | --- | --- |
| K1: private Rezepte über den PDF-Pfad lesbar | Behoben. `/recipe/…` verwendet jetzt dieselbe Haushaltszuordnung wie `/api/…`. Fremde private Rezepte liefern 404 vor dem PDF-Renderer; globale PDFs bleiben lesbar. | `app/tenancy.py:225`, `tests/test_tenants.py:87` |
| K2: Migration scheitert mit SQLite-Tupelzeilen | Bereits im integrierten Stand 1.8.0 behoben. Die Migration verwendet vorübergehend `sqlite3.Row` und stellt die ursprüngliche Einstellung wieder her. Regressionen für Bestandsstände 231, 232 und 260 bestehen. | `app/tenancy.py:52`, `tests/test_tenants.py:583` |

Der K1-Regressionstest lieferte vor der Korrektur HTTP 200 für ein fremdes
privates Rezept. Nach der Korrektur erhalten Gast und fremder Haushalt 404;
Eigentümer und Leser globaler Rezepte erhalten 200. Dieser Nachweis verwendet
isolierte Testdaten. In Produktion wurden keine privaten Prüfrezeptdaten oder
echten Konten angelegt.

## Weitere umgesetzte Korrekturen

- Freigabelinks globaler Rezepte sind nur im erzeugenden Haushalt auflistbar
  und widerrufbar. Auch die eingeladene zweite Person kann diese Links verwalten;
  andere Haushalte erhalten keinen Zugriff. Öffentliche Freigaben behalten ihre
  Prüfung des signierten, widerrufbaren Tokens.
  Quellen: `app/tenant_db.py:31`, `tests/test_tenants.py:116`.
- Der Job-Livestream verlangt Administratorrechte und prüft die Berechtigung
  während einer offenen Verbindung erneut. Web-Gäste und normale Konten öffnen
  weder diesen Stream noch dessen Polling-Ersatz.
  Quellen: `app/routes/api_events.py:36`, `app/static/app.js`,
  `tests/test_tenants.py:138`, `tests/web_async.test.cjs`.
- Fehlgeschlagene und gesperrte Browser-Anmeldungen protokollieren keine
  eingegebenen Benutzernamen und IP-Adressen mehr.
  Quellen: `app/main.py:629`, `tests/test_tenants.py:155`.
- Web-, Expo- und SwiftUI-Rezeptkarten unterscheiden fünf Zustände:
  laufende Auswertung, fehlgeschlagene Auswertung, fehlende Beschreibung,
  manuelle Pflege und kochfertig. „Kochfertig“ erfordert erfolgreiche Auswertung,
  Zutaten, Schritte und keinen Pflegehinweis.
  Quellen: `app/static/features/recipes.js:9`,
  `native-ios/src/lib/recipe-status.ts`,
  `ios-swift/Rezepte/Models/Models.swift:238`.
- Die semantischen Web-Statusfarben verwenden die dunkleren Expo-Farben.
  Der berechnete Textkontrast besteht auf beiden hellen Flächen mindestens
  4,5:1. Web-Favoriten und Expo-Entfernen-Schaltflächen sind mindestens
  44 Pixel beziehungsweise Punkte groß. Die vorhandenen Expo-Hauptbuttons
  waren bereits mindestens 50 Punkte hoch.

## Zusätzlich beim Rollout gefundene Fehler

Der erste Review-Rollout übersprang eine gleich große Versionsdatei mit gleichem
Zeitstempel. Die externe Hashprüfung erkannte die Abweichung, bevor Produktion
dieses Paket erhielt. Der Updater vergleicht jetzt beim Installieren und
Wiederherstellen per Prüfsumme, kontrolliert das installierte Release gegen
das Dateimanifest und erwartet die Version des Quellpakets. Python-Bytecode
verwendet Inhalts-Hashes. Drei neue Regressionen prüfen diese Fälle.
Quellen: `proxmox/update-local.sh:152`, `tools/release_state.py:63`,
`tests/test_release_state.py`.

Die erste Produktionsprüfung fand einen bestehenden PDF-Layoutfehler:
lange Beschreibungen, Zutatenzeilen oder einzelne Schritte konnten nicht über
mehrere Seiten umbrechen und verursachten HTTP 500. Die Tabellen erlauben jetzt
einen Umbruch innerhalb der Zeile. Drei Regressionen mit jeweils 120 Abschnitten
prüfen Textbestand und Reihenfolge über mehrere Seiten. Die drei betroffenen
Bestandsrezepte 252, 255 und 259 liefern nach dem erneuten Rollout jeweils
HTTP 200, gültige PDF-Daten und `private, no-store`.
Quellen: `app/recipes/recipe_pdf.py`, `tests/test_recipe_pdf.py:75`.

## Prüfung am laufenden Dienst

- Alle **127 installierten Release-Dateien** stimmen mit ihren SHA-256-Werten
  überein. Gesundheits- und Bereitschaftsprüfung melden 1.8.1 und HTTP 200.
- **229 aktive globale Rezepte** sind unverändert erhalten. Der Vergleich mit
  dem geschützten Datenbanksnapshot prüft elf Tabellen, darunter Benutzer samt
  Passworthashes und Rollen, Rezeptinhalte, Zutaten, Schritte, Tags, Einkauf,
  Planung, Pending, Verlauf und Rezeptversionen. Bei diesem Update wurden
  weder Papierkorbrezepte entfernt noch Quelldaten ergänzt.
- SQLite-Integrität und Fremdschlüssel bestehen. Die vorhandenen Import- und
  Sicherungstimer bleiben aktiv; Aktivierung und Zeitpläne sind erhalten.
- Gastanmeldung für Browser und API, globale Bibliothek, globale PDFs und
  Registrierungsseite bestehen. Acht unzulässige Gastzugriffe erhalten 403,
  darunter Job-Events, Importe und Einladungen. Ohne Anmeldung bleibt die
  Rezept-API gesperrt.
- Review: sieben Demorezepte, vorhandene Benutzer erhalten; Importtimer bleibt
  deaktiviert. CT117 ist wieder ausgeschaltet und nicht eingehängt.

Das finale Archiv liegt unter
`.tmp/releases/1.8.1-security/rezepte-1.8.1.tar.gz`.
SHA-256: `3483b109f552001f6f409481f17e534ec11c5e699a408572163f54cffaa37c7b`.
Maschinenlesbarer Nachweis: `.tmp/releases/1.8.1-security/rollout-report.json`.
Die vorherigen Versuche sind separat in diesem ausgeschlossenen Verzeichnis
aufbewahrt. Keine Einladungsnachrichten wurden versendet.

Geschützte Rückfallsicherung auf CT200:
`/opt/scrapper-code-backups/state-20261003-230402-14149`.
Auf CT117: `/opt/scrapper-code-backups/state-20261003-230206-331`.
Diese Sicherungen enthalten private Laufzeitdaten und gehören nicht in Git.

## Lokale Prüfungen und Grenzen

- Gesamtlauf nach der PDF-Korrektur: **749 bestanden, 2 übersprungen**,
  zunächst ein fehlgeschlagener veralteter Guard-Test in 339,13 s.
  Dieser erwartete die alte Zeichenfolge `rsync -a --delete` und wurde auf
  die notwendigen Optionen einschließlich Prüfsummenprüfung angepasst.
  Danach bestehen **40 Prüfungen** für Update, Release und API-Kompatibilität.
  Produktivcode wurde nach dem Gesamtlauf nicht mehr verändert.
- **69 gezielte Backend-Prüfungen** für PDFs, Freigaben, Haushalte, Migration
  und Release bestehen. Die beiden übersprungenen OCR-Prüfungen im Gesamtlauf
  benötigen das lokal fehlende Tesseract.
- Web: **32 Node-Tests bestanden**. Expo: **25 Node-Tests**, TypeScript-Prüfung,
  Lint und iOS-JavaScript-/Hermes-Export erfolgreich. Der Export ist keine IPA.
  Export-SHA-256:
  `002e2637aeae03ff8190d33fa7069cc9d5c10e0926ca7d3d01bf0958d6055d33`.
- SwiftUI: alle **43 Swift-Dateien** ohne Parser-Syntaxfehler. Neue XCTest-Fälle
  für die Statusklassifikation liegen vor. Unter Windows wurden weder
  Swift-Typprüfung, Xcode-Build, XCTest noch Simulatorprüfung ausgeführt.
- Ruff, Shell-Syntaxprüfung des Updaters und `git diff --check` bestehen.
  Kein Commit, Push, Branchwechsel, IPA-Build oder TestFlight-Upload erfolgte.

## Weitere offene Auditpunkte

- Kosten- und Nutzungskontingente für Importe. Registrierung hat bereits ein
  Limit von zehn Versuchen pro IP in zehn Minuten; ein Importkontingent und ein
  eigenes Gast-Anmeldelimit wurden hier nicht ergänzt. Gäste dürfen keine
  Importe starten.
- Selbstständige Kontolöschung und die Datenbereinigung des letzten
  Haushaltsmitglieds. Die bestehende Admin-Löschung erhält den Zugriff einer
  zweiten Person durch Eigentümerwechsel; das ersetzt keine Kontolöschung
  durch den Kontoinhaber.
- Einladungstoken in URL-Abfragen und damit möglichen Access-Logs.
- Admin-Fallbacks in Legacy- und Systemkontexten wurden nicht vollständig
  erneut bewertet; dafür wird kein geschlossener Sicherheitsbefund behauptet.
- CSP mit `unsafe-eval` sowie widersprüchliche Ollama-Angaben in der README.
- Weitere GUI-Vereinheitlichung: Statusbegriffe außerhalb der Rezeptkarten,
  Schriften, Radien, Einkaufsbegriffe, englische Resttexte, doppelte
  Buttondefinitionen und übrige Theme-Reste. Das ist weiterhin Nacharbeit.
- Native Änderungen liegen im Quellstand. Die primäre App ist im integrierten
  Stand SwiftUI unter `ios-swift/`; Expo unter `native-ios/` ist der ältere Client.
  Ein neuer nativer Build und die Prüfung auf einem iPhone stehen aus.
