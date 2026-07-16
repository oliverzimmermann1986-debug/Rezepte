# PDF- und Scan-Verarbeitung

## Automatische Importpipeline

PDF-Anhänge können vor der Rezeptanalyse lokal aufbereitet werden:

1. Seitenausrichtung über Textschreibrichtung oder Tesseract OSD
2. Erkennung und optionale Entfernung leerer Seiten
3. konservativer Beschnitt großer weißer Ränder
4. optionales Begradigen reiner Scan-Seiten
5. optionale Kontrastverbesserung
6. optionaler OCR-Textlayer für durchsuchbare Scans
7. Integritätsprüfung und atomarer Austausch

Text- und Vektor-PDFs werden nicht unnötig gerastert. Rasteroperationen gelten ausschließlich für Seiten, die als Scan erkannt wurden.

## Manueller Seiteneditor

Im Admin-Reiter **PDF & Scan** kann ein Rezept-PDF geöffnet werden. Pro Seite sind möglich:

- Vorschau
- Verschieben
- Drehung in 90°-Schritten
- Löschen

Mindestens eine Seite muss erhalten bleiben. Nach dem Speichern wird eine abgeleitete PDF-Vorschau erneuert. Ein vorhandenes, benutzerdefiniertes Thumbnail wird nicht überschrieben.

## Sicherheit und Originale

- verschlüsselte PDFs bleiben unverändert
- digital signierte PDFs bleiben unverändert
- Symlinks werden nicht als Rezept-PDF verarbeitet
- Pfade müssen unterhalb des konfigurierten Rezeptstamms liegen
- Änderungen werden atomar geschrieben
- bei aktivem `keep_original` wird das Original vorher unter `data/pdf-originals/` gesichert
- ein Fehler lässt das Ausgangsdokument unangetastet

## Konfiguration

```yaml
pdf:
  auto_rotate: true
  use_tesseract_osd: true
  remove_blank_pages: true
  auto_crop: true
  deskew_scans: false
  ocr_scans: true
  improve_contrast: false
  ocr_language: deu+eng
  keep_original: true
  min_text_chars: 20
  text_dominance: 0.65
  osd_min_confidence: 3.0
  max_osd_pages: 12
```

Für Scan-Erkennung und OCR installiert das Proxmox-Skript:

```text
tesseract-ocr
tesseract-ocr-osd
tesseract-ocr-deu
tesseract-ocr-eng
```

Auf bestehenden Installationen:

```bash
apt-get update
apt-get install -y tesseract-ocr tesseract-ocr-osd tesseract-ocr-deu tesseract-ocr-eng
```

## Bereits vorhandene PDFs

Die erweiterte Stapelverarbeitung wird im Admin-Reiter gestartet. Der bestehende CLI-Befehl bleibt für reine automatische Ausrichtung erhalten:

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-auto-rotate
```

## Verbesserungen in 1.2.1

- OSD-Erkennung mit niedrigerer, praxisgerechter Mindestschwelle.
- Fallback über einen lokalen Vierfach-OCR-Vergleich, wenn OSD bei kurzen oder bildlastigen Rezeptseiten keine Entscheidung trifft.
- Alle Seiten eines PDFs können geprüft werden; die bisherige Standardgrenze von 12 Seiten wurde auf 100 erhöht.
- Scan-Aufbereitung standardmäßig in 300 DPI.
- Automatische Weißpunkt-/Kontrastkorrektur und vorsichtiges Nachschärfen.
- Begradigung schiefer Scan-Seiten standardmäßig aktiv.
- „Nur analysieren“ verwendet exakt dieselbe Verarbeitung wie „Aufbereiten“, schreibt aber keine Datei.
- Der Ergebnisbericht zeigt Erkennungsmethode, alte und neue Rotation sowie unsichere Seiten.

### Gesamten Bestand aufbereiten

Im Admin Center: **PDF & Scan -> Bestand jetzt aufbereiten**. Die Rezept-ID bleibt leer.

Alternativ per Konsole:

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-optimize
```

Vor jeder Änderung wird das Original unter dem Datenverzeichnis in `pdf-originals` gesichert.

## Hintergrundläufe und Fehlerdiagnose (v1.2.3)

Bestandsläufe werden vom Web-UI als Hintergrundjob gestartet. Ein Reverse-Proxy-, Cloudflare- oder Browser-Timeout beendet den PDF-Worker nicht. Beim erneuten Öffnen von **Admin → PDF & Scan** verbindet sich die Oberfläche wieder mit dem aktiven Lauf.

Vor jedem Lauf erscheint eine Systemprüfung für:

- PyMuPDF und Pillow
- Tesseract sowie installierte OCR-Sprachen
- Lesbarkeit und Schreibbarkeit des Rezeptverzeichnisses
- Schreibbarkeit des Original-Backupverzeichnisses
- freien Speicherplatz

Auf dem Server kann dieselbe Prüfung ausgeführt werden:

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-doctor
```

Ausführliche Servermeldungen:

```bash
journalctl -u scrapper-web -n 200 --no-pager
```

Sehr große Seiten werden automatisch mit einer reduzierten, aber weiterhin OCR-tauglichen DPI gerendert. Dadurch werden Speicherabbrüche bei A2-/A1-Scans vermieden.
