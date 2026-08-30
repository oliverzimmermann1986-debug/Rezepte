# Rezepte Design System

## Leitidee

Rezepte ist eine warme, robuste Küchenwerkzeugtafel für einen privaten Haushalt.
Warmes Pflaume markiert aktive Arbeitsbereiche und wichtige Aktionen. Cremeflächen
halten die Oberfläche ruhig, während dunkle Serifentitel den Rezeptcharakter
tragen. Die Anwendung soll auch mit einer Hand und mitten im Kochvorgang klar
bedienbar bleiben.

## Farben

- `--brand`: Pflaume für Primäraktionen, aktive Navigation und Fokus.
- `--bg`: warmes Creme als Seitenhintergrund.
- `--surface`: helle Arbeitsflächen und Karten.
- `--ink`: dunkles Braun für Text und Icons.
- `--muted`: gedämpftes Braun für Hilfstext.
- Grün steht für bereit oder erfolgreich, Rot nur für Fehler und destruktive
  Aktionen.

Text und interaktive Zustände müssen mindestens WCAG AA erreichen. Farbe ist nie
der einzige Zustandsträger.

## Typografie

- Serifentitel geben Rezepten und Bereichen eine eigenständige Stimme.
- System-Sans-Serif bleibt für Navigation, Formulare, Tabellen und Statuswerte.
- Überschriften sind kompakt, Fließtext bleibt kurz und gut scannbar.
- Formulare verwenden auf Mobilgeräten mindestens 16 px Schriftgröße.

## Layout

- Desktop: feste Seitenleiste, breite Arbeitsfläche, maximal gut lesbare
  Inhaltsbreite.
- Mobil: App-Bar oben, Daumennavigation unten, sichere Abstände für Geräte-Ränder.
- Karten nutzen feine Konturen, kleine Schatten und großzügige Innenabstände.
- Rezeptsuche und aktuelle Hauptaktion stehen im ersten sichtbaren Bereich.

## Komponenten und Zustände

- Primärbuttons sind Pflaume mit heller Beschriftung und haben eine klare Text- oder Icon-Beschriftung.
- Sekundärbuttons bleiben hell mit sichtbarer Kontur.
- Touch-Ziele sind mindestens 44 x 44 px groß.
- Filter öffnen auf kleinen Bildschirmen als Sheet.
- Lade-, Leer-, Fehler- und Erfolgzustände behalten dieselbe visuelle Hierarchie.
- Zutaten können in den Stammdaten als „Nicht einkaufen“ markiert werden.

## Bewegung

- Bewegung erklärt Zustandswechsel und bleibt kurz.
- Hover- und Press-Zustände nutzen kleine Translationen und Farbwechsel.
- Sheets und Dialoge dürfen weich einblenden; Dauer ungefähr 160 bis 240 ms.
- `prefers-reduced-motion` reduziert nicht notwendige Übergänge.

## Produktgrenzen

Keine Preisschätzung, keine Notizen und keine Funktion zum Teilen von Listen.
Der Einkaufsflow umfasst die aktuelle Liste, wiederkehrende Einkäufe und die
Anbindung an den bestehenden Einkauf-Dienst. Der Admin-Bereich bleibt privat.
