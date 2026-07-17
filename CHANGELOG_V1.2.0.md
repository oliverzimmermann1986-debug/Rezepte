# Rezepte 1.2.0

## Neu

- zentraler, rollenbasierter Admin-Reiter
- Rezept-Snapshots mit Vergleich und atomarer Wiederherstellung
- zentrale Importübersicht
- intelligente Suche mit Synonymen, Ausschlüssen und Tippfehlerhinweisen
- erweiterte PDF-/Scan-Aufbereitung
- manueller PDF-Seiteneditor
- protokollierte Wartungsaktionen
- Medien- und Pfadintegritätsprüfung
- verifiziertes Testbackup mit automatischer Aufbewahrungsgrenze

## Technische Verbesserungen

- neue, getrennte Module für Admin-API, Suche und PDF-Verarbeitung
- Batch-Abfrage von Zutaten statt N+1-Zugriffen bei der Relevanzsortierung
- atomare Datenbanktransaktionen für Restore-Vorgänge
- versionierte Schema-Migrationen
- explizite Pillow-Abhängigkeit
- zusätzliche API-, Datenbank-, PDF-, OCR-, Such- und UI-Regressionstests
