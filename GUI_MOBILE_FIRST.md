# GUI – Recipe First und Mobile First

## Leitentscheidung

Die Anwendung öffnet auf `/#recipes`. Die Rezeptsuche ist der primäre Arbeitsbereich; Import, Prüfung, Historie und Einstellungen sind unterstützende Funktionen.

## Rezeptseite

- prominente Volltextsuche mit 16-Pixel-Eingabe auf iOS
- Filter nach Typ und Kategorie
- Sortierung nach Datum, Name oder Typ
- horizontale Schnellfilter auf kleinen Displays
- einspaltige kompakte Rezeptkarten auf Smartphones
- mehrspaltiges Raster auf Tablet und Desktop
- Medienkennzeichnung für Video, Bild und PDF
- Detaildialog als mobile Vollbreitenansicht beziehungsweise Desktop-Modal
- schrittweises Nachladen großer Sammlungen

## Mobile Navigation

Die fünf Hauptbereiche liegen in einer festen Bottom-Navigation. Sie sitzt bündig am unteren Viewport-Rand, verwendet einen vollständig deckenden Hintergrund und besitzt einen reservierten Inhaltsabstand. Dadurch kann das letzte Rezept vollständig oberhalb der Navigation angezeigt werden. Kopf- und Navigationsleiste berücksichtigen iPhone-Safe-Areas. Touch-Ziele sind mindestens 44 Pixel hoch. Es gibt keinen horizontalen Seitenüberlauf.

## Unterstützende Seiten

- **Import:** ein klarer Hauptbutton, Laufstatus, Zeitplan und letzte Protokolle
- **Prüfen:** Medienvorschau, KI-Vorschlag und große Speichern-/Überspringen-Aktionen
- **Historie:** such- und filterbare Tabelle, mobil als beschriftete Karten
- **Einstellungen:** auf kleinen Displays einklappbare Bereiche und feste Speicherleiste

## Barrierefreiheit

- semantische Navigation und Seitenüberschriften
- sichtbarer Tastaturfokus
- zugängliche Namen für Icon-Schaltflächen
- Escape schließt den obersten Dialog
- reduzierte Animationen werden berücksichtigt
- Tabellenzellen erhalten mobil ihre Spaltenbezeichnung

## Technischer Aufbau

- `app/static/index.html`: Seitenstruktur
- `app/static/app.js`: Alpine-State, API-Aufrufe und Navigation
- `app/static/rezeptliebe.css`: einziges Designsystem für App-Shell, Rezeptsuche, Karten, Formulare, Dialoge und Responsive-Regeln
- `app/routes/api_recipes.py`: Suche, Details und sichere Medienausgabe

Die Oberfläche nutzt absichtlich keinen Service Worker. Administrative Zustände und private Medien werden deshalb nicht unkontrolliert offline zwischengespeichert.

## Butter-Yellow Design (v1.3.1)

Die Oberfläche verwendet app-weit das bestätigte Butter-Yellow-System: warme Cremeflächen, gelbe Akzente, serifengeprägte Rezeptüberschriften, helle Karten und eine konsistente responsive Navigation. Alte Stylesheet-Schichten wurden vollständig entfernt. `app/static/rezeptliebe.css` ist die einzige visuelle Quelle für Rezeptseite, Import, Prüfung, Historie, Einstellungen, Modale und Login.
