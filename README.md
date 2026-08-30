# Rezepte

## Native iPhone-App

Der neue iPhone-Hauptpfad liegt in [`ios-swift/`](ios-swift/README.md) und ist
eine eigenständige SwiftUI-App. Ihre Quellenküche übernimmt Rezeptlinks aus
Webseiten, Pinterest, YouTube, TikTok und Instagram sowie Fotos und PDFs. Die
bisherige Expo-App bleibt unter [`native-ios/`](native-ios/README.md) als
Vergleichs- und Rückfallstand erhalten, ist aber nicht mehr der automatisch
geprüfte Hauptpfad.

Die SwiftUI-App enthält außerdem die vollständige manuelle Importprüfung mit
KI-Neuanalyse sowie einen servergespeicherten Kochmodus mit Portionsskalierung,
Schritt-Timern und idempotentem Eintrag in die Kochhistorie.

Der **Quellenwächter** macht aus dem Original-Link einen überprüfbaren
Rezeptpass: Textstände werden als Fingerprint gesichert, spätere Änderungen als
Diff angezeigt und niemals automatisch in das Rezept übernommen. Ein lokaler
**Rezept-TÜV** markiert zusätzlich fehlende Portionen, Zutaten, Schritte,
Mengenangaben und Dubletten. Wochenplanzutaten und wiederkehrender
Haushaltsbedarf laufen in derselben Einkaufsliste zusammen.

Proxmox-LXC-Container für den Scraper-Job:

**Rezeptbibliothek mit offenem Quellenimport** — übernimmt Links aus zwei
separaten E-Mail-Postfächern (Rezepte + Hochzeit). Plattformmedien werden nicht
heruntergeladen. Unvollständige Eingänge bleiben mit ihrem Original-Link zur
manuellen Bearbeitung erhalten.

Der Job wird über ein **Web-Interface** verwaltet (Konfiguration, manuelles Starten, Pending-Auflösung, Logs, Historie). Externe Erreichbarkeit ist explizit für **Cloudflare-Tunnel + Cloudflare Access** (MFA-Layer) ausgelegt.


## Oberfläche

- Quellen-Eingang ist die Startseite; das Archiv bleibt einen Tab entfernt
- Butter, Salbei, Tomate und Pflaume sind gerätebezogen umschaltbar
- Einkaufsliste mit lokalem Produktkatalog, Autovervollständigung, Icons und Supermarktbereichen
- Quellenwächter mit unveränderlicher Baseline, Quell-Diff und Rezept-TÜV
- wiederkehrender Haushaltsbedarf zusammen mit Rezept- und Wochenplanzutaten
- native iPhone-Navigation mit Dynamic Type, Dark Mode und iOS-Safe-Areas
- erweiterte Filter als Side-Sheet am Desktop und Bottom-Sheet auf Smartphones
- keine externen Schriftarten oder Design-CDNs
- Administration in den Einstellungen für Konten mit Vollzugriff
- automatische PDF-/Scan-Aufbereitung mit Ausrichtung, OCR, Randbeschnitt und Seiteneditor


---

## Architektur auf einen Blick

```
E-Mail-Inbox / App
        │
        ▼
  strikte Linkprüfung
        │
        ▼
SQLite Pending ──► manuelle Pflege ──► Rezept mit Original-Link

Optional und vollständig getrennt:
private SQLite-Queue ──► video_archiver ──► privates ID-basiertes Archiv
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
- Link-only-Import ohne Plattformmedien oder versteckte Videodateien
- IMAP-Retry mit Backoff (3 Versuche, 1s/4s)
- OpenAI-Health-Check beim Job-Start (bricht ab statt 50 sinnlose Pending-Items zu erzeugen)
- Thread-safe Cancel für laufende Import- und Analysejobs
- Async Telegram raus, alle Notifications nur noch in Web-UI


---

## Admin-Zentrale

Der Reiter **Admin** ist ausschließlich für aktive Konten mit der Rolle
`admin` sichtbar. Normale Benutzer können Rezepte, Einkauf und Wochenplanung
nutzen, aber keine Server-, Import- oder Benutzerverwaltung ausführen.

- **Importzentrale:** offene Prüfungen, verbliebene Altfehler, laufende Jobs und letzte Importe
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
cd /opt && git clone https://github.com/oliverzimmermann1986-debug/Rezepte.git scrapper
cd scrapper
bash proxmox/install.sh
```

Das Install-Script erzeugt automatisch:
- einen `scrapper`-User
- ein **zufälliges Initial-Passwort** (gespeichert in `data/.initial-password`)
- ein **zufälliges `secret_key`** (48 Zeichen)
- die systemd-Units für Web, Scraper, Backups und den eng begrenzten Schedule-Helper

```
🌐 Web-Interface (LOKAL):    http://127.0.0.1:8000
👤 Login:                    admin
🔑 Initial-Passwort:         (siehe Ausgabe oder data/.initial-password)
```

Der uvicorn-Bind ist standardmäßig **`127.0.0.1:8000`**. Abweichungen werden
nur root-verwaltet in `/etc/scrapper/web.env` konfiguriert. Damit macht eine
Neuinstallation den Port nicht unbeabsichtigt im LAN erreichbar.

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

Für diese Variante ist keine Bind-Override-Datei nötig.

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

Für diese Topologie muss root beide Vertrauensgrenzen explizit setzen:

```bash
sudo install -d -m 0755 /etc/scrapper
sudo tee /etc/scrapper/web.env >/dev/null <<'EOF'
SCRAPPER_BIND_HOST=0.0.0.0
SCRAPPER_FORWARDED_ALLOW_IPS=127.0.0.1
EOF
sudo chmod 0600 /etc/scrapper/web.env
sudo systemctl restart scrapper-web
```

Ergänze außerdem in `/opt/scrapper/data/config.yaml` den unmittelbaren
cloudflared-Peer. Nur die Anwendung wertet dessen Forwarded-Header aus:

```yaml
web:
  # Nur bei vorgeschaltetem Cloudflare Access aktivieren.
  auth_disabled: true
  trusted_proxies:
    - 127.0.0.1/32
    - "::1/128"
    - 192.168.1.<cloudflared-ip>/32
```

Danach `sudo systemctl restart scrapper-web` ausführen. Uvicorn darf die
unmittelbare Proxy-IP nicht vor dieser Prüfung durch `X-Forwarded-For`
ersetzen; deshalb bleibt `SCRAPPER_FORWARDED_ALLOW_IPS` auf Loopback.

**Wichtig**: Setze zusätzlich eine LAN-Firewall, die Port 8000 ausschließlich
für die cloudflared-Container-IP freigibt:

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
- **OpenAI API-Key** und Modell (Default: `gpt-4o-mini`; optionale Base-URL nur mit erneuter Key-Eingabe änderbar)
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
Sie enthalten **nur SQLite**, keine Rezeptordner, Bilder/PDFs, `config.yaml`
oder Dateien des Video-Archivers. Sichere Datenbank, Konfiguration und die in
`paths.recipe_dir`/`paths.wedding_dir` konfigurierten Medien daher gemeinsam
**außerhalb** des Containers. Optionen:

```bash
# Variante B: cron-Job der das täglich nach 04:30 macht
cat > /etc/cron.d/scrapper-offsite-backup <<'EOF'
30 4 * * * scrapper rsync -a /opt/scrapper/data/backups/ /mnt/offsite/rezepte-backups/
# Zusätzlich die tatsächlichen Rezept-/Hochzeitsordner und config.yaml sichern.
# Externe Mounts brauchen einen eigenen Snapshot/Backup-Job.
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
git clone https://github.com/oliverzimmermann1986-debug/Rezepte.git scrapper
cd scrapper
bash proxmox/install.sh

# Schritt 2: Config zuerst zurückspielen
sudo systemctl stop scrapper-web
sudo cp /tmp/backup-config.yaml /opt/scrapper/data/config.yaml
sudo chown scrapper:scrapper /opt/scrapper/data/config.yaml
sudo chmod 600 /opt/scrapper/data/config.yaml

# Schritt 3: Medienordner aus demselben Sicherungsstand zurückspielen, dann DB.
# db-restore rotiert danach bewusst secret_key und widerruft alte Sessions/Shares.
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli db-restore \
    /opt/scrapper/data/backups/daily/scrapper-2026-05-22.db.gz

# Schritt 4: Im Web-UI einloggen und Mail-/KI-Verbindungen testen
```

### 3. Was nicht im Backup ist

- **Rezept-, Bild-, PDF- und Hochzeitsdateien** in den konfigurierten Datei-/Mountpfaden
- **`config.yaml` und Secrets** (separat verschlüsselt sichern)
- **Cookies und Dateien des optionalen Video-Archivers** (liegen bewusst außerhalb der Anwendung)
- **systemd-Customizations** (falls du die Unit-Files manuell angepasst hast - normalerweise nicht nötig da `cp systemd/* /etc/systemd/system/` reicht)

### 4. Unvollständige Linkimporte

Ein gültiger TikTok-/Instagram-Beitragslink wird ohne Medienabruf gespeichert.
Fehlen Zutaten oder Schritte, bleibt er unter **Manuelle Prüfung** sichtbar und
kann dort ergänzt werden. Alte Downloadfehler werden beim erneuten Linkimport
in einen normalen offenen Eingang umgewandelt.

### 5. Optionales privates Videoarchiv

Der eigenständige Worker unter [`video_archiver/`](video_archiver/README.md)
verarbeitet eine separate SQLite-Queue und speichert berechtigte Inhalte als
`<Rezept-ID>.mp4` plus Prüfsummen-Sidecar. Er ist kein Bestandteil der App oder
Rezepte-API und sein Archiv darf nicht öffentlich bereitgestellt werden.

## Monitoring

Die App stellt einen öffentlichen Minimal-Healthcheck sowie geschützte
Diagnose- und Metrikendpunkte bereit:

```bash
# Healthcheck (HTTP 200 wenn ok, 503 wenn DB nicht erreichbar)
curl -s http://127.0.0.1:8000/healthz

# Tiefer Check (DB + KI + Disk) mit gültiger Login-Session
curl -s -b rezepte.cookies http://127.0.0.1:8000/healthz/deep | jq

# Prometheus-Metriken sind nur für Administrator-Sessions freigegeben
curl -s -b rezepte.cookies http://127.0.0.1:8000/metrics
```

Verfügbare Metriken: `scrapper_pending_count`, `scrapper_pending_oldest_seconds`,
`scrapper_jobs_running{kind=...}`, `scrapper_jobs_24h_total{kind,status}`,
`scrapper_history_total`, `scrapper_download_failures_total`,
`scrapper_last_run_age_seconds`, `scrapper_last_run_duration_seconds`.

Für automatisches Prometheus-Scraping muss der Betreiber die Authentisierung
am vorgeschalteten, privaten Monitoring-Proxy lösen. `/metrics` darf nicht
ungefiltert ins LAN oder Internet freigegeben werden.

---

## Konfigurationsstruktur

`data/config.yaml` (wird beim Erststart aus `config/config.example.yaml` erzeugt):

```yaml
web:
  username: admin
  password: $2b$12$...   # bcrypt-Hash, von der App selbst geschrieben
  secret_key: <48 random chars>
  trusted_proxies: [127.0.0.1/32, "::1/128"]

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
- **Kein lokaler Ollama-Pfad** — KI-Analyse einschließlich Vision nutzt OpenAI. Bei zu niedriger Confidence landet das Item in Pending zur manuellen Auflösung
- **Begrenzter Video-Fallback** — nur temporär für Transkript/Frame-OCR; Videos werden weder an die native App noch über öffentliche Medienrouten ausgeliefert
- **Keine NAS-Annahme** — Pfade sind generisch konfigurierbar und können auf lokale Mounts zeigen

---

## Lizenz / Verantwortung

Self-hosted Setup. Vor produktivem Einsatz: das Hardening-Checklist im `data/config.yaml` durchgehen, Initial-Passwort ändern, Cloudflare-Access (oder ein Äquivalent) davorstellen und Updates über `proxmox/install.sh` beziehungsweise `proxmox/update-local.sh` einspielen.

KI-Inhalte werden an OpenAI übermittelt. Der Betreiber muss vor produktiver
Nutzung die passende Rechtsgrundlage sowie die erforderlichen Datenschutz-
Nachweise (insbesondere AVV/DPA, SCC und gegebenenfalls eine DSFA) selbst prüfen
und dokumentieren; die Software kann diese externe Prüfung nicht ersetzen.

Bei Fragen / Issues / PRs → GitHub.

## Admin Center

Direktaufruf: `/admin`, PDF-Werkzeuge: `/admin/pdf`. Beide Bereiche erfordern
ein aktives Administratorkonto; normale Konten erhalten keinen Zugriff auf
mutierende Verwaltungs-, Import- und Wartungsfunktionen.

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

### PDF-Rezeptdaten

PDF-Rezepte werden nach OCR/Ausrichtung direkt auf Zutaten, Mengen, Einheiten, Schritte und Portionen ausgewertet. Für Bestandsdateien steht die Funktion unter **Admin → PDF & Scan** zur Verfügung. Details: `PDF_RECIPE_EXTRACTION.md`.
