# Rezeptliebe 1.2.1

## Admin-Zugang

- Direktrouten `/admin` und `/admin/pdf`.
- Mobiler Admin-Button in der Kopfzeile.
- Sichtbare Anzeige von Benutzername und Rolle.
- CLI-Befehle `user-list` und `user-role`.
- Verständliche Meldung bei fehlender Admin-Rolle.

## PDF-Rotation und Qualität

- Robuster OSD-Lauf für Scan-Seiten.
- Vierfach-OCR-Voting als Fallback für kurze oder bildlastige PDFs.
- Standardmäßig bis zu 100 Scan-Seiten je PDF statt 12.
- 300-DPI-Qualitätsprofil mit Kontrastkorrektur und Nachschärfen.
- Deskew, OCR, Kontrast und Schärfung standardmäßig aktiviert.
- Dry-Run verwendet nun dieselbe Rotations- und Qualitätsanalyse wie der echte Lauf.
- Verarbeitungsbericht enthält Methode und Rotationsentscheidung pro Seite.
- Neuer Bestandsbefehl `python -m app.cli pdf-optimize`.
