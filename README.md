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

Self-hosted Rezeptbibliothek im Proxmox-LXC-Container:

**Rezeptbibliothek mit offenem Quellenimport** — übernimmt ausdrücklich gestartete
URL-, Text-, Foto-, PDF- und Videoimporte. Unvollständige Eingänge bleiben mit
ihrer Quelle zur manuellen Bearbeitung erhalten. E-Mail-Abruf und Mailzeitpläne
sind seit 1.10.0 entfernt.

Die Bibliothek wird über ein **Web-Interface** verwaltet (Konfiguration, direkte Importe, Pending-Auflösung, Logs, Historie). Für die externe Erreichbarkeit kann ein **Cloudflare-Tunnel** verwendet werden. Web- und iOS-App melden sich direkt mit einem eigenen Rezeptkonto an.


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
URL / Text / Foto / PDF / Video aus der App
        │
        ▼
  strikte Linkprüfung
        │
        ▼
SQLite Pending ──► manuelle Pflege ──► Rezept mit Original-Link

Optional und vollständig getrennt:
private SQLite-Queue ──► video_archiver ──► privates ID-basiertes Archiv
```

Importe werden ausdrücklich in der App gestartet. Die Hintergrundwarteschlange und Dateisperren schützen vor konkurrierender Verarbeitung; es gibt keinen periodischen Mailabruf.

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
- Dateisperren (`fcntl.flock`) für Import-, Analyse- und Verwaltungsaufgaben
- Aufbewahrungsgrenze für Job-Logs über `app.cli log-cleanup`
- Link-only-Import ohne Plattformmedien oder versteckte Videodateien
- Thread-safe Cancel für laufende Import- und Analysejobs
- Async Telegram raus, alle Notifications nur noch in Web-UI


---

## Gastzugang und Konten

- **Als Gast ansehen:** auf der Anmeldung in Web/PWA und iOS. Gäste können die
  öffentliche Rezeptbibliothek lesen. Das Backend sperrt Schreibzugriffe auf
  Rezept- und Haushaltsdaten; Verwaltungsdaten bleiben Administratoren vorbehalten.
  Die signierte Gastsitzung gilt 24 Stunden und legt keinen Benutzer an.
- **Konto erstellen:** eigene Anmeldung mit Benutzername und Passwort ab zehn
  Zeichen (höchstens 72 UTF-8-Bytes). Registrierte Personen erhalten die Rolle
  `user`; eine Registrierung kann keinen Administrator anlegen. Der Betreiber
  muss zuerst als aktiver Administrator eingerichtet sein.
- **Vier Rollen:** Gast, Benutzer, Vollbenutzer und Admin. Vollbenutzer haben
  zusätzlich zu den Benutzerrechten private Link-, Foto- und PDF-Importe für ihren
  Haushalt. Globale Importe und Administration bleiben Admins vorbehalten.
  Angemeldete Gastkonten können weiterhin ihre eigene Kontosicherheit verwalten.
  [Alle Rollen und Anmeldewege](docs/account-management.md).
- **Zweite Person einladen:** im Reiter **Konto**. Der Link gilt sieben Tage,
  funktioniert einmal und ist widerrufbar. Ein neuer Link widerruft die vorige
  offene Einladung. Beide Personen haben eigene Passwörter; Einladungen übertragen
  keine Adminrechte. Bestehende Benutzer können den Link unter **Konto** annehmen.
  Es sind höchstens zwei Personen pro Konto möglich.

Die Installation unterstützt **mehrere getrennte Haushalte**. Globale Rezepte sind
für alle angemeldeten Personen und Gäste lesbar. Private Rezepte, Favoriten,
Bewertungen, Kochverlauf, Einkaufsliste, wiederkehrende Einkäufe und Wochenplan
gehören zu genau einem Haushalt. Zwei eingeladene Personen teilen diesen Haushalt.
Globale Inhalte können nur Administratoren bearbeiten; private Rezepte können
beide Haushaltsmitglieder pflegen. Ein privater Import eines bereits global
vorhandenen Links speichert einen Verweis auf das vorhandene Rezept, ohne einen
weiteren Download oder Analyseauftrag. Web und iOS bieten die Ansichten **Global
für alle** und **Mein Haushalt**. Private Importe können unter **Konto** geprüft
und übernommen werden. Globale Link-Importe sind Administratoren vorbehalten.

Freigaben gehören zum Haushalt und bleiben für die zweite Person verwaltbar,
wenn die Anmeldung des Erstellers gelöscht wird. Ein wiedervergebener
Benutzername erhält diese Rechte nicht. Während einer Änderung oder synchronen
Analyse wird ein gleichzeitiger Haushaltsbeitritt mit 409 abgewiesen; nach
Abschluss kann die Einladung erneut angenommen werden. Das letzte Mitglied
eines Haushalts mit Daten oder laufenden Importen kann nicht gelöscht werden;
zuerst muss eine zweite Person die Daten übernehmen.

Neue Link-, Datei- und Fotoanalysen, erneute Analysen sowie Share-Intakes haben
zusammen mit einzeln gestarteten Rezeptbildern ein persistentes Kontingent
über gleitende 24 Stunden: standardmäßig 20 je
Haushalt und 200 insgesamt. `web.import_daily_limit` und
`web.import_server_daily_limit` passen es an; 0 sperrt neue Analysen und Bilder.
Fehlgeschlagene Versuche zählen mit. Verknüpfungen vorhandener globaler Rezepte,
Upload-Replays und Wiederholungen eines aktiven URL-Auftrags verbrauchen kein
zusätzliches Kontingent; das gilt auch für aktive Bildaufträge. Endet ein Auftrag
während der Wiederholung, benötigt ein neuer Auftrag ein eigenes Kontingent.
429 enthält eine Wartezeit in `Retry-After`.
Eine erneute Linkübernahme erhält bereits ermittelte Vorschläge und Medien;
ein Platzhalter überschreibt diese Daten nicht.
Gastanmeldungen sind auf 30 je IP innerhalb von fünf Minuten begrenzt.
Gesonderte Admin-Batchjobs verwenden diese Nutzerkontingente nicht; sie bleiben
durch ihre jeweiligen Zugriffsrechte und die serverweiten KI-Kontingente geschützt.

Die Migration auf Schema **261** behält bestehende Rezepte als globale Sammlung
und ordnet vorhandene Einkaufs- und Plandaten einmalig dem Betreiberhaushalt zu.
Beim Beitritt eines bestehenden Kontos werden dessen private Inhalte, Sammlung,
Einkäufe und Planungen zusammengeführt. Laufende Importe müssen zuerst enden.
Die externe Einkauf-API verwendet gemeinsame Zugangsdaten und wird für getrennte
Haushalte deshalb durch die eigene lokale Einkaufsliste ersetzt; ihre bestehende
Konfiguration bleibt für den alten Einbenutzermodus erhalten.
Einladungen speichern nur den Hash des Tokens; der kopierbare Link wird
ausschließlich beim Erstellen angezeigt.

Die Kontenanmeldung gilt auch hinter einem vertrauenswürdigen Proxy. Der
frühere Schalter `web.auth_disabled` wird ignoriert und beim Speichern entfernt;
er kann keine Anmeldung oder Rollenprüfung mehr umgehen. Gastzugang und
Registrierung verwenden die eigenen, serverseitig begrenzten Endpunkte.

Die Konto- und Haushaltstabellen wurden mit Schema **261** angelegt. Schema
**262** ergänzt stabile Freigabeeigentümer und die Importkontingente. Schema
**263** bindet persönlichen Kochfortschritt und Abschlusswiederholungen an feste
Benutzer-IDs. Beim Löschen einer Anmeldung wird deren persönlicher Zwischenstand
entfernt; die Kochhistorie des Haushalts bleibt erhalten. Historische Zwischenstände
werden nur zugeordnet, wenn das aktuelle Konto damals bereits bestand. Unklare
Altdaten bleiben gespeichert und werden neu registrierten Konten nicht gezeigt.
Bestehende Benutzer und
Rezeptdaten bleiben erhalten. Web-Registrierung und Anmeldung verwenden
Same-Origin-Prüfungen, native Clients weiterhin Bearer-Sitzungen.

Bildveröffentlichung und Rollback verwenden eine gemeinsame Dateisperre für
Web-/Worker-Prozesse. Versteckte Entwürfe und Rückfallkopien werden weder als
Titelbild noch als Originalsicherung ausgewählt. Bilduploads verarbeiten
Dekodierung, Sicherung und Veröffentlichung außerhalb der HTTP-Ereignisschleife.

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

Versionen erfassen strukturierte Rezeptdaten. Vor einem Coverwechsel wird das
bisherige Bild separat im Rezeptordner unter `.versions/` gesichert und mit
dem Snapshot verknüpft. Videos und PDF-Originale werden nicht in der
Datenbankversion dupliziert; PDF-Änderungen sichern ihr Original separat im
Datenverzeichnis.

Papierkorbsicherungen werden dem ursprünglichen Rezept zugeordnet, auch wenn
derselbe Ordnerpfad inzwischen erneut verwendet wird. Löschläufe prüfen den
aktuellen Löschzeitpunkt unter der Rezeptsperre; zwischenzeitlich gerettete
Rezepte bleiben erhalten. Eine Versionswiederherstellung ersetzt die
Schrittliste und entfernt dabei den dazu nicht mehr passenden Kochfortschritt.
Gesicherte Cover und Versionsdaten werden gemeinsam übernommen. Scheitert
ein Datei- oder Datenbankschritt, werden Cover und Metadaten zurückgerollt.
Eine erfolgreiche Cover-Wiederherstellung hebt ältere Bildaufträge auf.
Versionen von Rezepten im Papierkorb können erst nach deren Rettung
wiederhergestellt werden.

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
- die systemd-Units für Web und Datenbank-Backups; alte Maildienste werden gezielt stillgelegt

```
🌐 Web-Interface (LOKAL):    http://127.0.0.1:8000
👤 Login:                    admin
🔑 Initial-Passwort:         (siehe Ausgabe oder data/.initial-password)
```

Der uvicorn-Bind ist standardmäßig **`127.0.0.1:8000`**. Abweichungen werden
nur root-verwaltet in `/etc/scrapper/web.env` konfiguriert. Damit macht eine
Neuinstallation den Port nicht unbeabsichtigt im LAN erreichbar.

### 3. Cloudflare-Tunnel und eigene Anmeldung

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

#### Anmeldung am Rezeptserver

Web, SwiftUI und Expo verwenden Benutzername und Passwort des Rezeptkontos.
Der Tunnel übernimmt die HTTPS-Erreichbarkeit; eigene Sitzungen und Rollen
bleiben immer erforderlich. Der lesende Gastzugang ist ausdrücklich begrenzt.
Für den Rezeptserver ist keine Cloudflare-Access-Anwendung erforderlich.

Beim Umstieg zuerst einen vorhandenen Administratorzugang prüfen und dann die
Access-Anwendung für den Rezeptserver einschließlich zugehöriger Pfadausnahmen
entfernen. Alte Gerätezugangsdaten werden beim App-Start aus dem Schlüsselbund
gelöscht; bestehende echte Rezept-Sitzungen bleiben erhalten. Abmelden widerruft
die Sitzung und führt zur App-Anmeldung zurück.

Die optionale Verbindung zum eigenständigen Einkaufsdienst kann weiterhin
Cloudflare-Service-Zugangsdaten benötigen. Diese gehören ausschließlich in
`einkauf.cf_access_client_id` und `einkauf.cf_access_client_secret`; sie werden
nicht für die Anmeldung am Rezeptserver verwendet.

### 4. Konfiguration

Im Web-UI → „Einstellungen":
- **OpenAI API-Key** und Modell (Default: `gpt-4o-mini`; optionale Base-URL nur mit erneuter Key-Eingabe änderbar)

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

Plus die `config.yaml` separat sichern (enthält Zugangsdaten, KI-Schlüssel und Webhook-URLs).

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

# Schritt 4: Im Web-UI einloggen und die KI-Verbindung testen
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

ai:
  openai:
    api_key: ""       # API-Key nur in der geschützten Serverkonfiguration
    model: gpt-4o-mini
    base_url: ""      # leer = api.openai.com
    timeout: 30
  confidence_threshold: 0.75
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

## Lokale Prüfungen

`python tools/run_isolated_tests.py -q` führt die Suite mit temporären Datenbank-
und Dateipfaden sowie deaktivierten externen Integrationen aus. Voraussetzung:
`pip install -r requirements-dev.txt` und `python -m playwright install chromium`.
Browserprüfungen verwenden die lokalen Web-Dateien und einen synthetischen
Haushalt; API-Schreibzugriffe bleiben innerhalb des Browser-Fixtures.
`node --test tests/web_async.test.cjs` prüft zusätzlich verzögerte Antworten,
Abbruch und Zeitlimits für JSON/PDF sowie konkurrierende Aktualisierungen von
Wochenplan, Einkaufsliste und Zutatenvorschlägen ohne laufenden Server. Sie
prüfen auch, dass verspätete Bild- und Löschantworten ein inzwischen geöffnetes
anderes Rezept nicht verändern oder schließen.
Nach Upload oder Wiederherstellung ändern sich die Bild-URLs für Detail und
Bibliothek, ohne Dateinamen zu verändern. Der Browser muss Cover vor erneuter
Verwendung validieren; Vorschaubilder werden mit Coverwechseln serialisiert.
Bei unveränderten Bildern antwortet der Server mit `304`, ohne die Bilddaten
erneut zu übertragen.

Die Bildtests nutzen simulierte KI-Antworten und echte lokale Dateien/SQLite:
Sie prüfen Rollbacks bei Speicherfehlern, den Vorrang späterer Uploads oder
Wiederherstellungen sowie fortsetzbare Bestandsläufe. Der Bestandslauf liest
die Rezept-IDs einmal; jeder Teilschritt lädt anschließend nur sein aktuelles
Rezept. Ein überholter Bildauftrag wird übersprungen, ohne einen späteren
Bildstand als erfolgreich generiert oder fehlgeschlagen zu markieren.
Versionsprüfungen simulieren auch Coverfehler nach einer Ordneränderung,
prüfen Kochfortschritt-Rollbacks und verhindern veraltete Thumbnail-Caches.

Für die native App: im Verzeichnis `native-ios` `npm test` und
`npm run typecheck` ausführen. Die Cache-Tests simulieren Speicherzugriffe,
Abbruch und Sitzungswechsel; sie ersetzen keinen Test auf einem iPhone.

## Lizenz / Verantwortung

Self-hosted Setup. Vor produktivem Einsatz: die Sicherheitskonfiguration in `data/config.yaml` prüfen, Initial-Passwort ändern, HTTPS über einen Reverse-Proxy oder Tunnel einrichten und Updates über `proxmox/install.sh` beziehungsweise `proxmox/update-local.sh` einspielen. Die eigene Kontenanmeldung bleibt immer aktiv.

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

Das Skript überträgt Backend und Frontend gemeinsam, bewahrt auf Produktion alle
Laufzeitdaten, startet `scrapper-web` neu und verifiziert anschließend Version,
native Capabilities und die exakten OpenAPI-Methoden des ausgelieferten Vertrags.
Nur auf der isolierten Instanz `rezepte-review` sichert es zusätzlich die rein
künstlichen Review-Daten und hebt Quell-URL, Quell-Snapshots und Demo-Wochenplan
einschließlich des künstlichen Warenkorbs und der wöchentlichen Einkaufsregel
atomar sowie wiederholbar auf den dokumentierten Sollstand an; Konten,
Zugangsdaten und Produkt-Nutzungsstatistiken bleiben dabei unverändert.

Vor dem Austausch verweigert das Skript ältere Datenbankschemas und Releases,
denen bisherige Fähigkeiten fehlen. Es sichert Datenbank, Konfiguration, Code
und installierte Dienste und stellt sie bei einem Fehler gemeinsam wieder her.
Die vorherige Aktivität des Backup-Timers bleibt erhalten; ausgeschaltete
Backup-Timer werden nicht aktiviert. Nach erfolgreichen Updateprüfungen werden
alte Maildienste deaktiviert und ausschließlich ihre bekannten Unit-/Polkit-Dateien
entfernt. Bis zu diesem Punkt stellt ein Rollback auch den früheren Timerzustand wieder her.

Für den Wechsel vom bisherigen Proxy-Einzelbenutzerbetrieb auf Haushalte
das Update mit `sudo ENABLE_HOUSEHOLD_AUTH=1 bash proxmox/update-local.sh`
aufrufen. Dafür muss der konfigurierte Betreiber bereits als aktiver Administrator
existieren. Seine bisherigen Zugangsdaten bleiben erhalten; Benutzer und Kennwörter
werden nicht neu angelegt oder zurückgesetzt. Danach ist eine persönliche Anmeldung
oder der lesende Gastzugang erforderlich.

Ab 1.9.0 bieten Web und Apps eine eigene Kontoseite mit Passwortänderung,
Gerätesitzungen und Kontolöschung sowie eine Benutzerverwaltung für Administratoren.
Apple und Google können als zusätzliche Anmeldewege eingerichtet werden.
Einrichtung, erneute Anmeldung nach dem Upgrade und Grenzen stehen in der
[Kontoverwaltung](docs/account-management.md).

Ab 1.9.1 können normale Konten eigene private Varianten sichtbarer Rezepte
erstellen und manuell bearbeiten. Importe, OCR, KI-Aktionen und Administration
bleiben Administratoren vorbehalten. Einkauf, Favoriten und Wochenplanung bleiben
für angemeldete Nutzer verfügbar.

Ab 1.8.9 ersetzt kein Proxy die eigene Anmeldung. Alte Einstellungen
`web.auth_disabled` und `web.external_logout_url` werden beim Laden ignoriert
und beim Speichern entfernt. Das Update erhält Konten, Kennwörter und den
vorhandenen Sitzungsschlüssel. Details: [CHANGELOG_V1.8.9.md](CHANGELOG_V1.8.9.md).

### PDF-Rezeptdaten

PDF-Rezepte werden nach OCR/Ausrichtung direkt auf Zutaten, Mengen, Einheiten, Schritte und Portionen ausgewertet. Für Bestandsdateien steht die Funktion unter **Admin → PDF & Scan** zur Verfügung. Details: `PDF_RECIPE_EXTRACTION.md`.

### Importgrenzen ab 1.8.5

Request-Bodies sind vor dem Parsing begrenzt: regulär auf 1 MiB, Dateiimporte
auf 25 MiB plus 1 MiB Formular-Overhead und Foto-/Coverimporte auf 10 MiB plus
Overhead. PDF-Vorschaubilder werden mit maximal 1600 Pixeln an der längeren Seite
gerendert. Video-Downloads werden bei bekannten Größen vorab und bei unbekannten
Streams durch eine laufende Überwachung bei 100 MiB abgebrochen. Das ist eine
Abbruchschwelle mit kurzen Messintervallen, keine bytegenaue Speicherreservierung.

Ordnerteile aus KI-Ausgaben und manuellen Eingaben verwenden dieselbe
Pfadbereinigung. HTML-Canonical-Tags dürfen nur URLs des tatsächlich abgerufenen
HTTPS-Origin beanspruchen. Die bestehenden DNS-/SSRF-Prüfungen bleiben aktiv.

Der Status der Sicherheits- und GUI-Befunde steht in [AUDIT.md](AUDIT.md), die
offenen Arbeiten in [FIXPLAN.md](FIXPLAN.md). Der Prüf- und Rolloutnachweis dieser
Runde steht in [AUDIT_FOLLOWUP_1.8.5.md](AUDIT_FOLLOWUP_1.8.5.md). Die dortigen
Mailbefunde sind historisch; der Mailimport wurde mit 1.10.0 entfernt.

### Request- und Archivstabilisierung ab 1.8.6

Anmeldung und Rollenprüfungen verwenden einen Worker und teilen ihren geprüften
Benutzer nur innerhalb desselben Requests. Lang laufende Status-Streams prüfen
einen Sitzungswiderruf weiterhin erneut. Thumbnails verwenden einen privaten
Revalidierungs-Cache: Auch ein `304` setzt eine gültige Sitzung und den richtigen
Haushalt voraus. Warenkorblesen ohne fällige Regel benötigt keine Schreibsperre.

Die Suche ermittelt Kandidaten-IDs aus Volltext, Teilwörtern und Zutaten einmal.
Die Teilwortsuche bleibt ein Scan; Details der vergleichenden Messung mit
2000 Rezepten stehen in [AUDIT_FOLLOWUP_1.8.6.md](AUDIT_FOLLOWUP_1.8.6.md).

### Fachlogik und KI-Belege ab 1.8.7

PDF-Mengen und Suchausschlüsse sind korrigiert. Unbelegte KI-Zutaten und
unvollständige Antworten erhalten den Fehlerstatus zur manuellen Pflege; der
lokale PDF-Quelltext wird erhalten. Alle KI-POST-Versuche unterliegen rollierenden,
serverweiten 24-Stunden-Kontingenten in `ai.openai`: `server_daily_request_limit`
(Standard 1000) und `server_daily_image_limit` (Standard 40). Null sperrt weitere
Versuche; fehlgeschlagene Versuche und Retries zählen mit. Die Kontingente gelten
auch für Hintergrundanalysen und sind keine Dollar-/Eurogrenze.

Automatische Rezeptbilder ersetzen kein vorhandenes Quellcover. Ein expliziter
Bildauftrag oder administrativer Backfill bleibt mit Originalsicherung möglich.
Testläufe isolieren Pfade und externe Integrationen bereits in `tests/conftest.py`;
auch ein direkter `pytest`-Aufruf liest keine Betreiberkonfiguration.
Details: [CHANGELOG_V1.8.7.md](CHANGELOG_V1.8.7.md).

### Anmeldung, Last und Upgrades ab 1.8.8

Formular-POSTs nutzen `strict-origin` in Header und Registrierungs-Meta. Der
Referer enthält damit nur den Ursprung; fremde oder `null` Origins bleiben
abgewiesen. Auch Logout erfordert einen passenden Origin. HTML und native Login
teilen den Zähler pro IP und Benutzername (fünf Fehlversuche); eine zusätzliche
IP-Grenze von 100 Fehlversuchen schützt vor breit gestreuten Versuchen. Direkte
Proxyheader werden ignoriert, konfigurierte Proxyketten von rechts geprüft.

SQLite-Schreibkonkurrenz vor Antwortbeginn liefert 503 mit `Retry-After: 1`.
Rezeptdetail-Datenabfragen verwenden eine gemeinsame schreibgeschützte
Verbindung pro Aufruf. Schema 265 trennt Worker-Abstürze von normalen Retries
und ergänzt fehlende Papierkorb-Quell-URLs aus erhaltenen Original-URLs.
Frische Prozesse beachten den konfigurierten Datenbankpfad; Job-Locks liegen
daneben. Browser-Tests erzwingen den temporären Pfad vor dem Datenbankzugriff.

Details: [CHANGELOG_V1.8.8.md](CHANGELOG_V1.8.8.md).
Vor `db-restore` müssen schreibende Services **und ihre Job-/Backup-Timer**
gestoppt sein. Hinterher nur die vorher aktiven Timer wieder starten.

Der separate Archiver erstellt seine venv an einem dauerhaften Pfad und
verknüpft `/opt/video-archiver/venv` darauf. Installation startet keinen Download
als Abnahme. Eine deaktivierte Timer-Einstellung bleibt bei Updates erhalten;
die Unit erlaubt vier Jobs zu je 900 Sekunden plus fünf Minuten Reserve.
Bei Updates bleiben vorhandene Workerargumente, Grenzwerte und Timerzyklen
erhalten. Das Zeitbudget wird mindestens auf `max_jobs × timeout + 300 s`
angehoben (mindestens 3900 s); nicht auflösbare Variablen lehnen das Update ab.
Der neue Linux-Test unter `tools/probe_video_archiver_install.py` prüft Launcher,
Rollback und SIGTERM ausschließlich mit temporären Testdaten.

Die priorisierte Übergabe einschließlich offener Betreiber- und Geräteprüfungen
steht in [AUDIT_HANDOFF.md](AUDIT_HANDOFF.md). Expo-Kochmodus und VoiceOver-
Korrekturen benötigen einen neuen App-Build und die Abnahme auf dem Gerät.
