# Rezepte 1.6.0

## Neu

- Die Einkaufsliste bietet Mengen und Einheiten, passende Produkt-Icons,
  Autovervollständigung aus den vorhandenen Rezeptzutaten sowie verlässlichere
  Supermarkt-Kategorien.
- Der Produktkatalog wird aus allen aktiven Rezepten aufgebaut und priorisiert
  häufig verwendete Zutaten in den Vorschlägen.
- Neue Rezeptanalysen erhalten die bereits verwendeten Zutatennamen und nutzen
  vorhandene Schreibweisen zuerst, ohne echte Sorten oder Zubereitungsformen
  zusammenzufassen.

## Verbessert

- Vorhandene Zutaten werden einmalig bereinigt: Leerzeichen, kanonische Namen
  und Einheiten werden normalisiert, aussagekräftige Bezeichnungen bleiben
  erhalten.
- Die iOS-App erkennt unerwartete HTML- und Cloudflare-Antworten und zeigt
  dadurch eine verständlichere Verbindungsfehlermeldung.

## Datensicherheit

- Vor der Datenbankmigration wird automatisch eine wiederherstellbare
  SQLite-Sicherung angelegt.
