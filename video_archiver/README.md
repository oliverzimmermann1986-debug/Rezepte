# Privater Video-Archiver

Dieser Worker ist absichtlich von iPhone-App und Rezepte-Backend getrennt. Er
besitzt eine eigene SQLite-Queue, eigene Cookies und ein eigenes Archiv. Weder
Videos noch Archivstatus werden durch die Rezepte-API ausgeliefert.

Nur eigene Inhalte oder Inhalte mit ausdrücklicher Archivierungsberechtigung
dürfen verarbeitet werden. Plattformbedingungen und Urheberrechte gelten auch
dann, wenn eine Datei nicht in der App angezeigt wird.

## Benutzung

`yt-dlp` und `ffmpeg` müssen auf dem Rechner des Workers installiert sein.

```powershell
python -m video_archiver --queue D:\Privat\video-queue.db enqueue `
  --id 35852573 `
  --url "https://www.tiktok.com/@beispiel/video/123456"

python -m video_archiver --queue D:\Privat\video-queue.db run `
  --archive D:\Privat\video-archiv `
  --yt-dlp C:\Tools\yt-dlp.exe `
  --max-size-mb 1000 `
  --confirm-rights
```

Der `run`-Befehl verarbeitet genau einen Auftrag und eignet sich deshalb für
Windows Aufgabenplanung oder einen systemd-Timer. Wiederholtes Ausführen ist
idempotent. Ein vorhandenes Video wird nur akzeptiert, wenn ID, Link und
SHA-256-Prüfsumme zur Sidecar-Datei passen; fremde Dateien werden niemals
überschrieben.

Ergebnis:

```text
D:\Privat\video-archiv\
├── 35852573.mp4
└── 35852573.json
```

Queue-Zustand prüfen:

```powershell
python -m video_archiver --queue D:\Privat\video-queue.db status
python -m video_archiver --queue D:\Privat\video-queue.db events --limit 20
```

Die Queue und das Archiv gehören nicht in Git, nicht in den App-Build und nicht
unter einen öffentlich erreichbaren Web-Pfad. Cookies dürfen ausschließlich auf
dem Worker liegen.

## Installation auf dem Rezept-Host

Das Installationsskript legt einen eigenen Systembenutzer, private Datenpfade
und einen gehärteten Fünf-Minuten-Timer an:

```bash
sudo bash video_archiver/install-host.sh
systemctl status video-archiver.timer
```

Vor jedem Timer-Lauf übernimmt der Worker neue TikTok-/Instagram-Links
schreibgeschützt aus `/opt/scrapper/data/scrapper.db` in seine eigene Queue.
Die App und die Rezepte-API erhalten dadurch weiterhin keinen Zugriff auf
Archiv, Cookies oder Archivstatus. Bereits bekannte Links werden nicht erneut
eingeplant.

Ein einzelner Auftrag kann zusätzlich manuell eingetragen werden:

```bash
cd /opt/video-archiver
sudo -u videoarchive ./venv/bin/python -m video_archiver \
  --queue /var/lib/video-archiver/queue.db enqueue \
  --id 35852573 \
  --url 'https://www.tiktok.com/@beispiel/video/123456'
```
