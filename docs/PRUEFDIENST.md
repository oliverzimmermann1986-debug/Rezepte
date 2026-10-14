# Rezepte auf dem zentralen Proxmox-Prüfdienst

Profil `rezepte`, VM 110, bestehender Dienst `pruefdienst.service`. Die Prüfdatei
`.pruefdienst.json` aktiviert die Prüfung am jeweiligen Commit. Der zentrale Dienst
entdeckt `main` und Tags; andere Branches werden von GitHub Actions geprüft.
Ein technischer Tag `ci/rezepte-…` prüft den genauen Küchenbranch-Stand ebenfalls
auf Proxmox. Er erstellt weder einen App-Build noch eine Veröffentlichung.

## Prüfplan

`python tools/ci_verify.py` führt denselben Plan auf GitHub und Proxmox aus:

- Python 3.11 und Node 20.19.4 wie in den bisherigen Repo-Workflows.
- Python-Syntax, Ruff, alle JavaScript-Dateien unter `app/static`.
- Alle Node-Tests in `tests`, `native-ios/tests` und `native-ios/scripts`.
- Expo: Installation mit `npm ci`, Typecheck, Lint, Konfiguration und
  iOS-JavaScript-Bundle. Keine Signierung und kein Upload.
- Vollständige Pytest-Suite einschließlich vorhandener API-, Rechte-, Import-,
  PDF/OCR-, SQLite-Migrations-, Update-, Wochenplan-, Portions- und Einkaufsprüfungen.
- Vorhandene Chromium-Testdateien werden als eigene Browserstufe ausgeführt.
  Namen und Zahl stehen im Bericht; fehlen sie in einem älteren Branch, wird
  keine Browserabnahme behauptet.
- Gemeinsame Branch-Coverage für Backend und Videoarchiv: mindestens 48 Prozent,
  entsprechend der bisherigen CI-Schwelle.

Alle geplanten Stufen beginnen als `not_run`. Fehler lassen unabhängige Stufen
weiterlaufen. Fehlende Voraussetzungen, leere Testreports und unerwartete übersprungene
Tests verhindern Grün. Zulässige übersprungene Tests stehen mit Namen/Grund im Bericht:
Linux-Installer-Eigentümertest benötigt root; historischer Upgradevergleich kann bei
einem über Schema 260 hinausgehenden main entfallen. Fehlendes Bash, OCR, Font oder
Playwright wird dagegen nicht still akzeptiert.

Tests benutzen neue synthetische Konfigurationen und Datenbanken im Container.
Sie erhalten keine Betreiberdateien, Produktionsdaten, KI-Zugangsdaten, GitHub-Token,
Host-Mounts oder Docker-Sockets. Kein Zugriff auf CT117/CT200 ist nötig.

## Ergebnisse und Grenzen

Verbindlich ist GitHub-Status `pruefdienst/release` am geprüften SHA. Der bestehende
Dienst veröffentlicht Server-, Client- und Browser-Teilstatus für die passenden
Gate-IDs. Rohlogs und `reports/unified-quality-report.json` gehen ins private
Prüfprotokoll-Repository. GitHub Actions lädt zusätzlich JUnit und Browser-/Bundleartefakte
für sieben Tage hoch. VM-Diagnose und fachliches Testergebnis sind separat.

Swift-/Xcode-Tests, Simulatorprüfungen, signierte IPA, TestFlight und Apple-Freigabe
laufen weiterhin im macOS-Workflow. Der nichtprivilegierte Linux-Container prüft keine
rootabhängige Installer-Eigentümerabnahme und keine reale Produktionsmigration.

## Image bereitstellen

Operator-Kontext auf VM 110; niemals Repo-Code auf dem VM-Host ausführen. Der vorhandene
Controller bleibt unverändert. `tools/ci/Dockerfile` baut ein Image mit Python 3.11,
checksumgeprüftem Node 20.19.4, Chromium für Playwright 1.61.0, FFmpeg und OCR.

1. Offizielles `python:3.11-slim-bookworm` laden und `RepoDigests[0]` als `BASE_IMAGE`
   verwenden; Node-SHA256 aus der offiziellen `SHASUMS256.txt` übernehmen.
2. Image mit `docker build -f tools/ci/Dockerfile --build-arg BASE_IMAGE=python@sha256:…
   --build-arg NODE_SHA256=…` auf dem dedizierten ersten Docker-Daemon bauen.
3. Per `docker save`/`docker load` auf den zweiten dedizierten Daemon übertragen.
   Auf beiden denselben unveränderlichen `sha256:…`-Image-Identifier prüfen.
4. Bestehende Profile in `/etc/ci-runner/service.json` erhalten, `images.rezepte`
   ergänzen, mit `service.cjs.configuration` validieren und atomar als root/0600 schreiben.
5. Dienst erst bei freier Queue und freien Prüfplätzen neu starten. Zustandsdatei und
   andere Profile erhalten. Danach den opt-in SHA und seinen GitHub-Status prüfen.

Browser-Version oder Runtime-Pins nur zusammen mit einem neuen geprüften Image ändern.
Die Root-Konfiguration erlaubt keine vom Repository gewählten Registry-Images.
