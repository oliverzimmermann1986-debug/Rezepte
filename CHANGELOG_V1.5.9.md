# Rezepte 1.5.9

## Behoben

- TikTok-Kurzlinks werden beim Import auf die eindeutige Beitrags-URL
  aufgelöst. Unterschiedliche Kurzlinks zum selben Video erzeugen dadurch
  kein zweites Rezept mehr.
- Bereits importierte Kurzlink-Rezepte werden konsistent in Datenbank und
  `info.json` auf die Beitrags-URL migriert.

## Neu

- Administratoren können ein Rezept direkt über das Papierkorb-Symbol auf der
  Rezeptkarte löschen. Eine Bestätigung schützt vor Fehlbedienung; das Rezept
  bleibt 30 Tage lang im Admin-Papierkorb wiederherstellbar.
