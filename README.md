# Scrappercontainer

Proxmox-LXC-Container mit zwei Jobs in einem:

1. **TikTok/Instagram Scraper** — zieht Links aus zwei separaten E-Mail-Postfächern (Rezepte + Hochzeit), lädt die Videos mit `yt-dlp`, lässt sie von einer **lokalen Ollama-Instanz** klassifizieren und sortiert sie in passende Ordner.
2. **rclone-Sync (Cloud↔Cloud oder Cloud↔Lokal)** — synchronisiert beliebige rclone-Pairs parallel, mit Cancel-Funktion und Live-Progress.

Beide Jobs werden über ein **Web-Interface** verwaltet (Konfiguration, manuelles Starten, Pending-Auflösung, Logs, Historie). Externe Erreichbarkeit ist explizit für **Cloudflare-Tunnel + Cloudflare Access** (MFA-Layer) ausgelegt.

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
- SQLite mit WAL-Mode + `synchronous=NORMAL` + 10s busy_timeout
- Indizes auf häufige Queries
- `pending_add` ist idempotent (Status/Timestamp bleiben bei Re-Insert erhalten)
- Path-Whitelists auf alle FileResponse-Endpoints (defense in depth)
- Stale-Job-Recovery beim Start (alte `running`-Jobs werden auf `error` gesetzt)

**Robustheit**
- File-Lock (`fcntl.flock`) zwischen Web-Trigger und systemd-CLI
- Log-Rotation aller Job-Logs (älter als 30 Tage werden bei jedem Job-Start aufgeräumt)
- yt-dlp Failed-Tracking: nach 3 fehlgeschlagenen Versuchen wird die URL als „aufgegeben" gespeichert und nicht mehr probiert
- IMAP-Retry mit Backoff (3 Versuche, 1s/4s)
- Ollama-Health-Check beim Job-Start (bricht ab statt 50 sinnlose Pending-Items zu erzeugen)
- Thread-safe Cancel für **Scraper** und **Backup**
- Async Telegram raus, alle Notifications nur noch in Web-UI

**Backup**
- Pairs können `cloud:path ↔ cloud:path`, `cloud:path ↔ local:/path`, oder beide remote sein — wird automatisch detected
- ThreadPool mit `max_parallel` Cap (Default 3) verhindert API-Drosselung bei vielen Pairs
- Cancel-Mechanismus killt laufende rclone-Subprozesse sauber

---

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
- die systemd-Units (`scrapper-web`, `scrapper-job.timer`, `rclone-sync.timer`)

```
🌐 Web-Interface (LOKAL):    http://127.0.0.1:8000
👤 Login:                    admin
🔑 Initial-Passwort:         (siehe Ausgabe oder data/.initial-password)
```

Der uvicorn-Bind ist **`127.0.0.1`** — Web-UI ist im LAN nicht direkt erreichbar. Extern-Zugriff erfolgt ausschließlich über Reverse-Proxy oder Cloudflare-Tunnel.

### 3. Cloudflare-Tunnel + Access (empfohlen)

```bash
# Im Container:
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

Im **Cloudflare-Dashboard → Zero Trust → Access**:
1. Identity Provider hinzufügen (Google / GitHub / Email-OTP)
2. Application „self-hosted" anlegen, Domain = `scrapper.deine-domain.tld`
3. Policy: Allow → Include = deine Email, Require = TOTP / Hardware-Key (optional)
4. Geo-Restriction auf dein Land (optional, schließt 99 % der Welt aus)

Damit hast du MFA vor der App, **ohne** die App selbst anzupassen.

### 4. Konfiguration

Im Web-UI → „Einstellungen":
- **E-Mail-Konten** (IMAP-App-Passwords für Gmail)
- **Ollama-URL** und Modell-Namen (Default: `qwen2.5:7b-instruct`, optional `fallback_model`)
- **Backup-Pairs** (rclone-Remotes mit `pcloud:foo` ↔ `gdrive:bar` oder `pcloud:foo` ↔ `/mnt/local/bar`)
- **Schedule** (systemd-OnCalendar-Expression für beide Jobs)

rclone muss vorab als `scrapper`-User konfiguriert sein:
```bash
sudo -u scrapper rclone config
```

---

## CLI

```bash
# Passwort zurücksetzen (Reset wenn ausgesperrt)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli set-password

# Session-Secret rotieren (invalidiert alle aktiven Sessions)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli rotate-secret
```

```bash
# Service-Befehle
systemctl status scrapper-web
systemctl restart scrapper-web
journalctl -u scrapper-web -f

# Manuell ausführen (respektiert File-Lock)
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.jobs.scraper_cli
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.jobs.backup_cli --dry-run
```

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

backup:
  max_parallel: 3       # parallel rclone-Prozesse
  rclone_args: "--bwlimit 8M --transfers 4"
  pairs:
    - name: rezepte-pcloud-gdrive
      remote: pcloud:/Rezepte
      local: gdrive:/Backup/Rezepte
      direction: bisync   # oder copy/sync
    - name: hochzeit-pcloud
      remote: pcloud:/Hochzeit
      local: /mnt/local/hochzeit
      direction: copy
```

`paths.recipe_dir` und `paths.wedding_dir` müssen **lokal beschreibbar** sein (Scraper macht `shutil.copy2`). Wenn du direkt nach Cloud willst, mount sie vorher per `rclone mount`.

---

## Was nicht (mehr) drin ist

- **Keine Telegram-Benachrichtigungen** — Status nur im Web-UI
- **Keine OpenAI Vision** — Klassifizierung nur per Ollama-Cascade. Wenn beide Modelle unter Confidence-Threshold liegen, landet das Item in Pending zur manuellen Auflösung
- **Keine Frame-Extraktion** — Pending-Items werden als `<video>` im Web-UI angezeigt, `<img>`-Thumbs sind raus
- **Keine NAS-Annahme** — paths sind generisch konfigurierbar, Backup-Pairs können Cloud↔Cloud sein

---

## Lizenz / Verantwortung

Self-hosted Setup. Vor produktivem Einsatz: das Hardening-Checklist im `data/config.yaml` durchgehen, Initial-Passwort ändern, Cloudflare-Access (oder ein Äquivalent) davorstellen, regelmäßig `git pull` für Updates.

Bei Fragen / Issues / PRs → GitHub.
