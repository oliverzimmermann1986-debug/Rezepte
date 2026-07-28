# Rezepte Redesign Mockups

Status: Richtungsentwurf, noch nicht zur Umsetzung freigegeben.

Alle sichtbaren Rezept- und Einkaufsdaten sind Beispieldaten.

## Design Read

- Modus: Operate
- Redesign: visueller Overhaul, Produktlogik und Informationsarchitektur bleiben erhalten
- Leitidee: Mise-en-place Rail
- Hauptszene: einhändige Nutzung am Handy in der Küche
- Hauptfluss: Rezept finden, kochen, Zutaten übernehmen, gemeinsam einkaufen
- Design Variance: 7
- Motion Intensity: 5
- Visual Density: 5

## Visuelles System

- Akzent: Butter Yellow `#F5C84F`
- Grundflächen: Creme `#FFFAF0` und `#FFFDF8`
- Text: warmes Dunkelbraun `#433427`
- Sekundärfarbe: Graphit für ruhige Bedienelemente
- Typografie: robuste, moderne System-Sans ohne externe Laufzeitabhängigkeit
- Radien: 14 px für Medien und große Flächen, 10 px für Bedienelemente
- Trennung: feine Linien und Abstand statt Kartenstapel
- Bilder: natürliche Food-Fotografie mit festen Ausschnitten

## Motion

- Rezeptkarte zu Detailansicht: Shared-Element-Übergang, 360 ms
- Zutaten in Einkauf übernehmen: gruppierte Bewegung in die Einkaufsleiste, 420 ms
- Einkaufsartikel abhaken: Komprimieren und Ausblenden, 180 ms, mit Rückgängig
- Tabs und Auswahlindikatoren: 180 bis 220 ms
- Bestätigungen: ruhiger Inline-Status statt globaler Animation
- Bei reduzierter Bewegung bleiben alle Zustände sofort und ohne Wegbewegung sichtbar

## Dateien

Aktuelle Varianten mit stärkerem Butter Yellow:

1. `01-desktop-bibliothek-butter-v2.png`
2. `02-desktop-rezept-kochflow-butter-v2.png`
3. `03-desktop-einkauf-wiederkehrend-butter-v2.png`
4. `04-desktop-admin-verwerfen-butter-v2.png`
5. `05-mobile-bibliothek-butter-v2.png`
6. `06-mobile-kochmodus-butter-v2.png`
7. `07-mobile-wiederkehrende-einkaeufe-butter-v2.png`
8. `08-desktop-admin-zutaten.png`
9. `09-mobile-admin-zutaten.png`

Die ursprünglichen, zurückhaltenderen Varianten bleiben zum Vergleich erhalten.

## Abgedeckte Kernzustände

- Rezeptbibliothek und Suche
- Quelle am Rezept
- Zutatenstatus bereit und wird erkannt
- Portionen und Kochmodus
- Rezeptzutaten in die Einkaufsliste übernehmen
- Gruppierte Einkaufsliste
- Wiederkehrende Einkäufe mit Fälligkeit und Bearbeitung
- Fehlgeschlagene Importe erneut versuchen oder dauerhaft verwerfen
- Zutaten in den Stammdaten als `Nicht einkaufen` kennzeichnen
- Bestätigung und Rückgängig
- Lokale und offline verfügbare Zustände

## Regel für nicht einzukaufende Zutaten

- Die Eigenschaft wird zentral an der kanonischen Zutat gepflegt.
- Beispiele sind Salz, Pfeffer und Wasser.
- Die Zutat bleibt im Rezept, in der Suche und im Kochmodus sichtbar.
- Beim Übernehmen von Rezeptzutaten in die Einkaufsliste wird sie automatisch ausgelassen.
- Die Eigenschaft ist auf Desktop und Mobile im Admin-Bereich bearbeitbar.

## Nicht Bestandteil des Redesigns

- Preisschätzung
- Notizen an Einkaufslisten oder Artikeln
- Einkaufsliste teilen
