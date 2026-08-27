# Rezepte 1.5.7

## Behoben

- „Nochmals mit KI prüfen“ erkennt nun auch ältere TikTok- und Instagram-
  Prüfeinträge ohne `source`-Merkmal als externe Links. Diese Einträge werden
  wieder über Caption, Cover, Videoframes und Audio analysiert, statt sofort am
  nicht mehr vorhandenen lokalen Video zu scheitern.
- Fehler der KI-Prüfung erscheinen in der iPhone-App sofort als sichtbarer
  Dialog. Sie gehen nicht mehr unbemerkt am Ende eines langen Formulars unter.
- Beim ersten erneuten Prüfen wird der fehlende Link-Marker im Bestand
  nachgetragen, sodass weitere Versuche stabil den aktuellen Analysepfad nutzen.
