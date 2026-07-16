# Rezeptliebe

Proxmox-LXC-Container für den Scraper-Job:

**Rezeptbibliothek mit TikTok/Instagram-Import** — zieht Links aus zwei separaten E-Mail-Postfächern (Rezepte + Hochzeit), lädt die Videos mit `yt-dlp`, lässt sie von einer **lokalen Ollama-Instanz** klassifizieren und sortiert sie in passende Ordner.

Der Job wird über ein **Web-Interface** verwaltet (Konfiguration, manuelles Starten, Pending-Auflösung, Logs, Historie). Externe Erreichbarkeit ist explizit für **Cloudflare-Tunnel + Cloudflare Access** (MFA-Layer) ausgelegt.


## Oberfläche

- Rezeptsuche ist die Startseite
- festes Butter-Yellow-Design ohne alte Theme-Umschaltung
- Favoriten und Einkaufsliste direkt in der Hauptnavigation
- Mobile-First mit vollständig freigehaltener Bottom-Navigation und iPhone-Safe-Area
- erweiterte Filter als Side-Sheet am Desktop und Bottom-Sheet auf Smartphones
- keine externen Schriftarten oder Design-CDNs
- zentraler Admin-Reiter für Import, Qualität, Versionen, PDF/Scan, Suche und Wartung
- automatische PDF-/Scan-Aufbereitung mit Ausrichtung, OCR, Randbeschnitt und Seiteneditor


---

## Architektur auf einen Blick

```
E-Mail-Inbox (Recipe)  ─┐
E-Mail-Inbox (Wedding) ─┤
                        ▼
                  ┌─────────────┐         ┌──────────────────┐
                  │  IMAP-Fetch │ ──URLs─►│  yt-dlp Download │
                  └─────────────┘         └─────────┬────────┘
                                                    ▼
                                          ┌─────────────────────┐
                                          │   Ollama-Cascade    │
                                          │  fast → fallback    │
                                          └────────┬────────────┘
                                                   ▼
                          ┌───────────────────────┴────────────────────┐
                          │                                            │
                          ▼ Auto: Confidence hoch                      ▼ Pending: User entscheidet im Web-UI
                  ┌──────────────────┐                         ┌──────────────────┐
                  │ FS: recipe_dir/  │                         │  SQLite pending  │
                  │     wedding_dir/ │                         │  + video in temp │
                  └──────────────────┘                         └──────────────────┘
```

Der Job läuft als systemd-Timer (Default `*:0/30` = alle 30 min) oder per Button im Web-UI. File-Locks verhindern doppelte Läufe zwischen Web und CLI.

---

## Features

**Hardened Web-Auth**
- Bcrypt-Passwort-Hashing (Klartext-PWs werden beim ersten Start automatisch gehasht)
- Auto-generierter Session-Secret beim Erststart
- Rate-Limiter auf `/login` (5 Fehlversuche / 10 min → 15 min Block pro IP)
- Security-Header (CSP, HSTS, X-Frame-Options, …)
- CLI: `python -m app.cli set-password` / `rotate-secret`
- App **verweigert den Start** bei aktivem Default-Login `admin/changeme`
- `/api/docs` standardmäßig **aus** (Opt-in via `SCRAPPER_ENABLE_DOCS=1`)

**Datenintegrität**
- SQLite mit WAL-Mode + `synchronous=FULL` + 10s busy_timeout
- Indizes auf häufige Queries
- `pending_add` ist idempotent (Status/Timestamp bleiben bei Re-Insert erhalten)
- Path-Whitelists auf alle FileResponse-Endpoints (defense in depth)
- Stale-Job-Recovery beim Start (alte `running`-Jobs werden auf `error` gesetzt)

**Admin-Zentrale**
- Importzentrale mit offenen Prüfungen, Fehlern, laufenden Jobs und Verlauf
- Rezept-Versionen vor inhaltlichen Änderungen mit Vergleich und Wiederherstellung
- PDF-/Scan-Stapelverarbeitung sowie manueller Seiteneditor
- Suchsynonyme, Ausschlüsse (`-Zutat` / `ohne Zutat`) und transparente Tippfehlerkorrektur
- Datenbank-, Medien-, Backup-, FTS-, Temp- und VACUUM-Wartung mit protokollierten Läufen
- technische Module getrennt in `api_admin.py`, `pdf_processing.py` und `recipes/search.py`

**Robustheit**
- File-Lock (`fcntl.flock`) zwischen Web-Trigger und systemd-CLI
- Log-Rotation aller Job-Logs (älter als 30 Tage werden bei jedem Job-Start aufgeräumt)
- yt-dlp Failed-Tracking: nach 3 fehlgeschlagenen Versuchen wird die URL als „aufgegeben" gespeichert und nicht mehr probiert
- IMAP-Retry mit Backoff (3 Versuche, 1s/4s)
- Ollama-Health-Check beim Job-Start (bricht ab statt 50 sinnlose Pending-Items zu erzeugen)
- Thread-safe Cancel für laufende Import- und Analysejobs
- Async Telegram raus, alle Notifications nur noch in Web-UI


---

## Admin-Zentrale

Der Reiter **Admin** ist für alle angemeldeten Benutzer sichtbar. Er bündelt bewusst alle technischen und qualitätssichernden Funktionen, damit die normale Rezeptansicht übersichtlich bleibt.

- **Importzentrale:** offene Prüfungen, fehlgeschlagene Downloads, laufende Jobs und letzte Importe
- **Qualität:** bestehende KI-Prüfungen, Duplikate und Qualitätsfunde
- **Versionen:** Snapshot, Vergleich und Wiederherstellung eines Rezeptstands
- **PDF & Scan:** Stapelverarbeitung, OCR sowie Seiten drehen, sortieren oder löschen
- **Suche:** Synonyme pflegen und den FTS-Index neu aufbauen
- **Wartung:** Integrität, Testbackup, Medienprüfung, Temp-Bereinigung und VACUUM
- **Stammdaten/Einstellungen/Papierkorb:** bestehende Verwaltungsfunktionen an einem Ort

Versionen erfassen strukturierte Rezeptdaten. Binärmedien wie Videos, frei ersetzte Bilder oder PDF-Originale werden nicht in der Datenbankversion dupliziert. PDF-Änderungen legen deshalb separat ein Original im Datenverzeichnis ab.

Details stehen in [`ADMIN_CENTER.md`](ADMIN_CENTER.md) und [`PDF_PROCESSING.md`](PDF_PROCESSING.md).

## Setup

### 1. Container anlegen

```bash
# Auf dem Proxmox-Host:
bash proxmox/create-container.sh
```

Default: unprivileged LXC, Debian 12, 2 GB RAM, 16 GB Disk. Du wirst nach Container-ID, Storage und Netz gefragt.

### 2. App installieren

```bash
pct enter <ctid>
cd /opt && git clone https://github.com/appear7240/Scrappercontainer.git scrapper
cd scrapper
bash proxmox/install.sh
```

Das Install-Script erzeugt automatisch:
- einen `scrapper`-User
- ein **zufälliges Initial-Passwort** (gespeichert in `data/.initial-password`)
- ein **zufälliges `secret_key`** (48 Zeichen)
- die systemd-Units (`scrapper-web`, `scrapper-job.timer`)

```
🌐 Web-Interface (LOKAL):    http://127.0.0.1:8000
👤 Login:                    admin
🔑 Initial-Passwort:         (siehe Ausgabe oder data/.initial-password)
```

Der uvicorn-Bind ist standardmäßig **`0.0.0.0:8000`**, weil die häufigste
Proxmox-Topologie cloudflared in einem **separaten Container** hat
(siehe Variante B unten). Wenn du cloudflared im selben Container laufen
lässt, kannst du auf `--host 127.0.0.1` umstellen — siehe Kommentare in
`systemd/scrapper-web.service`.

### 3. Cloudflare-Tunnel + Access (empfohlen)

#### Variante A — cloudflared im selben Container

```bash
# Im scrapper-Container:
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
dpkg -i cloudflared.deb
cloudflared tunnel login
cloudflared tunnel create scrapper
cloudflared tunnel route dns scrapper scrapper.deine-domain.tld
```

`~/.cloudflared/config.yml`:
```yaml
tunnel: <tunnel-uuid>
credentials-file: /root/.cloudflared/<uuid>.json
ingress:
  - hostname: scrapper.deine-domain.tld
    service: http://localhost:8000
  - service: http_status:404
```

```bash
cloudflared service install
```

Wenn du diese Variante nutzt, kannst du in `systemd/scrapper-web.service`
auf `--host 127.0.0.1` umstellen — dann ist Port 8000 nur lokal sichtbar.

#### Variante B — cloudflared in eigenem Container (häufiger bei Proxmox)

Du hast bereits einen LXC mit cloudflared (z.B. „Tunnel-Hub" für mehrere
Services). Im **Cloudflare Zero Trust Dashboard → Tunnels** trägst du dort
die neue Route ein:

```yaml
# in der Tunnel-Config des cloudflared-Hosts:
ingress:
  # ... bestehende Einträge ...
  - hostname: scrapper.deine-domain.tld
    service: http://<scrapper-container-ip>:8000
  - service: http_status:404
```

Der `bind_host` in unserer `scrapper-web.service` muss in diesem Fall
`0.0.0.0` bleiben (Default), damit der cloudflared-Container über LAN
zugreifen kann. **Wichtig**: setze eine LAN-Firewall (z.B. UFW im
scrapper-Container) die Port 8000 nur für die cloudflared-Container-IP
freigibt:

```bash
apt install -y ufw
ufw allow from 192.168.1.<cloudflared-ip> to any port 8000 proto tcp
ufw default deny incoming
ufw default allow outgoing
ufw enable
```

#### Cloudflare Access (für beide Varianten)

Im **Cloudflare Zero Trust Dashboard → Access → Applications**:
1. **Add an application → Self-hosted**
2. Application Domain: `scrapper.deine-domain.tld`
3. **Policy** anlegen: Action=Allow, Include=Email(s), optional Require=TOTP
4. (Optional) Country-Restriction auf dein Land

Damit hast du MFA vor der App, **ohne** die App selbst anzupassen.

### 4. Konfiguration

Im Web-UI → „Einstellungen":
- **E-Mail-Konten** (IMAP-App-Passwords für Gmail)
- **Ollama-URL** und Modell-Namen (Default: `qwen2.5:7b-instruct`, optional `fallback_model`)
- **Schedule** (systemd-OnCalendar-Expression für den Importdienst)

---

## CLI

```bash
# Passwort zurücksetzen (Reset wenn ausgesperrt)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli set-password

# Session-Secret rotieren (invalidiert alle aktiven Sessions)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli rotate-secret

# SQLite-Online-Backup mit gzip + integrity-check + multi-tier retention
# (läuft automatisch via systemd-Timer täglich um 04:00)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli db-backup [pfad]

# Restore aus einem Backup. Service vorher stoppen!
sudo systemctl stop scrapper-web
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli db-restore /opt/scrapper/data/backups/daily/scrapper-2026-05-22.db.gz
sudo systemctl start scrapper-web

# Alle Backups auflisten gegliedert nach Tier
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli list-backups

# SQLite-Speicher reclaimen (läuft automatisch sonntags)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli db-vacuum

# Logs aufräumen (älter als paths.log_retention_days)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli log-cleanup [days]

# Bereits vorhandene Rezept-PDFs einmalig automatisch ausrichten
# Erweiterte Stapelverarbeitung (OCR, Beschnitt, Leerseiten) erfolgt im Admin-Reiter
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-auto-rotate
# Optional anderes Wurzelverzeichnis:
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-auto-rotate /pfad/zu/pdfs
```

```bash
# Service-Befehle
systemctl status scrapper-web
systemctl restart scrapper-web      # mit Type=notify wartet auf 'ready'-Signal
journalctl -u scrapper-web -f

# Manuell ausführen (respektiert File-Lock)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.jobs.scraper_cli

# Daily DB-Backup-Timer aktivieren (läuft 04:00, macht auch log-cleanup + sonntags vacuum)
systemctl enable --now scrapper-db-backup.timer
systemctl list-timers scrapper-db-backup
```

## Disaster Recovery

Wenn Container/VM/Disk weg ist - so kommst du zurück. Voraussetzung ist
ein Backup unter `/opt/scrapper/data/backups/` (existiert wenn der DB-
Backup-Timer mindestens einmal lief).

### 1. Backups regelmäßig off-site sichern

Die täglichen Backups landen in `data/backups/daily/scrapper-YYYY-MM-DD.db.gz`.
Sichere die idealerweise **außerhalb** des Containers. Optionen:

```bash
# Variante B: cron-Job der das täglich nach 04:30 macht
cat > /etc/cron.d/scrapper-offsite-backup <<'EOF'
30 4 * * * scrapper rsync -a /opt/scrapper/data/backups/ /mnt/offsite/rezeptliebe-backups/
EOF

# Variante C: Proxmox-Backup vom kompletten Container (vzdump)
# Auf dem Proxmox-Host: einmal pro Tag automatisch
```

Plus die `config.yaml` separat sichern (enthält Mail-Passwörter, Webhook-URLs).

### 2. Restore-Playbook

Wenn der Container weg ist und du in einer neuen Umgebung neu aufbauen musst:

```bash
# Schritt 1: Neuen LXC anlegen + Repo klonen + install.sh
pct create <neuer-ctid> ... (siehe Setup-Block oben)
pct enter <neuer-ctid>
cd /opt
git clone https://github.com/appear7240/Scrappercontainer.git scrapper
cd scrapper
bash proxmox/install.sh

# Schritt 2: Backup zurückspielen
sudo systemctl stop scrapper-web
# - DB-Backup aus deiner Off-Site-Sicherung nach data/backups/daily kopieren
# - Restore
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli db-restore \
    /opt/scrapper/data/backups/daily/scrapper-2026-05-22.db.gz

# Schritt 3: Config zurückspielen
# Sichere config.yaml aus dem letzten Off-Site-Backup übertragen
sudo cp /tmp/backup-config.yaml /opt/scrapper/data/config.yaml
sudo chown scrapper:scrapper /opt/scrapper/data/config.yaml
sudo chmod 600 /opt/scrapper/data/config.yaml


# Schritt 4: Im Web-UI einloggen und Mail-/KI-Verbindungen testen
```

### 3. Was nicht im Backup ist

- **yt-dlp Cookies-Datei** (falls konfiguriert)
- **Bereits einsortierte Videos** in den Recipe/Wedding-Folders (liegen in den konfigurierten Rezept-/Hochzeitsverzeichnissen und müssen separat gesichert werden)
- **systemd-Customizations** (falls du die Unit-Files manuell angepasst hast - normalerweise nicht nötig da `cp systemd/* /etc/systemd/system/` reicht)

### 4. Failed-Email-Recovery

Wenn yt-dlp eine URL nicht runterladen kann, wird der Versuch in der DB
getrackt. Nach `MAX_DOWNLOAD_ATTEMPTS=3` Fehlversuchen wird die URL beim
nächsten Mail-Sync übersprungen.

Im UI unter **Pending → Wiederholbare Fehler** siehst du diese URLs mit
Versuchszahl + letztem Fehler. Reset-Button setzt den Counter zurück -
beim nächsten Mail-Sync wird die URL nochmal versucht (sofern noch in
einer Email vorhanden). Häufige Ursachen für Failures:
- Video privat/gelöscht → Cookies-Datei kann helfen (siehe yt-dlp-Config)
- yt-dlp veraltet → `pip install -U yt-dlp` im scrapper-venv
- Cloudflare-Block → User-Agent ändern oder Cookies setzen

## Monitoring

Die App stellt mehrere Endpoints für externes Monitoring bereit:

```bash
# Healthcheck (HTTP 200 wenn ok, 503 wenn DB nicht erreichbar)
curl -s http://127.0.0.1:8000/healthz

# Tiefer Check (DB + KI + Disk) - immer 200, Details im Body
curl -s http://127.0.0.1:8000/healthz/deep | jq

# Prometheus-Metriken (für Grafana / Alertmanager)
curl -s http://127.0.0.1:8000/metrics
```

Verfügbare Metriken: `scrapper_pending_count`, `scrapper_pending_oldest_seconds`,
`scrapper_jobs_running{kind=...}`, `scrapper_jobs_24h_total{kind,status}`,
`scrapper_history_total`, `scrapper_download_failures_total`,
`scrapper_last_run_age_seconds`, `scrapper_last_run_duration_seconds`.

Prometheus-Scrape-Config:
```yaml
scrape_configs:
  - job_name: scrapper
    metrics_path: /metrics
    static_configs:
      - targets: ['scrapper.lan:8000']
```

Wenn dein cloudflared im selben Container läuft (`bind_host: 127.0.0.1`),
scrape Prometheus von einem anderen Container über Cloudflare Access oder
über das LAN-IP des Container-Bridges.

---

## Konfigurationsstruktur

`data/config.yaml` (wird beim Erststart aus `config/config.example.yaml` erzeugt):

```yaml
web:
  username: admin
  password: $2b$12$...   # bcrypt-Hash, von der App selbst geschrieben
  secret_key: <48 random chars>
  bind_host: 127.0.0.1
  bind_port: 8000

paths:
  recipe_dir: /pfad/zu/rezepten
  wedding_dir: /pfad/zu/hochzeit
  temp_dir: /opt/scrapper/temp
  logs_dir: /opt/scrapper/logs

mail:
  recipe:
    enabled: true
    imap_host: imap.gmail.com
    imap_port: 993
    username: …
    password: …      # App-Password, NICHT das Google-Konto-Passwort
    folder: INBOX
    max_mails: 20
  wedding:
    enabled: true
    # … wie recipe
    default_category: Sonstiges
    always_pending: false

ai:
  ollama:
    enabled: true
    url: http://localhost:11434
    model: qwen2.5:7b-instruct
    fallback_model: qwen2.5:14b-instruct  # optional, leer = kein Fallback
    timeout: 60
  confidence_threshold: 0.75
  fallback_threshold: 0.5
  description_min_length: 20

ytdlp:
  binary: /opt/scrapper/venv/bin/yt-dlp

pdf:
  auto_rotate: true
  remove_blank_pages: true
  auto_crop: true
  deskew_scans: false
  ocr_scans: true
  improve_contrast: false
  ocr_language: deu+eng
  keep_original: true
```

`paths.recipe_dir` und `paths.wedding_dir` müssen **lokal beschreibbar** sein (Scraper macht `shutil.copy2`). Cloud-/NAS-Ziele müssen vorab als lokales Dateisystem eingebunden sein.

---

## Was nicht (mehr) drin ist

- **Keine Telegram-Benachrichtigungen** — Status nur im Web-UI
- **Keine OpenAI Vision** — Klassifizierung nur per Ollama-Cascade. Wenn beide Modelle unter Confidence-Threshold liegen, landet das Item in Pending zur manuellen Auflösung
- **Keine Frame-Extraktion** — Pending-Items werden als `<video>` im Web-UI angezeigt, `<img>`-Thumbs sind raus
- **Keine NAS-Annahme** — Pfade sind generisch konfigurierbar und können auf lokale Mounts zeigen

---

## Lizenz / Verantwortung

Self-hosted Setup. Vor produktivem Einsatz: das Hardening-Checklist im `data/config.yaml` durchgehen, Initial-Passwort ändern, Cloudflare-Access (oder ein Äquivalent) davorstellen, regelmäßig `git pull` für Updates.

Bei Fragen / Issues / PRs → GitHub.

## Admin Center

Direktaufruf: `/admin`, PDF-Werkzeuge: `/admin/pdf`. Der Menüpunkt ist für jeden aktiven, angemeldeten Benutzer sichtbar. Es gibt keine Admin-Rollen mehr; alle Konten haben denselben Vollzugriff.

```bash
# Benutzer und Aktivstatus anzeigen
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli user-list
```

## PDF-Bestand optimieren

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-optimize
```

Der Lauf dreht falsch ausgerichtete Seiten, verbessert Scan-Lesbarkeit, erzeugt bei Bedarf OCR-Textlayer und sichert die Originale.

### PDF-Diagnose

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-doctor
```

PDF-Bestandsläufe aus dem Admin Center werden ab v1.2.3 im Hintergrund verarbeitet und zeigen ihren Fortschritt im Browser an.

### Lokales ZIP-Update ohne Versionsmischung

Ein entpacktes Release nicht über `proxmox/install.sh` aktualisieren, da dieses für
eine Git-Installation gedacht ist. Für ZIP-Releases verwenden:

```bash
cd /pfad/zum/entpackten/Release
sudo bash proxmox/update-local.sh
```

Das Skript überträgt Backend und Frontend gemeinsam, bewahrt alle Laufzeitdaten,
startet `scrapper-web` neu und verifiziert anschließend Version und PDF-Routen.
