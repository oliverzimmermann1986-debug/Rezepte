# PDF-Verarbeitung – Fehlerbehebung v1.2.3

## Behobene Hauptursache

Der bisherige Bestandslauf wurde vollständig innerhalb der HTTP-Anfrage ausgeführt. OCR und 300-DPI-Aufbereitung vieler PDFs konnten länger dauern als das Timeout von Browser, Reverse-Proxy oder Cloudflare. Die Oberfläche meldete dann nur „PDF-Verarbeitung fehlgeschlagen“, obwohl der Serverprozess teilweise noch lief.

Ab v1.2.3 wird der Lauf als serverseitiger Hintergrundjob ausgeführt. Fortschritt und Ergebnis werden in der SQLite-Datenbank gespeichert und beim erneuten Öffnen des PDF-Reiters wieder angezeigt.

## Systemprüfung

Vor dem Start prüft die Anwendung:

- PyMuPDF
- Pillow
- Tesseract und OCR-Sprachen
- Rezeptverzeichnis
- Schreibrechte
- Original-Backupverzeichnis
- freien Speicher

Konsole:

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-doctor
```

Logs:

```bash
journalctl -u scrapper-web -n 200 --no-pager
```

## Update

```bash
cd /opt/scrapper
/opt/scrapper/venv/bin/pip install -r requirements.txt
cp systemd/scrapper-web.service /etc/systemd/system/
cp systemd/scrapper-job.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart scrapper-web
```

Danach die installierte PWA einmal vollständig schließen und neu öffnen.

## `Not Found` im PDF-Reiter (v1.2.4)

Ein 404 bei `/api/admin/pdf/preflight` bedeutet, dass die neuen statischen Dateien
bereits auf Disk liegen, der laufende Uvicorn-Prozess aber noch die alten Router im
Speicher hat. Ein einfacher Neustart behebt den Zustand meist sofort:

```bash
systemctl restart scrapper-web
```

Für lokal entpackte ZIP-Releases sollte das Update künftig ausschließlich mit dem
mitgelieferten Updater erfolgen:

```bash
cd /pfad/zum/entpackten/Release
sudo bash proxmox/update-local.sh
```

Der Updater führt bewusst kein `git pull` aus, bewahrt `data/`, `files/`, `logs/`,
`temp/` und `venv/` und prüft nach dem Neustart sowohl die gemeldete Version als auch
die PDF-API.
