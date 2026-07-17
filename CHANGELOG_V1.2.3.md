# Rezepte v1.2.3

## PDF-Verarbeitung stabilisiert

- PDF-Bestandsläufe laufen als serverseitige Hintergrundjobs und sind nicht mehr an die Dauer einer HTTP-Anfrage gebunden.
- Fortschritt, aktuelle Datei und Ergebnis werden in `maintenance_runs` gespeichert.
- Ein erneutes Öffnen des PDF-Adminbereichs verbindet sich wieder mit einem laufenden Job.
- Es kann nur ein rechenintensiver PDF-Bestandslauf gleichzeitig laufen.
- Vor dem Start werden PyMuPDF, Pillow, Tesseract-Sprachen, Rezeptpfad, Schreibrechte, Backup-Verzeichnis und freier Speicher geprüft.
- Das Frontend zeigt den tatsächlichen Server- oder Dateifehler statt nur „PDF-Verarbeitung fehlgeschlagen“.
- Sehr große Scan-Seiten erhalten automatisch eine speichersichere DPI-Begrenzung, damit der Webdienst nicht durch den OOM-Killer beendet wird.
- OCR-Prozesse werden über `OMP_THREAD_LIMIT=1` auf kontrollierten Ressourcenverbrauch begrenzt.
- Neuer Diagnosebefehl: `python -m app.cli pdf-doctor`.
- Installer-Secrets werden ohne die unter `set -o pipefail` fehlerhafte `tr | head`-Pipeline erzeugt.

## Kompatibilität

Der bisherige synchrone API-Modus bleibt für Einzeltests und Integrationen erhalten. Das Web-UI verwendet standardmäßig den neuen Hintergrundmodus.
