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
