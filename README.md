# Scrappercontainer

Proxmox-LXC-Container, der zwei Jobs in einem zusammenfasst:

1. **TikTok/Instagram Scraper** – holt Links aus zwei separaten E-Mail-Inboxen (Rezepte + Hochzeit), lädt die Videos mit `yt-dlp`, lässt sie von Ollama analysieren (mit OpenAI-Vision-Fallback) und sortiert sie in die richtigen Ordner.
2. **rclone bisync Backup** – synchronisiert pCloud ↔ NAS für alle konfigurierten Paare parallel.

Beide Jobs werden über ein **Web-Interface** verwaltet (Konfiguration, manuelles Starten, Pending-Auflösung, Logs, Historie).

> Ersetzt die Vorgänger-Scripts `rclone-sync.sh` und `tiktokscript.py` (~1500 Zeilen Python). Telegram-Reply-Mechanik fällt komplett weg – Pending-Items werden direkt im Browser aufgelöst.

---

## Was ist neu vs. den alten Scripts?

| Alt | Neu |
|---|---|
| Subject-Keyword-Klassifizierung (`#rezept` / `#hochzeit`) | **2 separate IMAP-Konten** – Inbox bestimmt den Typ |
| Pending wird per Telegram-Reply aufgelöst | **Web-Interface** mit Vorschaubild, Dropdown, Beschreibung |
| Konfiguration über `credentials.json` + ENV-Variablen | **Web-UI + `config.yaml`** |
| `nohup` + manuelle Cron-Setups | **systemd Services + Timer** |
| Status-Files in JSON | **SQLite-Datenbank** |
| Telegram empfängt Updates und Replies | **Nur Notifications** (Send-only) |

---

## Architektur

```
+---------+    +------------------+    +---------------+
|   Web   |◄──►|    FastAPI       |◄──►|   SQLite      |
+---------+    | (Port 8000)      |    +---------------+
               +─────┬─────┬──────+
                     │     │
              ┌──────┘     └──────┐
              ▼                   ▼
       ┌───────────┐        ┌─────────────┐
       │ Scraper   │        │ rclone      │
       │ (timer:   │        │ (timer:     │
       │  30 min)  │        │  täglich)   │
       └─────┬─────┘        └──────┬──────┘
             │                     │
       ┌─────┴──────┐         ┌───┴────────┐
       │ Ollama     │         │  pCloud    │
       │ OpenAI     │         │  ↕         │
       │ yt-dlp     │         │  /mnt/nas  │
       └────────────┘         └────────────┘
```

---

## Schnellstart (Proxmox)

### 1. Auf dem Proxmox-Host

```bash
# Scripts holen
curl -O https://raw.githubusercontent.com/appear7240/Scrappercontainer/main/proxmox/create-container.sh
curl -O https://raw.githubusercontent.com/appear7240/Scrappercontainer/main/proxmox/install.sh
chmod +x create-container.sh install.sh

# Container anlegen (Defaults anpassen über Env-Variablen)
CTID=200 HOSTNAME=scrapper PASSWORD=changeme \
NAS_MOUNT_HOST=/mnt/media-nas \
./create-container.sh

# Installations-Script in den Container kopieren und ausführen
pct push 200 install.sh /root/install.sh
pct exec 200 -- bash /root/install.sh
```

Die Default-Werte im `create-container.sh` (anpassbar über Env-Variablen):

| Variable | Default | Bedeutung |
|---|---|---|
| `CTID` | `200` | Container-ID |
| `HOSTNAME` | `scrapper` | Hostname |
| `STORAGE` | `local-lvm` | Storage für rootfs |
| `DISK_SIZE` | `16` | GB |
| `MEMORY` | `2048` | MB |
| `CORES` | `2` | CPU-Kerne |
| `IP_ADDR` | `dhcp` | `192.168.x.x/24,gw=…` für statisch |
| `NAS_MOUNT_HOST` | `/mnt/media-nas` | NAS-Verzeichnis am Host |
| `MEDIA_MOUNT_HOST` | leer | optional: weiterer Mount |

### 2. Web-UI öffnen

```
http://<container-ip>:8000
```

Login: `admin` / `changeme`  → **sofort in den Einstellungen ändern!**

### 3. Einstellungen ausfüllen

Im Tab **Einstellungen** alles eintragen:

- **Web**: neues Passwort + zufälliger Session Secret (32+ Zeichen)
- **E-Mail Rezepte**: IMAP-Daten der Rezept-Inbox
- **E-Mail Hochzeit**: IMAP-Daten der Hochzeit-Inbox (+ Default-Kategorie)
- **Pfade**: Zielordner für Rezepte / Hochzeit
- **Ollama**: URL (z. B. `http://192.168.x.x:11434`), Modell (z. B. `gemma3:12b`)
- **OpenAI** (optional): API-Key für Vision-Fallback
- **Telegram**: Bot-Token + Chat-ID (separat für Recipe/Wedding/Backup, oder fallback auf Recipe-Bot)
- **Backup**: Sync-Paare anlegen (remote ↔ lokal)

Speichern → `data/config.yaml` wird auf der Platte aktualisiert.

### 4. rclone konfigurieren

```bash
pct exec 200 -- sudo -u scrapper rclone config
```

Remote anlegen (z. B. `pcloud`), Auth abschließen. Der Remote-Name muss zu `backup.rclone_remote` in der Config passen.

### 5. Erster Test

- Im Web-UI auf **Dashboard** → **Scraper starten** klicken
- Status oben in der Sidebar zeigt „läuft"
- Nach dem Lauf: Tab **Jobs & Logs** für Detail-Output
- Falls KI unsicher: Tab **Pending** – Vorschau, Name/Kategorie wählen, Speichern

---

## Verzeichnis-Layout im Container

```
/opt/scrapper/
├── app/                 # FastAPI + Job-Code
├── config/              # config.example.yaml
├── data/
│   ├── config.yaml      # ← deine Konfiguration
│   └── scrapper.db      # SQLite (Pending, History, Jobs)
├── logs/                # Job-Logs (web.log, scraper-*.log, backup-*.log)
├── temp/                # yt-dlp Downloads (auto-cleanup)
│   └── pending/         # Pending-Files (überleben Cleanup)
├── systemd/             # Unit-Files (in /etc/systemd/system kopiert)
└── venv/                # Python venv

/mnt/rezepte/<Typ>/<Kategorie>/<Name>/
  ├── <Name>.mp4
  ├── <Name>.jpg
  ├── description.txt
  └── info.json

/mnt/hochzeit/<Kategorie>/<Name>/
  └── (gleich)

/mnt/media-nas/{Serien,Filme,Fotos,Iphone_backup}/   # NAS-Bind-Mount
```

---

## systemd-Services

| Service | Funktion |
|---|---|
| `scrapper-web.service` | Web-Interface (uvicorn :8000, immer) |
| `scrapper-job.service` | Scraper-Lauf (oneshot) |
| `scrapper-job.timer` | alle 30 Min. |
| `rclone-sync.service` | Backup-Lauf (oneshot) |
| `rclone-sync.timer` | täglich 03:00 |

Nützliche Befehle (im Container):

```bash
systemctl status scrapper-web              # Web-Server-Status
systemctl restart scrapper-web             # nach Code-Update
journalctl -u scrapper-web -f              # Live-Log

systemctl list-timers                      # alle Timer + nächster Lauf
systemctl start scrapper-job.service       # manuell triggern
systemctl start rclone-sync.service        # manuell triggern

# Logs in /opt/scrapper/logs/
tail -f /opt/scrapper/logs/web.log
ls -lt /opt/scrapper/logs/scraper-*.log | head
```

---

## Update durchführen

Im Container:

```bash
cd /opt/scrapper
git pull
sudo -u scrapper venv/bin/pip install -r requirements.txt
systemctl restart scrapper-web
```

---

## Pending-Workflow

Wenn die KI sich nicht sicher ist (confidence < 0.75, frei in der Config einstellbar), landet das Video in **Pending**:

1. **Telegram** verschickt einen Hinweis mit Link und KI-Vorschlag (keine Reply nötig).
2. Im Web-UI unter **Pending** siehst du:
   - Standbild aus dem Video
   - KI-Vorschlag (Name, Typ/Kategorie, Confidence)
   - Original-Beschreibung (aufklappbar)
   - Eingabe-Felder mit KI-Werten vorausgefüllt
3. **Speichern** → Datei landet in `/mnt/rezepte/...` bzw. `/mnt/hochzeit/...`
4. **Skip** → URL wird in der Historie als übersprungen markiert
5. Während eine URL pending ist, wird sie bei späteren Mail-Läufen ignoriert

---

## API (für Skripte)

Alle Routen erfordern den Session-Cookie (Login `/login` POST).

| Methode | Route | Zweck |
|---|---|---|
| GET | `/api/config` | Konfiguration (Passwörter maskiert) |
| PUT | `/api/config` | Konfiguration setzen |
| POST | `/api/config/reload` | von Disk neu laden |
| GET | `/api/pending` | Pending-Items |
| GET | `/api/pending/preview?url=…` | Frame-Bild |
| GET | `/api/pending/video?url=…` | Video-Stream |
| POST | `/api/pending` | Auflösen `{url, action:save\|skip, name, type, category}` |
| POST | `/api/jobs/scraper/run` | Scraper triggern |
| POST | `/api/jobs/backup/run?dry_run=true` | Backup triggern |
| GET | `/api/jobs/list?kind=scraper&limit=50` | Job-Historie |
| GET | `/api/jobs/{id}/log` | Log-File |
| GET | `/api/jobs/status/current` | was läuft gerade |
| GET | `/api/history?limit=200` | verarbeitete URLs |

Swagger-UI: `http://<ip>:8000/api/docs`

---

## Troubleshooting

**Web-UI nicht erreichbar**
```bash
pct exec 200 -- systemctl status scrapper-web
pct exec 200 -- journalctl -u scrapper-web -n 50
```

**Scraper sieht keine Mails**
- IMAP-Daten in `data/config.yaml` korrekt? (Gmail braucht **App-Passwort**, kein Account-PW)
- Im Web-UI sind beide Mail-Konten als „Aktiv" markiert?
- Test im Container: `pct exec 200 -- sudo -u scrapper /opt/scrapper/venv/bin/python -m app.jobs.scraper_cli`

**Ollama nicht erreichbar**
- URL korrekt? Default-Port ist 11434
- Erreichbar aus dem Container? `pct exec 200 -- curl -s http://192.168.x.x:11434/api/tags`
- Falsches Modell? Im UI eintragen wie es Ollama listet (z. B. `gemma3:12b`)

**rclone-Backup schlägt fehl**
- `pct exec 200 -- sudo -u scrapper rclone listremotes` – Remote vorhanden?
- bisync-Lock-Cleanup macht der Job automatisch
- Bei "Must run --resync" macht das Script das automatisch beim nächsten Lauf

**Pending-Vorschaubild fehlt**
- Frame wird on-the-fly aus dem Video gerendert. Falls nicht: video_path/frame_path in der DB prüfen.
- DB inspizieren: `pct exec 200 -- sqlite3 /opt/scrapper/data/scrapper.db "SELECT url, video_path, frame_path FROM pending;"`

**Passwort vergessen**
```bash
pct exec 200 -- nano /opt/scrapper/data/config.yaml
# unter web: password ändern, speichern
pct exec 200 -- systemctl restart scrapper-web
```

---

## Sicherheitshinweise

- Das Web-UI hat eine simple Username/Passwort-Authentifizierung, gedacht für **interne Netze**.
- Vor Exposition ins Internet: nginx-Reverse-Proxy mit HTTPS + zusätzlicher Auth davor!
- `secret_key` in der Config muss zufällig sein (`openssl rand -hex 32`).
- `data/config.yaml` enthält Klartext-Passwörter (`chmod 600`). Im Web-UI maskiert.

---

## Lizenz

Privater Eigenbedarf. Anpassen wie du magst.
