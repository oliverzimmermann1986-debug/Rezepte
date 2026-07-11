# Butter-Yellow Designsystem

## Zielbild

Die Anwendung ist als helle, warme Rezeptbibliothek gestaltet. Rezepte stehen visuell vor Import- und Verwaltungsfunktionen. Das Design verwendet keine externen Fonts oder CDNs und bleibt damit im lokalen Netz vollständig nutzbar.

## Gestaltung

- **Butter Yellow:** `#F5C84F` für primäre Aktionen und aktive Navigation
- **Creme:** `#FFFAF0` und `#FFFDF8` für Seiten und Karten
- **Text:** warmes Dunkelbraun statt hartem Schwarz
- **Überschriften:** lokale Serifenschrift für den Rezeptcharakter
- **Formen:** großzügige Rundungen und sehr dezente warme Schatten
- **Statusfarben:** Grün, Rot und Orange bleiben semantisch unterscheidbar

## Responsive Verhalten

- feste, Safe-Area-fähige Kopf- und Bottom-Navigation auf Smartphones
- Rezeptkarten als kompakte Bild-/Text-Zeilen auf kleinen Displays
- drei Rezeptspalten am Desktop, zwei auf mittleren Displays
- erweiterte Filter auf Smartphones standardmäßig eingeklappt
- Filter werden über den gelben Schieberegler-Button geöffnet
- Touch-Ziele bleiben mindestens 44 Pixel groß
- Eingaben verwenden mobil 16 Pixel Schriftgröße gegen iOS-Autozoom

## Technische Struktur

Die Oberfläche besitzt nur noch **eine** Stylesheet-Datei: `app/static/rezeptliebe.css`. Die früheren Dateien `style.css`, `mobile-first.css`, `recipe-focus.css` und `butter-yellow.css` wurden entfernt. Damit gibt es keine alte dunkle Designschicht und keine Kaskade aus nachträglichen Überschreibungen mehr. Rezeptseite, Import, Prüfung, Historie, Einstellungen, Modale und Login verwenden dieselben Butter-Yellow-Komponenten.

Die mobile Bottom-Navigation liegt bündig am unteren Bildschirmrand, ist vollständig deckend und reserviert im Inhaltsbereich einen eigenen Abstand. Das letzte Rezept endet deshalb oberhalb der Navigation und wird nicht von ihr verdeckt.
