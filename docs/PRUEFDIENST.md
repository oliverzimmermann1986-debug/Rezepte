# Rezepte auf dem zentralen Proxmox-Prüfdienst

Profil `rezepte`, VM 110, bestehender Dienst `pruefdienst.service`. Die Prüfdatei
`.pruefdienst.json` aktiviert die Prüfung am jeweiligen Commit. Der zentrale Dienst
entdeckt `main` und Tags. **Alle Linux-Prüfungen laufen ausschließlich dort.**
Für andere Branches und Pull Requests im selben Repository übermittelt der kurze
GitHub-Workflow `Pruefdienst request` nur den exakten Quell-SHA als idempotenten Tag
`ci/rezepte-request/<SHA>`. Er checkt keinen Code aus und führt keine App-Tests aus.
Ein erfolgreicher Auftrag ist noch kein erfolgreicher Test: maßgeblich bleibt
`pruefdienst/release` am angeforderten Commit. Bei Pull Requests wird der Head-SHA
geprüft; `main` wird direkt vom Prüfdienst erkannt. Die Prüfung eines synthetischen
GitHub-Merge-Commits ist damit nicht verbunden.
Fork-PRs erhalten keinen Schreibtoken; ein Maintainer muss deren Stand zuerst in
einen vertrauenswürdigen Repository-Branch übernehmen und dort prüfen lassen.
Ein technischer Tag `ci/rezepte-…` prüft den genauen Küchenbranch-Stand ebenfalls
auf Proxmox. GitHub kann dafür zusätzliche Buildprüfungen starten; ein technischer
Tag veröffentlicht keine App und löst keinen signierten Upload aus.

## Prüfplan

`python tools/ci_verify.py` führt den vollständigen Plan auf Proxmox aus;
derselbe Einstieg kann lokal mit den angegebenen Runtimes verwendet werden:

- Python 3.11 und Node 20.19.4 wie in den bisherigen Repo-Workflows.
- Python-Syntax, Ruff, alle versionierten JavaScript-Dateien, Shellsyntax und YAML-Konfigurationen.
- Alle Node-Tests in `tests`, `native-ios/tests` und `native-ios/scripts`.
- Expo: Installation mit `npm ci`, Typecheck, Lint, Produktions-/Entwicklungs-/Preview-Konfiguration und
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
Prüfprotokoll-Repository. Die frühere Linux-Quality-Action und der doppelte
Linux-Expo-Prüfjob entfallen. JUnit und Browser-/Bundledateien entstehen im
isolierten Container; der zentrale Dienst archiviert daraus JSON-Bericht und
Rohlog, aber keine Browserbilder oder vollständigen Bundles. VM-Diagnose und
fachliches Testergebnis sind separat.

Swift-/Xcode-Tests, Simulatorprüfungen, signierte IPA, TestFlight und Apple-Freigabe
laufen weiterhin im macOS-Workflow; VM 110 betreibt Linux und kann Xcode nicht ausführen.
Technische CI-Tags starten keine zusätzlichen macOS-Prüfungen. Signierte neue
SwiftUI- und Legacy-Expo-Uploads verlangen vorher einen erfolgreichen
`pruefdienst/release`-Status auf exakt dem zu bauenden SHA. Teilstatus, ausstehende,
fehlende oder rote Ergebnisse sowie API-Ausfälle erlauben keinen Upload.
Der nichtprivilegierte Linux-Container prüft keine
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
