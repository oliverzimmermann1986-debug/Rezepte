# 1.15.1

- iOS nennt fehlende Zutaten und Zubereitungsschritte im Archiv und erklärt den Zustand im Rezept. Vollständige Rezepte mit einem nicht bestätigten Analysestatus bekommen einen Prüfhinweis ohne erfundene Fehlerursache.
- Wochenvorgaben in iOS sind Mindestzahlen; zusätzliche passende Hauptgerichte sind erlaubt. Eine ausdrücklich gewählte Null bleibt ein Ausschluss, auch beim Austauschen.
- Optional pro Tag: Frühstück, Vorspeise und Nachspeise mit eigenen Vorschlägen und Austausch. Hauptgerichte und zusätzliche Mahlzeiten werden separat geprüft.
- Globale Suche, Haushaltsfilter, Vorwochen, Vorlieben, Zutaten- und Zeitgrenzen gelten auch für zusätzliche Mahlzeiten. Ohne passendes Rezept bleibt eine gewünschte Mahlzeit offen.
- Wochenprofile speichern die optionalen Mahlzeiten; ältere Profile bleiben kompatibel. PDF-Export benennt die Mahlzeiten und prüft die Auswahl gegen den aktuellen Serverbestand.
- Bestehende API-Clients behalten die exakten Quoten, bis sie ausdrücklich quota_mode=minimum senden. Keine Datenbankmigration.
