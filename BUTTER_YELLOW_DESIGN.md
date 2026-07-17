# Rezepte – Butter-Yellow Design

## Umsetzung

- `app/static/rezepte.css` ist das einzige App-Stylesheet.
- Alte Dark-/Light-/Ocean-/Forest-/Lavender-Themes und externe Google Fonts wurden entfernt.
- Die Rezeptbibliothek ist die Startseite. Favoriten sind als eigener Navigationspunkt verfügbar.
- Die erweiterte Filterung öffnet sich am Desktop als rechtes Side-Sheet und mobil als Bottom-Sheet.
- Die mobile Bottom-Navigation besitzt eine feste, vollständig deckende Fläche. Der Inhaltsbereich reserviert ihre komplette Höhe plus Safe Area; kein Rezept liegt darunter.
- Touch-Ziele sind mindestens 40–44 Pixel groß; Formfelder nutzen mobil 16 Pixel gegen iOS-Autozoom.

## Farbwerte

- Butter Yellow: `#F5C84F`
- Creme: `#FFFAF0` / `#FFFDF8`
- Warmes Dunkelbraun: `#433427`
- Kartenrahmen: `#EADBBC`

## Navigation

Desktop: Rezepte, Favoriten, Einkaufsliste, Prüfen, Import prüfen, Jobs & Logs, Stammdaten, Einstellungen, Papierkorb.

Mobil: Rezepte, Favoriten, Einkaufsliste, Prüfen und Mehr. Die übrigen Bereiche liegen im Mehr-Bottom-Sheet.
