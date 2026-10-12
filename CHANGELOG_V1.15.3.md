# 1.15.3

- In „Einstellungen → Mein Haushalt“ lassen sich 1 bis 24 Standard-Portionen dauerhaft für alle Haushaltsmitglieder speichern. Bestehende Haushalte beginnen mit zwei Portionen; Mitglieder können die gemeinsame Vorgabe ändern.
- Neue Wochenvorschläge, zusätzliche Mahlzeiten, Einzelplanungen, Wünsche, Rezept-Einkäufe und frische Kochvorgänge verwenden die Haushaltsvorgabe. Eine eigene Portionszahl pro Gericht, gespeicherte Wochenprofile, bereits geplante Mahlzeiten und laufender Kochfortschritt behalten ihre Auswahl.
- Die Vorgabe wird bei Sitzungsprüfung und Haushaltwechsel geladen und im kontogebundenen Offline-Sitzungssnapshot gespeichert. Abmeldung und Kontowechsel entfernen die vorherige Vorgabe.
- Web-Kontoeinstellungen bieten dieselbe Vorgabe. Neue Planungen und Rezeptmengen nutzen sie; ein Rezeptwechsel überschreibt keine schon gewählten Portionen. Nährwerte pro Portion werden beim Skalieren der Gesamtmenge nicht vervielfacht.
- Schema 274 ergänzt default_servings am Haushalt. Die Migration setzt nur die neue Spalte, erhält vorhandene Daten und wiederholt sich beim Neustart nicht. Einladungsbeitritt verwendet die Vorgabe des Zielhaushalts.
- API: PATCH /api/account/preferences erlaubt nur die eigene Standard-Portionszahl. Fehlende Portionsangaben beim Planen verwenden die Vorgabe atomar; eine Wiederholung erhält die bereits eingeplante Portionszahl.
