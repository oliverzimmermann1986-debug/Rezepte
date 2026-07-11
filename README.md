# Rezeptliebe

Mobile-First-Webanwendung zum Sammeln, Analysieren, Einsortieren und schnellen Wiederfinden eigener Rezepte. Die **Rezeptbibliothek ist die Startseite und der Hauptbereich**. Inhalte kommen aus zwei IMAP-Postfächern oder aus unterstützten TikTok-/Instagram-Links in E-Mails.

## Hauptfunktionen

- Schnelle Rezeptsuche über Name, Gerichtstyp, Kategorie und Beschreibung
- Filter, Sortierung und mobile Rezeptkarten
- Detailansicht mit Video, Bild oder PDF
- Automatischer Import aus Rezept- und Hochzeits-Postfach
- Klassifizierung über lokales Ollama oder OpenAI-kompatible API
- Prüfbereich für unsichere Ergebnisse
- Historie mit Bearbeiten, Verschieben und Löschen
- Automatischer Import-Zeitplan über systemd
- Verifizierte SQLite-Datenbanksicherungen
- Responsive Oberfläche für Smartphone, Tablet und Desktop

Die frühere allgemeine Dateisynchronisierung ist nicht mehr Bestandteil der Anwendung.

## Aufbau

| Bereich | Aufgabe |
|---|---|
| Rezepte | Startseite, Volltextsuche, Filter und Medienansicht |
| Import | Manueller Lauf, Zeitplan, Fortschritt und Jobprotokolle |
| Prüfen | Unsichere KI-Ergebnisse korrigieren oder überspringen |
| Historie | Bereits verarbeitete Einträge verwalten |
| Einstellungen | Postfächer, Pfade, KI, Download und Zugangsdaten |

## Installation in einem Debian-/Proxmox-LXC

```bash
git clone https://github.com/appear7240/Scrappercontainer.git /opt/scrapper
cd /opt/scrapper
sudo bash proxmox/install.sh
```

Der Installer:

- installiert Python, FFmpeg, SQLite und die festgelegten Python-Abhängigkeiten,
- erstellt den Benutzer `scrapper`,
- erzeugt sichere Zugangsdaten beim Erststart,
- richtet Webdienst, Import-Timer und tägliche Datenbanksicherung ein,
- bindet den Webdienst standardmäßig nur an `127.0.0.1:8000`,
- entfernt beim Update nicht mehr verwendete Alt-Units und Alt-Konfiguration.

Das Initialpasswort liegt einmalig unter:

```bash
cat /opt/scrapper/data/.initial-password
```

## Konfiguration

Produktive Konfiguration:

```text
/opt/scrapper/data/config.yaml
```

Wichtige Abschnitte:

- `web`: Benutzer, Passwort, Bind-Adresse und vertrauenswürdige Proxys
- `paths`: Rezept-, Hochzeits-, Temp- und Logverzeichnisse
- `mail.recipe`: Rezeptpostfach
- `mail.wedding`: Hochzeitspostfach
- `ai`: Ollama oder OpenAI-kompatibler Provider
- `ytdlp`: Download-Binary, Cookies und Grenzen
- `schedule.scraper_interval`: systemd-OnCalendar-Ausdruck

Nach der Installation sollten zuerst Passwort, E-Mail-Konten, Zielpfade und KI-Verbindung in der Oberfläche geprüft werden.

## Rezeptablage und Suche

Neue Rezeptdatensätze erhalten eine stabile interne ID. Die Suche verwendet Metadaten aus der Datenbank:

- Name
- Rezepttyp
- Kategorie
- Beschreibung
- Verarbeitungszeitpunkt
- Quelle

Bestehende Installationen werden beim ersten Aufruf der Rezeptseite nachindexiert. Vorhandene `info.json`-Dateien und die Verzeichnisstruktur werden dazu defensiv eingelesen. Medien werden ausschließlich aus dem konfigurierten Rezeptstamm ausgeliefert.

## Unterstützte Medien

- Video: MP4, WebM, MKV, MOV
- Bild: JPG, JPEG, PNG, WebP
- Dokument: PDF

Die Detailansicht streamt beziehungsweise öffnet die zuerst gefundene geeignete Mediendatei. Pfadausbrüche außerhalb des Rezeptverzeichnisses werden blockiert.

## Import

Manueller Start in der Oberfläche oder per API:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/scraper/run
```

Im normalen Betrieb ist die angemeldete Weboberfläche zu verwenden. Der Import verarbeitet nur unterstützte Social-Media-URLs und begrenzt Laufzeit, Dateigröße, Mailgröße sowie Zahl und Größe der Anhänge.

Zeitplan anzeigen:

```bash
systemctl list-timers scrapper-job.timer
```

Logs:

```bash
journalctl -u scrapper-web -f
journalctl -u scrapper-job.service -n 200 --no-pager
```

## Datenbanksicherung

Die SQLite-Datenbank wird über einen eigenen Timer online und konsistent gesichert. Sicherungen liegen unter:

```text
/opt/scrapper/data/backups/
```

Manuell sichern:

```bash
runuser -u scrapper -- /opt/scrapper/venv/bin/python -m app.cli db-backup
```

Vor Updates erzeugt der Installer zusätzlich ein Pre-Update-Abbild.

## Update

```bash
cd /opt/scrapper
sudo bash proxmox/install.sh
```

Der Installer stoppt betroffene Dienste, sichert die Datenbank, führt ausschließlich einen Fast-Forward auf den gewählten Branch aus, aktualisiert die virtuelle Umgebung und startet die Dienste wieder.

## Sicherheit

- Passwort-Hashing und serverseitig signierte Sitzungen
- Sitzungsinvalidierung bei Passwortänderung
- Same-Origin-Schutz für schreibende API-Aufrufe
- restriktive Security Header
- vertrauenswürdige Proxy-Netze statt blindem Forwarded-Header-Vertrauen
- lokale Standardbindung
- gehärtete systemd-Units
- strikte Pfadgrenzen für Medien, Logs und Datei-Browser
- root-eigene, eng begrenzte Helfer für Zeitplan und optionale HDD-Aktion
- atomare Konfigurationsspeicherung mit Backup

## Entwicklung und Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -q
node --check app/static/app.js
bash -n proxmox/install.sh
```

## Grenzen der lokalen Prüfung

Echte IMAP-Postfächer, Social-Media-Downloads, Ollama/OpenAI, Reverse-Proxy, systemd und optionale Shelly-/HDD-Steuerung müssen im Zielsystem mit den dortigen Zugangsdaten und Mounts geprüft werden.
