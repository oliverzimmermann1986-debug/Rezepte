# Rezepte 1.5.0

## Sicherheit

- Unveränderliche Benutzer-ID und Session-Version in Anmeldesitzungen
- Exakte Origin-/Referer-Prüfung, Upload-Limits und getrennte Login-Limits
- Rollenbasierter Schutz aller mutierenden Verwaltungsfunktionen
- Individuelle, nur gehasht gespeicherte Share-Intake-Tokens
- Root-verwaltete systemd-Proxy-Overrides und enger Schedule-Helper

## Datenintegrität und Betrieb

- Atomare DB-/Dateisystem-Workflows mit Kompensation bei Fehlern
- Singleton-Jobs, persistente Abbrüche und robuste Worker-Leases
- Readiness, Watchdog, begrenzter Shutdown und abgesicherter Restore
- Atomare Proxmox-Updates mit Rollback sowie gehärteter Video-Archiver

## Native App 1.0.1

- Server- und benutzerspezifische Cache-Isolation
- Schutz vor verspäteten Antworten aus abgelaufenen Sitzungen
- Session-Aktualisierung bei App-Aktivierung

## Validierung

- 393 Python-Tests bestanden, 2 übersprungen
- Ruff, Python-Bytecode, Shell-Syntax und Git-Diff-Prüfung bestanden
- TypeScript-Typecheck und Expo-Lint bestanden
