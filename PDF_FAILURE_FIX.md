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
