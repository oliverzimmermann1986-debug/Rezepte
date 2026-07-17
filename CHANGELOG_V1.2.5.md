# Rezepte v1.2.5

## Rezeptdaten direkt aus PDF

- PDF-Mailanhänge werden nach Drehung, Scanverbesserung und OCR unmittelbar auf Zutaten, Mengen, Einheiten, Zubereitungsschritte, Portionen und Tags ausgewertet.
- Klassische Zutatenlisten funktionieren mit einem lokalen Parser auch ohne OpenAI.
- Bei konfiguriertem OpenAI werden komplexe Tabellen, Fließtext, mehrspaltige Layouts und Arbeitsschritte zusätzlich strukturiert erkannt.
- Neue PDF-Rezepte werden sofort in der Rezeptdatenbank angelegt; die Zutaten müssen nicht mehr auf den späteren Hintergrundindex warten.
- Im Admin Center kann der gesamte PDF-Bestand nachträglich ausgelesen werden.
- Bestehende Zutaten und Schritte werden standardmäßig nicht überschrieben.
- Optionales Überschreiben erzeugt vorher automatisch eine wiederherstellbare Rezeptversion.
- Dry-Run zeigt pro PDF Zutatenvorschau, Anzahl der Schritte, Portionen und Erkennungsmethode.
- Ergebnisübersicht zählt erkannte Zutaten, Schritte und aktualisierte Rezepte.

## Fehlerkorrektur

- Die Ablage von PDF-Originalen bei Mailanhängen akzeptiert nun den vorgesehenen Parameter; der bisher mögliche `unexpected keyword argument original_pdf_data`-Fehler ist behoben.
- PWA-Cache und Backend-Kompatibilitätskennung wurden auf v1.2.5 angehoben.
