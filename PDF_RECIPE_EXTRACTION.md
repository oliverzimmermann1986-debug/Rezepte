# Zutaten und Rezeptdaten aus PDF

## Automatischer Import

Bei einem neuen PDF-Mailanhang läuft die Verarbeitung in dieser Reihenfolge:

1. Ausrichtung und Scanverbesserung
2. OCR-Textlayer bei gescannten Seiten
3. Textauslesung aus allen PDF-Seiten
4. lokale Erkennung klassischer Zutatenlisten
5. optionale KI-Auswertung für komplexe Layouts
6. Übernahme von Zutaten, Mengen, Einheiten, Schritten, Portionen und Tags
7. direkte Aufnahme in die Rezeptdatenbank

Der lokale Parser erkennt typische Abschnitte wie `Zutaten`, `Ingredients`, `Du brauchst` und beendet die Liste bei `Zubereitung` oder `Anleitung`.

## Bestehende PDFs

Unter **Admin → PDF & Scan**:

- `Zutaten, Schritte und Portionen aus dem PDF lesen` aktivieren
- zunächst `Bestand analysieren`
- Vorschau und erkannte Zutaten kontrollieren
- anschließend `Bestand jetzt aufbereiten`

Standardmäßig werden nur fehlende Rezeptdaten ergänzt. Die Option **Bereits gepflegte Rezeptdaten überschreiben** sollte nur bewusst aktiviert werden. Vor einem Überschreiben wird automatisch eine Rezeptversion angelegt.

## Erkennung ohne OpenAI

Ohne verfügbaren OpenAI-Schlüssel werden Mengenlisten lokal erkannt, zum Beispiel:

```text
Zutaten:
- 500 g Kartoffeln
- 1,5 l Gemüsebrühe
- 2 Stück Zwiebeln
- Salz nach Geschmack
```

Komplexe Tabellen, Fließtexte, Schritte und Portionen werden mit OpenAI zuverlässiger erkannt. Ein Ausfall der KI verhindert die lokale Zutatenauslesung nicht.

## Sicherheit

- Original-PDFs bleiben bei aktivierter Originalsicherung erhalten.
- Bestehende manuelle Daten werden standardmäßig nicht überschrieben.
- Überschreibungen erzeugen eine Version für Rückgängig/Wiederherstellung.
- PDFs außerhalb des Rezeptstamms werden nicht verarbeitet.
